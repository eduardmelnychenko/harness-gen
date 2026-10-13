from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness_gen.artifacts import (
    GeneratedFile,
    GenerationResult,
    ValidationFinding,
    ValidationReport,
    ValidationStatus,
)
from harness_gen.config import Provider, ProviderSettings
from harness_gen.errors import SessionPersistenceError
from harness_gen.generation import plan_generation
from harness_gen.requirements import (
    ConfirmedPathPattern,
    RequirementValue,
    RequirementsDraft,
    requirements_to_state,
)
from harness_gen.provider_approval import ProviderApproval
from harness_gen.sessions import DatabaseSettings, SessionManager
from harness_gen.targets import AgentTarget, ArtifactKind
from harness_gen.tasks import TaskType
from harness_gen.validation import validate_generated_files


class FixedGenerator:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: object,
    ) -> GenerationResult:
        self.calls.append(approval)
        plan = plan_generation(requirements)
        files: list[GeneratedFile] = []
        for artifact in plan.artifacts:
            if artifact.kind is ArtifactKind.CORE_INSTRUCTIONS:
                content = (
                    "# Core instructions\nRead AGENTS.md.\n"
                    f"{requirements.purpose.confirmed_value}"
                )
            elif artifact.kind is ArtifactKind.SHARED_CONTEXT:
                content = (
                    "# Shared project context\n"
                    "This file provides shared project context and instructions. "
                    f"It is not target settings. "
                    f"{requirements.purpose.confirmed_value}"
                )
            else:
                content = artifact.frontmatter + "Apply the confirmed requirements."
            files.append(
                GeneratedFile(
                    artifact_id=artifact.artifact_id,
                    path=artifact.path,
                    kind=artifact.kind,
                    purpose=artifact.purpose,
                    content=content,
                )
            )
        output_files = tuple(files)
        report = validate_generated_files(plan, output_files)
        return GenerationResult(plan, output_files, report, attempts=1)


class InvalidGenerator(FixedGenerator):
    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: object,
    ) -> GenerationResult:
        result = super().generate(requirements, provider_settings, approval)
        return GenerationResult(
            result.plan,
            result.files,
            ValidationReport(
                ValidationStatus.FAIL,
                (
                    ValidationFinding(
                        "invalid_output",
                        "The output did not pass validation.",
                    ),
                ),
            ),
            result.attempts,
        )


