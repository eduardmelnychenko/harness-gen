from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from harness_gen.cli import run_cli
from harness_gen.artifacts import GeneratedFile, GenerationResult
from harness_gen.config import ProviderSettings
from harness_gen.generation import plan_generation
from harness_gen.requirements import RequirementsDraft, requirements_to_state
from harness_gen.targets import ArtifactKind
from harness_gen.validation import validate_generated_files


class FixedGenerator:
    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: object,
    ) -> GenerationResult:
        plan = plan_generation(requirements)
        files: list[GeneratedFile] = []
        confirmed = "\n".join(_confirmed_values(requirements_to_state(requirements)))
        for artifact in plan.artifacts:
            if artifact.kind is ArtifactKind.CORE_INSTRUCTIONS:
                content = f"# Core instructions\nRead AGENTS.md.\n{confirmed}"
            elif artifact.kind is ArtifactKind.SHARED_CONTEXT:
                content = (
                    "# Shared project context\nThis file provides shared project "
                    f"context and instructions. It is not target settings.\n{confirmed}"
                )
            else:
                content = artifact.frontmatter + "Follow confirmed requirements."
            files.append(
                GeneratedFile(
                    artifact_id=artifact.artifact_id,
                    path=artifact.path,
                    kind=artifact.kind,
                    purpose=artifact.purpose,
                    content=content,
                )
            )
        result_files = tuple(files)
        return GenerationResult(
            plan,
            result_files,
            validate_generated_files(plan, result_files),
            attempts=1,
        )


class CountingGenerator(FixedGenerator):
    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: object,
    ) -> GenerationResult:
        self.calls += 1
        return super().generate(requirements, provider_settings, approval)


class CommandLineTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness_gen")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_terminal_flow_collects_reviews_and_writes_approved_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "project"
            destination.mkdir()
            database = root / "private" / "session.db"
            answers = iter(
                [
                    "Create instructions for the API team.",
                    "Maintain the public API.",
                    "1",
                    "ollama",
                    "2",
                    "",
                    "Protect existing interfaces.",
                    "Clear instructions",
                    "",
                    "",
                    "HTTP service",
                    "Python",
                    "Unit tests",
                    "Linux",
                    "n",
                    "y",
                    "src/**/*.py",
                    "e",
                    "1",
                    "Maintain the corrected public API.",
                    "14",
                    "c",
                    str(destination),
                    "y",
                ]
            )
            output: list[str] = []

            result = run_cli(
                input_fn=_input_from(answers),
                output_fn=output.append,
                cwd=root,
                environ={"HARNESS_DB_URL": f"sqlite:///{database}"},
                generator=FixedGenerator(),
            )

            self.assertEqual(result, 0, output)
            self.assertTrue((destination / "AGENTS.md").is_file(), output)
            self.assertTrue(
                (destination / ".github" / "copilot-instructions.md").is_file()
            )
            self.assertTrue(
                any(
                    path.name.startswith("path-")
                    for path in (destination / ".github" / "instructions").glob("*")
                )
            )
            self.assertIn(
                "Maintain the corrected public API.",
                (destination / "AGENTS.md").read_text(),
            )
            self.assertTrue(database.is_file())
            self.assertTrue((root / "logs").is_dir())
            log_content = next((root / "logs").glob("*.log")).read_text()
            self.assertNotIn("Create instructions for the API team.", log_content)
            self.assertNotIn("Maintain the public API.", log_content)
            self.assertIn("Ollama is a provider", " ".join(output))
            self.assertIn("not an agent target", " ".join(output))
            self.assertIn(
                "local Ollama does not need a separate provider approval",
                " ".join(output),
            )
            self.assertTrue(any("Files were written" in item for item in output))

    def test_pause_during_interview_is_resumable_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "project"
            destination.mkdir()
            database = root / "session.db"
            initial = iter(["Create API instructions.", ":pause"])
            first_result = run_cli(
                input_fn=_input_from(initial),
                output_fn=lambda _: None,
                cwd=root,
                environ={"HARNESS_DB_URL": f"sqlite:///{database}"},
                generator=FixedGenerator(),
            )
            self.assertEqual(first_result, 0)

            resumed = iter(
                [
                    "r",
                    "Maintain the API.",
                    "1",
                    "2",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "HTTP",
                    "Python",
                    "Tests",
                    "",
                    "n",
                    "n",
                    "c",
                    str(destination),
                    "y",
                ]
            )
            output: list[str] = []
            second_result = run_cli(
                input_fn=_input_from(resumed),
                output_fn=output.append,
                cwd=root,
                environ={"HARNESS_DB_URL": f"sqlite:///{database}"},
                generator=FixedGenerator(),
            )

            self.assertEqual(second_result, 0, output)
            self.assertTrue((destination / "AGENTS.md").is_file(), output)

    def test_remote_provider_decline_stops_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "session.db"
            generator = CountingGenerator()
            answers = iter(
                [
                    "Create API instructions.",
                    "Maintain the API.",
                    "1",
                    "2",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "n",
                    "n",
                    "c",
                    "n",
                ]
            )
            output: list[str] = []

            result = run_cli(
                input_fn=_input_from(answers),
                output_fn=output.append,
                cwd=root,
                environ={
                    "HARNESS_DB_URL": f"sqlite:///{database}",
                    "HARNESS_OLLAMA_BASE_URL": "https://models.example.test",
                },
                generator=generator,
            )

            self.assertEqual(result, 1, output)
            self.assertEqual(generator.calls, 0)
            self.assertTrue(
                any("The request will go to Ollama" in line for line in output)
            )
            self.assertTrue(
                any("No provider request was sent" in line for line in output)
            )

    def test_data_science_unknowns_block_provider_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "session.db"
            generator = CountingGenerator()
            answers = iter(
                [
                    "Create analysis harness instructions.",
                    "Analyze the supplied data.",
                    "3",
                    "2",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "n",
                    "n",
                    "p",
                ]
            )
            output: list[str] = []

            result = run_cli(
                input_fn=_input_from(answers),
                output_fn=output.append,
                cwd=root,
                environ={
                    "HARNESS_DB_URL": f"sqlite:///{database}",
                    "HARNESS_OLLAMA_BASE_URL": "https://models.example.test",
                },
                generator=generator,
            )

            self.assertEqual(result, 0, output)
            self.assertEqual(generator.calls, 0)
            self.assertTrue(
                any(
                    "Confirm the data source for the analysis." in line
                    for line in output
                )
            )
            self.assertTrue(
                any("Confirm the goal of the analysis." in line for line in output)
            )
            self.assertFalse(any("Send the disclosed data" in line for line in output))


def _input_from(answers: object):
    iterator = iter(answers)

    def read(_: str) -> str:
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError from None

    return read


def _confirmed_values(value: object) -> list[str]:
    if isinstance(value, dict):
        if value.get("confirmed") is True and isinstance(value.get("value"), str):
            return [value["value"]]
        result: list[str] = []
        for item in value.values():
            result.extend(_confirmed_values(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_confirmed_values(item))
        return result
    return []


if __name__ == "__main__":
    unittest.main()
