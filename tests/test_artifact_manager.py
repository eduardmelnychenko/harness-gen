from __future__ import annotations

import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import harness_gen.artifact_manager as artifact_manager
from harness_gen.artifact_manager import (
    WriteAction,
    apply_artifact_review,
    format_artifact_review,
    prepare_artifact_review,
    record_write_approval,
)
from harness_gen.artifacts import (
    GeneratedFile,
    GenerationResult,
    ValidationReport,
    ValidationStatus,
)
from harness_gen.errors import (
    ArtifactManagerError,
    PartialArtifactWriteError,
    StaleArtifactReview,
    UnvalidatedArtifactsError,
    WriteApprovalDeclined,
    WriteApprovalRequired,
)
from harness_gen.generation import plan_generation
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.targets import AgentTarget, ArtifactKind
from harness_gen.tasks import TaskType


def _generation(
    *,
    target: AgentTarget = AgentTarget.GITHUB_COPILOT,
    purpose: str = "Maintain the API.",
    task_instructions: bool = True,
) -> GenerationResult:
    requirements = RequirementsDraft(
        request="Create project instructions.",
        purpose=RequirementValue(purpose, confirmed=True),
        requirements_reviewed=True,
        task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
        target=target,
        task_type_instructions=task_instructions,
    )
    plan = plan_generation(requirements)
    files = []
    for artifact in plan.artifacts:
        if artifact.kind is ArtifactKind.CORE_INSTRUCTIONS:
            content = f"# Core instructions\nRead AGENTS.md.\n{purpose}"
        elif artifact.kind is ArtifactKind.SHARED_CONTEXT:
            content = (
                "# Shared project context\n"
                "This file provides shared project context and instructions. "
                f"It is not target settings. {purpose}"
            )
        else:
            content = artifact.frontmatter + "Apply the project task requirements."
        files.append(
            GeneratedFile(
                artifact_id=artifact.artifact_id,
                path=artifact.path,
                kind=artifact.kind,
                purpose=artifact.purpose,
                content=content,
            )
        )
    return GenerationResult(
        plan=plan,
        files=tuple(files),
        validation=ValidationReport(ValidationStatus.PASS),
        attempts=1,
    )


