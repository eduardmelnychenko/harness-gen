from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage

from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError
from harness_gen.interview_assistance import (
    InterviewAssistant,
    TopicInterviewTurn,
    validate_suggested_questions,
)
from harness_gen.logging_config import configure_logging
from harness_gen.logging_events import web_logging_scope


class FakeRunnable:
    def __init__(self, output: object, model: FakeModel | None = None) -> None:
        self.output = output
        self.model = model

    def invoke(self, messages: object) -> object:
        if self.model is not None:
            self.model.messages = messages
        return self.output


class FakeModel:
    def __init__(self, structured_output: object, coaching_output: object) -> None:
        self.structured_output = structured_output
        self.coaching_output = coaching_output
        self.messages: object = None

    def with_structured_output(self, _schema: type[object]) -> FakeRunnable:
        return FakeRunnable(self.structured_output, self)

    def invoke(self, messages: object) -> object:
        self.messages = messages
        return self.coaching_output


class SequencedRunnable:
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = outputs
        self.calls: list[object] = []

    def invoke(self, messages: object) -> object:
        self.calls.append(messages)
        return self.outputs.pop(0)


class SequencedModel:
    def __init__(self, outputs: list[object]) -> None:
        self.runnable = SequencedRunnable(outputs)

    def with_structured_output(self, _schema: type[object]) -> SequencedRunnable:
        return self.runnable


class InterviewAssistanceValidationTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness_gen")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_accepts_unique_optional_questions(self) -> None:
        self.assertEqual(
            validate_suggested_questions(
                ("Which API versions must remain compatible?",),
                ("What is the main purpose of the harness?",),
            ),
            ("Which API versions must remain compatible?",),
        )

    def test_rejects_duplicate_and_deterministic_questions(self) -> None:
        cases = (
            (
                ("Which users are affected?", "Which users are affected?"),
                ("What is the main purpose of the harness?",),
            ),
            (
                ("What is the main purpose of the harness?",),
                ("What is the main purpose of the harness?",),
            ),
            (
                ("Which users are affected?",) * 4,
                (),
            ),
            (
                ("Which users are affected.",),
                (),
            ),
            (
                ("What API key should I use?",),
                (),
            ),
        )
        for suggestions, deterministic in cases:
            with self.subTest(suggestions=suggestions):
                with self.assertRaises(HarnessError):
                    validate_suggested_questions(suggestions, deterministic)

    def test_assistant_uses_structured_suggestions_and_returns_coaching(self) -> None:
        model = FakeModel(
            {"questions": ["Which API versions must remain compatible?"]},
            AIMessage(content="List the users, goal, and success measure."),
        )
        assistant = InterviewAssistant(
            model_factory=lambda _settings, _approval: model
        )

        suggestions = assistant.suggest_questions(
            ProviderSettings.from_environment({}),
            None,
            "Create an API harness.",
            {"purpose": "Maintain public routes."},
            ("What is the main purpose of the harness?",),
        )
        advice = assistant.coach(
            ProviderSettings.from_environment({}),
            None,
            "Create an API harness.",
            "What is the main purpose of the harness?",
            "What details should I include?",
        )
        chat_reply = assistant.answer_question(
            ProviderSettings.from_environment({}),
            None,
            "Create an API harness.",
            "What is the main purpose of the harness?",
            (
                {
                    "role": "assistant",
                    "content": "What is the main purpose of the harness?",
                },
                {"role": "user", "content": "Maintain public routes."},
            ),
            "What should I include?",
        )

        self.assertEqual(
            suggestions,
            ("Which API versions must remain compatible?",),
        )
        self.assertEqual(advice, "List the users, goal, and success measure.")
        self.assertEqual(chat_reply, "List the users, goal, and success measure.")
        chat_context = json.loads(model.messages[1].content)
        self.assertEqual(
            chat_context["conversation"][1]["content"],
            "Maintain public routes.",
        )
        self.assertEqual(chat_context["user_question"], "What should I include?")

    def test_topic_interview_extracts_user_stated_requirements(self) -> None:
        model = FakeModel(
            {
                "sufficient": False,
                "next_question": "Which API versions must remain compatible?",
                "extracted_requirements": [
                    {"key": "workflows", "value": "Maintain public API compatibility."}
                ],
                "unresolved_choices": [],
            },
            AIMessage(content=""),
        )
        assistant = InterviewAssistant(
            model_factory=lambda _settings, _approval: model
        )

        turn = assistant.interview_topic(
            ProviderSettings.from_environment({}),
            None,
            "Create an API harness.",
            ["software_development"],
            "github_copilot",
            {"purpose": "Maintain a public API."},
            (
                {"role": "assistant", "content": "What is the purpose?"},
                {"role": "user", "content": "Maintain a public API."},
            ),
            (),
        )

        self.assertIsInstance(turn, TopicInterviewTurn)
        self.assertEqual(turn.next_question, "Which API versions must remain compatible?")
        self.assertEqual(turn.extracted_requirements[0].key, "workflows")
        context = json.loads(model.messages[1].content)
        self.assertEqual(context["mandatory_gaps"], [])
        self.assertEqual(context["selected_task_types"], ["software_development"])

    def test_topic_interview_retries_invalid_structured_output_once(self) -> None:
        model = SequencedModel(
            [
                {
                    "sufficient": False,
                    "next_question": "What is the purpose?",
                    "extracted_requirements": [],
                    "unresolved_choices": [],
                    "unexpected": True,
                },
                {
                    "sufficient": False,
                    "next_question": "What is the main purpose?",
                    "extracted_requirements": [],
                    "unresolved_choices": ["purpose"],
                },
            ]
        )
        assistant = InterviewAssistant(
            model_factory=lambda _settings, _approval: model
        )

        turn = assistant.interview_topic(
            ProviderSettings.from_environment({}),
            None,
            "Create an API harness.",
            ["software_development"],
            "github_copilot",
            {},
            (),
            ("What is the main purpose of the harness?",),
        )

        self.assertEqual(turn.next_question, "What is the main purpose?")
        self.assertEqual(len(model.runnable.calls), 2)
        self.assertIn(
            "previous response did not pass validation",
            model.runnable.calls[1][-1].content,
        )

    def test_web_stage_log_records_interview_input_and_model_output(self) -> None:
        model = FakeModel(
            {
                "sufficient": False,
                "next_question": "Which API versions must remain compatible?",
                "extracted_requirements": [],
                "unresolved_choices": [],
            },
            AIMessage(content=""),
        )
        assistant = InterviewAssistant(
            model_factory=lambda _settings, _approval: model
        )
        with tempfile.TemporaryDirectory() as directory:
            log_path = configure_logging(
                log_directory=Path(directory) / "logs",
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
            )

            with web_logging_scope():
                assistant.interview_topic(
                    ProviderSettings.from_environment({}),
                    None,
                    "Create an API harness with explicit model trace.",
                    ["software_development"],
                    "github_copilot",
                    {"purpose": "Maintain API compatibility."},
                    (),
                    (),
                )

            content = log_path.read_text(encoding="utf-8")
            self.assertIn('"stage": "topic_interview"', content)
            self.assertIn('"event": "model_input"', content)
            self.assertIn("Create an API harness with explicit model trace.", content)
            self.assertIn('"event": "model_output"', content)
            self.assertIn("Which API versions must remain compatible?", content)

    def test_topic_interview_rejects_invalid_completion_or_unknown_fields(self) -> None:
        invalid_results = (
            {
                "sufficient": True,
                "next_question": "What is the goal?",
                "extracted_requirements": [],
                "unresolved_choices": [],
            },
            {
                "sufficient": False,
                "next_question": "What API key should I use?",
                "extracted_requirements": [{"key": "unknown", "value": "value"}],
                "unresolved_choices": [],
            },
        )
        for result in invalid_results:
            with self.subTest(result=result):
                assistant = InterviewAssistant(
                    model_factory=lambda _settings, _approval: FakeModel(
                        result, AIMessage(content="")
                    )
                )
                with self.assertRaises(HarnessError):
                    assistant.interview_topic(
                        ProviderSettings.from_environment({}),
                        None,
                        "Create an API harness.",
                        ["software_development"],
                        "github_copilot",
                        {},
                        (),
                        (),
                    )


if __name__ == "__main__":
    unittest.main()
