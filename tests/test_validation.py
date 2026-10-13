from __future__ import annotations

import unittest

from harness_gen.artifacts import (
    ArtifactBody,
    GeneratedContentResponse,
    GeneratedFile,
    GenerationPlan,
    ValidationStatus,
)
from harness_gen.generation import plan_generation
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.targets import AgentTarget
from harness_gen.tasks import TaskType
from harness_gen.validation import (
    is_safe_output_path,
    validate_generated_files,
    validate_generated_response,
)


def _plan():
    requirements = RequirementsDraft(
        request="Create instructions for an API.",
        purpose=RequirementValue("Maintain the API.", True),
        requirements_reviewed=True,
        task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
        target=AgentTarget.CLAUDE_CODE,
    )
    return plan_generation(requirements)


def _response(plan):
    return GeneratedContentResponse(
        files=[
            {
                "artifact_id": artifact.artifact_id,
                "content": (
                    "# Core\nMaintain the API. See AGENTS.md."
                    if artifact.artifact_id == "core"
                    else "# Shared project context\n"
                    "This is shared project context, not target settings. "
                    "Maintain the API."
                ),
            }
            for artifact in plan.artifacts
        ]
    )


class OutputValidationTests(unittest.TestCase):
    def test_validates_required_details_and_shared_context(self) -> None:
        plan = _plan()

        files, report = validate_generated_response(plan, _response(plan))

        self.assertEqual(report.status, ValidationStatus.PASS)
        self.assertEqual(len(files), 2)

    def test_missing_confirmed_detail_is_incomplete(self) -> None:
        plan = _plan()
        response = _response(plan)
        response.files[0].content = "# Core\nSee AGENTS.md."

        _, report = validate_generated_response(plan, response)

        self.assertEqual(report.status, ValidationStatus.INCOMPLETE)
        self.assertTrue(
            any(item.code == "missing_confirmed_detail" for item in report.findings)
        )

    def test_invalid_or_duplicate_paths_fail_validation(self) -> None:
        plan = _plan()
        files = (
            GeneratedFile(
                artifact_id="core",
                path="../outside.md",
                kind=plan.artifacts[0].kind,
                purpose=plan.artifacts[0].purpose,
                content="# Core\nSee AGENTS.md.\nMaintain the API.",
            ),
        )

        report = validate_generated_files(plan, files)

        self.assertEqual(report.status, ValidationStatus.FAIL)
        self.assertFalse(is_safe_output_path("../outside.md"))
        self.assertFalse(is_safe_output_path("src/./main.py"))

    def test_unrequested_artifact_fails_validation(self) -> None:
        plan = _plan()
        response = _response(plan)
        response.files.append(
            ArtifactBody(artifact_id="extra", content="unrequested")
        )

        _, report = validate_generated_response(plan, response)

        self.assertEqual(report.status, ValidationStatus.FAIL)
        self.assertTrue(
            any(item.code == "unexpected_artifact" for item in report.findings)
        )

    def test_empty_plan_fails_validation(self) -> None:
        plan = _plan()
        empty_plan = GenerationPlan(
            target=plan.target,
            artifacts=(),
            requirements=plan.requirements,
        )

        _, report = validate_generated_response(
            empty_plan,
            GeneratedContentResponse(files=[]),
        )

        self.assertEqual(report.status, ValidationStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
