from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError
from harness_gen.interview_assistance import (
    InterviewAssistant,
    validate_suggested_questions,
)


class FakeRunnable:
    def __init__(self, output: object) -> None:
        self.output = output

    def invoke(self, _messages: object) -> object:
        return self.output


class FakeModel:
    def __init__(self, structured_output: object, coaching_output: object) -> None:
        self.structured_output = structured_output
        self.coaching_output = coaching_output

    def with_structured_output(self, _schema: type[object]) -> FakeRunnable:
        return FakeRunnable(self.structured_output)

    def invoke(self, _messages: object) -> object:
        return self.coaching_output


class InterviewAssistanceValidationTests(unittest.TestCase):
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

        self.assertEqual(
            suggestions,
            ("Which API versions must remain compatible?",),
        )
        self.assertEqual(advice, "List the users, goal, and success measure.")


if __name__ == "__main__":
    unittest.main()