class ArtifactReviewTests(unittest.TestCase):
    def test_review_lists_destination_purposes_and_full_action_set(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, generation)
            summary = format_artifact_review(review)

            self.assertEqual(review.destination, destination.resolve())
            self.assertEqual(len(review.items), len(generation.files))
            self.assertTrue(
                all(item.action is WriteAction.CREATE for item in review.items)
            )
            self.assertEqual(summary[0], f"Project directory: {destination.resolve()}")
            self.assertTrue(any("Core instructions" in line for line in summary))
            self.assertFalse((destination / ".github").exists())

    def test_write_requires_review_approval_and_passing_validation(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, generation)

            with self.assertRaises(WriteApprovalDeclined):
                record_write_approval(review, approved=False)
            with self.assertRaises(WriteApprovalRequired):
                apply_artifact_review(review, generation, None)
            with self.assertRaises(UnvalidatedArtifactsError):
                prepare_artifact_review(
                    destination,
                    GenerationResult(
                        generation.plan,
                        generation.files,
                        ValidationReport(ValidationStatus.INCOMPLETE),
                        attempts=3,
                    ),
                )
            self.assertFalse((destination / ".github").exists())

    def test_apply_creates_files_and_directories_with_private_modes(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, generation)
            approval = record_write_approval(review, approved=True)

            result = apply_artifact_review(review, generation, approval)

            self.assertEqual(len(result.written), len(generation.files))
            self.assertEqual(set(item.path for item in result.written), {
                file.path for file in generation.files
            })
            self.assertEqual(
                result.created_directories, (".github", ".github/instructions")
            )
            for file in generation.files:
                path = destination / file.path
                self.assertEqual(path.read_text(), file.content)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((destination / ".github").stat().st_mode) & 0o700,
                0o700,
            )

    def test_replace_preserves_existing_file_permissions(self) -> None:
        generation = _generation(task_instructions=False)
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            core_path = destination / ".github/copilot-instructions.md"
            shared_path = destination / "AGENTS.md"
            core_path.parent.mkdir()
            core_path.write_text("Old core.")
            shared_path.write_text("Old shared context.")
            core_path.chmod(0o640)
            shared_path.chmod(0o644)
            review = prepare_artifact_review(destination, generation)
            self.assertTrue(
                all(item.action is WriteAction.REPLACE for item in review.items)
            )

            approval = record_write_approval(review, approved=True)
            apply_artifact_review(review, generation, approval)

            self.assertEqual(stat.S_IMODE(core_path.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(shared_path.stat().st_mode), 0o644)
            self.assertEqual(core_path.read_text(), generation.files[0].content)

    def test_stale_destination_file_aborts_before_any_write(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            core_path = destination / ".github/copilot-instructions.md"
            core_path.parent.mkdir()
            core_path.write_text("Initial content.")
            review = prepare_artifact_review(destination, generation)
            approval = record_write_approval(review, approved=True)
            core_path.write_text("Changed after review.")

            with self.assertRaises(StaleArtifactReview):
                apply_artifact_review(review, generation, approval)

            self.assertEqual(core_path.read_text(), "Changed after review.")
            self.assertFalse((destination / "AGENTS.md").exists())

    def test_changed_generation_draft_invalidates_approval(self) -> None:
        first_generation = _generation(purpose="Maintain the API.")
        changed_generation = _generation(purpose="Maintain the data service.")
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, first_generation)
            approval = record_write_approval(review, approved=True)

            with self.assertRaises(StaleArtifactReview):
                apply_artifact_review(review, changed_generation, approval)

            self.assertFalse((destination / "AGENTS.md").exists())

    def test_changed_reviewed_content_invalidates_approval(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, generation)
            changed_file = replace(
                review.items[0].file,
                content=review.items[0].file.content + "\nChanged.",
            )
            changed_items = (
                replace(review.items[0], file=changed_file),
                *review.items[1:],
            )
            changed_review = replace(review, items=changed_items)

            with self.assertRaises(StaleArtifactReview):
                record_write_approval(changed_review, approved=True)

    def test_symlink_destination_file_and_parent_are_rejected(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "project"
            destination.mkdir()
            outside_file = root / "outside.md"
            outside_file.write_text("Do not change.")
            (destination / ".github").mkdir()
            (destination / ".github/copilot-instructions.md").symlink_to(outside_file)

            with self.assertRaises(ArtifactManagerError):
                prepare_artifact_review(destination, generation)
            self.assertEqual(outside_file.read_text(), "Do not change.")

            linked_destination = root / "linked-project"
            linked_destination.symlink_to(destination, target_is_directory=True)
            with self.assertRaises(ArtifactManagerError):
                prepare_artifact_review(linked_destination, generation)

    def test_parent_directory_symlink_is_rejected(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "project"
            destination.mkdir()
            outside_directory = root / "outside"
            outside_directory.mkdir()
            (destination / ".github").symlink_to(
                outside_directory, target_is_directory=True
            )

            with self.assertRaises(ArtifactManagerError):
                prepare_artifact_review(destination, generation)
            self.assertEqual(tuple(outside_directory.iterdir()), ())

    def test_partial_failure_reports_files_already_written(self) -> None:
        generation = _generation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            review = prepare_artifact_review(destination, generation)
            approval = record_write_approval(review, approved=True)
            original_write = artifact_manager._write_reviewed_file
            call_count = 0

            def fail_on_second_file(root_fd: int, item) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("disk failure")
                original_write(root_fd, item)

            with patch(
                "harness_gen.artifact_manager._write_reviewed_file",
                side_effect=fail_on_second_file,
            ):
                with self.assertRaises(PartialArtifactWriteError) as raised:
                    apply_artifact_review(review, generation, approval)

            self.assertEqual(len(raised.exception.written_paths), 1)
            self.assertTrue(
                (destination / raised.exception.written_paths[0]).exists()
            )
            self.assertFalse((destination / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