class WorkflowGraphTests(unittest.TestCase):
    def test_graph_requires_review_and_write_approval_before_apply(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.db"
            destination = Path(temporary_directory) / "project"
            destination.mkdir()
            with SessionManager(
                _database_settings(database),
                provider_settings=_local_provider(),
                generator=generator,
            ) as manager:
                reference = manager.start_workflow(_complete_requirements())
                self.assertEqual(manager.state()["phase"], "requirements_review")

                with self.assertRaises(SessionPersistenceError):
                    manager.resume(
                        {"approved": True, "api_key": "must not persist"}
                    )
                result = manager.resume({"approved": True})
                self.assertEqual(result["phase"], "destination_selection")
                self.assertEqual(len(generator.calls), 1)

                result = manager.resume({"destination": str(destination)})
                self.assertEqual(result["phase"], "write_approval")
                self.assertTrue(result["review_summary"])
                self.assertFalse((destination / "AGENTS.md").exists())

            with SessionManager(
                _database_settings(database),
                provider_settings=_local_provider(),
                generator=FixedGenerator(),
            ) as resumed:
                self.assertEqual(resumed.active_thread_id, reference.thread_id)
                self.assertEqual(resumed.state()["phase"], "write_approval")
                result = resumed.resume({"approved": True})
                self.assertEqual(result["outcome"], "complete")
                self.assertTrue((destination / "AGENTS.md").is_file())
                self.assertEqual(
                    (destination / "AGENTS.md").read_text(),
                    next(
                        item["content"]
                        for item in result["generated_files"]
                        if item["path"] == "AGENTS.md"
                    ),
                )

    def test_generation_is_blocked_when_requirements_are_incomplete(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=generator,
            ) as manager:
                manager.start_workflow(
                    RequirementsDraft(
                        request="Create project instructions.",
                        task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
                        target=AgentTarget.GITHUB_COPILOT,
                    )
                )
                result = manager.resume({"approved": True})

                self.assertEqual(result["outcome"], "requirements_incomplete")
                self.assertIn("purpose", result["error_message"])
                self.assertEqual(generator.calls, [])

    def test_remote_provider_requires_approval_and_rechecks_endpoint(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.db"
            remote_a = _remote_provider("https://ollama.example.test")
            with SessionManager(
                _database_settings(database),
                provider_settings=remote_a,
                generator=generator,
            ) as manager:
                manager.start_workflow(_complete_requirements())
                result = manager.resume({"approved": True})
                self.assertEqual(result["phase"], "provider_approval_wait")
                self.assertIn(
                    "Ollama at a remote endpoint",
                    result["provider_pending"]["disclosure"],
                )

            with SessionManager(
                _database_settings(database),
                provider_settings=_remote_provider(
                    "https://different.example.test"
                ),
                generator=generator,
            ) as resumed:
                result = resumed.resume({"approved": True})
                self.assertEqual(result["outcome"], "provider_approval_expired")
                self.assertEqual(generator.calls, [])

    def test_declining_external_provider_sends_no_generation_request(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_remote_provider(
                    "https://ollama.example.test"
                ),
                generator=generator,
            ) as manager:
                manager.start_workflow(_complete_requirements())
                manager.resume({"approved": True})
                result = manager.resume({"approved": False})

                self.assertEqual(result["outcome"], "provider_approval_declined")
                self.assertEqual(generator.calls, [])

    def test_external_approval_is_passed_to_generation(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_remote_provider(
                    "https://ollama.example.test"
                ),
                generator=generator,
            ) as manager:
                manager.start_workflow(_complete_requirements())
                manager.resume({"approved": True})
                result = manager.resume({"approved": True})

                self.assertEqual(result["phase"], "destination_selection")
                self.assertEqual(len(generator.calls), 1)
                self.assertIsInstance(generator.calls[0], ProviderApproval)

    def test_failed_offline_validation_blocks_destination_and_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "project"
            destination.mkdir()
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=InvalidGenerator(),
            ) as manager:
                manager.start_workflow(_complete_requirements())
                result = manager.resume({"approved": True})

                self.assertEqual(result["outcome"], "validation_failed")
                self.assertEqual(result["validation_status"], "fail")
                self.assertFalse((destination / "AGENTS.md").exists())

    def test_declined_full_write_set_does_not_change_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "project"
            destination.mkdir()
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=FixedGenerator(),
            ) as manager:
                manager.start_workflow(_complete_requirements())
                manager.resume({"approved": True})
                manager.resume({"destination": str(destination)})
                result = manager.resume({"approved": False})

                self.assertEqual(result["outcome"], "write_declined")
                self.assertEqual(tuple(destination.iterdir()), ())

    def test_requirement_corrections_return_to_review(self) -> None:
        generator = FixedGenerator()
        corrected = _complete_requirements(
            purpose="Maintain the data service."
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=generator,
            ) as manager:
                manager.start_workflow(_complete_requirements())
                result = manager.resume(
                    {"requirements": requirements_to_state(corrected)}
                )

                self.assertEqual(result["phase"], "requirements_review")
                self.assertEqual(
                    result["requirements"]["purpose"]["value"],
                    "Maintain the data service.",
                )
                self.assertFalse(result["requirements_reviewed"])
                self.assertEqual(generator.calls, [])

    def test_requirements_review_confirms_exact_path_patterns(self) -> None:
        requirements = replace(
            _complete_requirements(),
            path_scoped_instructions=True,
            path_patterns=(ConfirmedPathPattern("src/**/*.py"),),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=FixedGenerator(),
            ) as manager:
                manager.start_workflow(requirements)
                result = manager.resume({"approved": True})

                self.assertEqual(result["phase"], "destination_selection")
                self.assertEqual(
                    result["requirements"]["path_patterns"],
                    [{"pattern": "src/**/*.py", "confirmed": True}],
                )

    def test_restart_workflow_drops_generated_files_and_reviews(self) -> None:
        generator = FixedGenerator()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "project"
            destination.mkdir()
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=generator,
            ) as manager:
                original = manager.start_workflow(_complete_requirements())
                manager.resume({"approved": True})
                manager.resume({"destination": str(destination)})

                restarted = manager.restart()
                state = manager.state()

                self.assertNotEqual(restarted.thread_id, original.thread_id)
                self.assertEqual(state["phase"], "requirements_review")
                self.assertFalse(state["requirements"]["requirements_reviewed"])
                self.assertNotIn("generated_files", state)
                self.assertNotIn("artifact_review", state)
                self.assertNotIn("provider_approval", state)
                self.assertFalse((destination / "AGENTS.md").exists())

    def test_requirement_correction_cannot_add_credential_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _database_settings(Path(temporary_directory) / "state.db"),
                provider_settings=_local_provider(),
                generator=FixedGenerator(),
            ) as manager:
                manager.start_workflow(_complete_requirements())
                corrected = requirements_to_state(_complete_requirements())
                corrected["api_key"] = "must not be checkpointed"

                with self.assertRaises(SessionPersistenceError):
                    manager.resume({"requirements": corrected})

                self.assertEqual(manager.state()["phase"], "requirements_review")
                self.assertEqual(
                    manager.resume({"approved": True})["phase"],
                    "destination_selection",
                )


def _complete_requirements(
    purpose: str = "Maintain the API.",
) -> RequirementsDraft:
    return RequirementsDraft(
        request="Create project instructions.",
        purpose=RequirementValue(purpose, confirmed=True),
        task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
        target=AgentTarget.GITHUB_COPILOT,
    )


def _local_provider() -> ProviderSettings:
    return ProviderSettings(
        provider=Provider.OLLAMA,
        model="llama3.2",
        base_url="http://localhost:11434",
    )


def _remote_provider(endpoint: str) -> ProviderSettings:
    return ProviderSettings(
        provider=Provider.OLLAMA,
        model="llama3.2",
        base_url=endpoint,
    )


def _database_settings(path: Path) -> DatabaseSettings:
    return DatabaseSettings(url=f"sqlite:///{path}", path=path)


if __name__ == "__main__":
    unittest.main()
