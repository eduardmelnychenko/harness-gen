from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import httpx

from harness_gen.artifacts import GenerationPlan
from harness_gen.config import ProviderSettings
from harness_gen.errors import (
    ProviderRequestError,
    RequirementsIncompleteError,
)
from harness_gen.generation import HarnessGenerator, plan_generation
from harness_gen.logging_config import configure_logging
from harness_gen.logging_events import web_logging_scope
from harness_gen.provider_approval import ProviderApproval
from harness_gen.requirements import (
    ConfirmedPathPattern,
    InterviewMessage,
    RequirementValue,
    RequirementsDraft,
    SupplementalAnswer,
    SetupRequirements,
    TaskAnswer,
)
from harness_gen.targets import AgentTarget, ArtifactKind
from harness_gen.tasks import TaskType


class FakeRunnable:
    def __init__(self, outputs: Sequence[object]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> object:
        self.calls.append(messages)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


class FakeChatModel:
    def __init__(self, outputs: Sequence[object]) -> None:
        self.runnable = FakeRunnable(outputs)

    def with_structured_output(self, _schema: type[object]) -> FakeRunnable:
        return self.runnable


def _requirements(
    *,
    reviewed: bool = True,
    target: AgentTarget = AgentTarget.GITHUB_COPILOT,
    task_type_instructions: bool = True,
) -> RequirementsDraft:
    return RequirementsDraft(
        request="Create instructions for a safe service.",
        purpose=RequirementValue(
            "Build a safe service harness.", confirmed=True
        ),
        requirements_reviewed=reviewed,
        task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
        target=target,
        operating_context=RequirementValue(
            "Run on Linux.", confirmed=True
        ),
        task_answers=(
            TaskAnswer(
                "workflows",
                "Support safe API updates.",
                confirmed=True,
            ),
        ),
        constraints=(RequirementValue("Do not expose customer data.", True),),
        desired_outputs=(RequirementValue("Document API behavior.", True),),
        setup=SetupRequirements(
            runtime=RequirementValue("Use Python 3.14.", True),
            verification=(RequirementValue("Run uv tests.", True),),
        ),
        task_type_instructions=task_type_instructions,
        path_scoped_instructions=True,
        path_patterns=(
            ConfirmedPathPattern("src/**/*.py", confirmed=True),
        ),
    )


def _response(plan: GenerationPlan, *, missing_detail: str | None = None) -> dict:
    details = (
        "Build a safe service harness.",
        "Run on Linux.",
        "Do not expose customer data.",
        "Document API behavior.",
        "Support safe API updates.",
        "Use Python 3.14.",
        "Run uv tests.",
    )
    if missing_detail is not None:
        details = tuple(item for item in details if item != missing_detail)
    confirmed_text = "\n".join(details)
    files = []
    for artifact in plan.artifacts:
        if artifact.artifact_id == "core":
            content = f"# Core instructions\nRead AGENTS.md.\n{confirmed_text}"
        elif artifact.artifact_id == "shared-context":
            content = (
                "# Shared project context\n"
                "This file provides shared project context and instructions. "
                "It is not target settings.\n"
                f"{confirmed_text}"
            )
        else:
            content = "Follow the selected task requirements."
        files.append(
            {
                "artifact_id": artifact.artifact_id,
                "content": content,
            }
        )
    return {"files": files}


class GenerationTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness_gen")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_plans_exact_target_native_paths(self) -> None:
        plan = plan_generation(_requirements())

        self.assertEqual(
            [item.path for item in plan.artifacts],
            [
                ".github/copilot-instructions.md",
                "AGENTS.md",
                ".github/instructions/task-software-development.instructions.md",
                ".github/instructions/path-001.instructions.md",
            ],
        )
        self.assertEqual(plan.artifacts[-1].path_pattern, "src/**/*.py")
        self.assertIn('applyTo: "src/**/*.py"', plan.artifacts[-1].frontmatter)

    def test_generates_and_validates_the_complete_file_set(self) -> None:
        requirements = replace(
            _requirements(),
            supplemental_answers=(
                SupplementalAnswer(
                    "Which API versions must remain compatible?",
                    "Keep version 1 supported.",
                ),
            ),
        )
        plan = plan_generation(requirements)
        model = FakeChatModel([_response(plan)])
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model
        )

        result = generator.generate(requirements, settings, None)

        self.assertTrue(result.writable)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(result.files), len(plan.artifacts))
        prompt = model.runnable.calls[0][1].content
        self.assertIn("Support safe API updates.", prompt)
        self.assertIn("Which API versions must remain compatible?", prompt)
        self.assertIn("Keep version 1 supported.", prompt)
        self.assertNotIn(requirements.request, prompt)
        path_file = next(
            item
            for item in result.files
            if item.kind is ArtifactKind.PATH_SCOPED_INSTRUCTIONS
        )
        self.assertTrue(
            path_file.content.startswith(plan.artifacts[-1].frontmatter)
        )

    def test_web_stage_log_records_generation_output_and_validation_locations(
        self,
    ) -> None:
        requirements = _requirements()
        plan = plan_generation(requirements)
        model = FakeChatModel(
            [_response(plan, missing_detail="Use Python 3.14.")]
        )
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model,
            max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            log_path = configure_logging(
                log_directory=Path(directory) / "logs",
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
            )

            with web_logging_scope():
                result = generator.generate(requirements, settings, None)

            self.assertFalse(result.writable)
            content = log_path.read_text(encoding="utf-8")
            self.assertIn('"stage": "generation"', content)
            self.assertIn('"event": "model_output"', content)
            self.assertIn("Build a safe service harness.", content)
            self.assertIn('"stage": "output_validation"', content)
            self.assertIn('"code": "missing_confirmed_detail"', content)
            self.assertIn('"location": ".github/copilot-instructions.md"', content)

    def test_core_prompt_keeps_task_types_when_separate_files_are_not_selected(
        self,
    ) -> None:
        requirements = _requirements(task_type_instructions=False)
        plan = plan_generation(requirements)
        model = FakeChatModel([_response(plan)])
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model
        )

        result = generator.generate(requirements, settings, None)

        self.assertTrue(result.writable)
        self.assertEqual(len(result.files), 3)
        self.assertIn("Software development", model.runnable.calls[0][1].content)

    def test_generation_includes_full_chat_as_separate_context(self) -> None:
        requirements = replace(
            _requirements(),
            interview_conversation=(
                InterviewMessage("user", "Can the harness cover API version 2?"),
                InterviewMessage(
                    "assistant",
                    "Include version 2 if the team confirms it.",
                ),
            ),
        )
        plan = plan_generation(requirements)
        model = FakeChatModel([_response(plan)])
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model
        )

        result = generator.generate(requirements, settings, None)

        self.assertTrue(result.writable)
        prompt = model.runnable.calls[0][1].content
        context = json.loads(prompt.split("\n\n", maxsplit=1)[1])
        self.assertEqual(
            context["interview_conversation_context"],
            [
                {
                    "role": "user",
                    "content": "Can the harness cover API version 2?",
                },
                {
                    "role": "assistant",
                    "content": "Include version 2 if the team confirms it.",
                },
            ],
        )
        self.assertNotIn(
            "interview_conversation_context",
            context["confirmed_requirements"],
        )
        self.assertIn(
            "Assistant\nmessages are advice, not requirements.",
            model.runnable.calls[0][0].content,
        )

    def test_retries_incomplete_files_and_returns_only_after_pass(self) -> None:
        requirements = _requirements()
        plan = plan_generation(requirements)
        model = FakeChatModel(
            [
                _response(plan, missing_detail="Use Python 3.14."),
                _response(plan),
            ]
        )
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model,
            max_attempts=2,
        )

        result = generator.generate(requirements, settings, None)

        self.assertTrue(result.writable)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(model.runnable.calls), 2)
        self.assertEqual(len(model.runnable.calls[1]), 3)

    def test_failed_retry_limit_never_marks_files_writable(self) -> None:
        requirements = _requirements()
        plan = plan_generation(requirements)
        model = FakeChatModel(
            [
                _response(plan, missing_detail="Use Python 3.14."),
                _response(plan, missing_detail="Use Python 3.14."),
            ]
        )
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model,
            max_attempts=2,
        )

        result = generator.generate(requirements, settings, None)

        self.assertFalse(result.writable)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.validation.status.value, "incomplete")

    def test_incomplete_requirements_block_before_model_creation(self) -> None:
        requirements = _requirements(reviewed=False)
        settings = ProviderSettings.from_environment({})
        factory_calls = 0

        def factory(
            _settings: ProviderSettings,
            _requirements: RequirementsDraft,
            _approval: ProviderApproval | None,
        ) -> FakeChatModel:
            nonlocal factory_calls
            factory_calls += 1
            return FakeChatModel([])

        with self.assertRaises(RequirementsIncompleteError):
            HarnessGenerator(model_factory=factory).generate(
                requirements, settings, None
            )
        self.assertEqual(factory_calls, 0)

    def test_provider_errors_are_reported_without_vendor_details(self) -> None:
        requirements = _requirements()
        settings = ProviderSettings.from_environment({})
        model = FakeChatModel([httpx.ConnectError("credential-like-private-text")])
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model
        )

        with self.assertRaises(ProviderRequestError) as raised:
            generator.generate(requirements, settings, None)

        self.assertNotIn("credential-like-private-text", str(raised.exception))
        self.assertEqual(
            raised.exception.output_paths,
            tuple(item.path for item in plan_generation(requirements).artifacts),
        )

    def test_malformed_structured_response_is_retried(self) -> None:
        requirements = _requirements()
        plan = plan_generation(requirements)
        model = FakeChatModel([{"unexpected": "shape"}, _response(plan)])
        settings = ProviderSettings.from_environment({})
        generator = HarnessGenerator(
            model_factory=lambda _settings, _requirements, _approval: model,
            max_attempts=2,
        )

        result = generator.generate(requirements, settings, None)

        self.assertTrue(result.writable)
        self.assertEqual(result.attempts, 2)

    def test_generation_rejects_zero_attempt_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            HarnessGenerator(max_attempts=0)


if __name__ == "__main__":
    unittest.main()
