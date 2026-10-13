from __future__ import annotations

import unittest

from harness_gen.errors import RequirementsError
from harness_gen.requirements import (
    ConfirmedPathPattern,
    CustomTaskSupport,
    RequirementValue,
    RequirementsDraft,
    SetupRequirements,
    TaskAnswer,
    requirements_from_state,
    requirements_to_state,
)
from harness_gen.targets import AgentTarget
from harness_gen.tasks import TaskType


class RequirementsDraftTests(unittest.TestCase):
    def test_data_science_blocks_provider_until_inputs_are_confirmed(self) -> None:
        draft = RequirementsDraft(
            request="Create an analysis harness.",
            task_types=(TaskType.DATA_SCIENCE,),
            target=AgentTarget.CLAUDE_CODE,
            setup=SetupRequirements(
                data_source=RequirementValue("sales table", confirmed=False),
                analysis_goal=RequirementValue(
                    "compare monthly totals", confirmed=False
                ),
            ),
        )

        self.assertEqual(
            draft.provider_interview_blockers,
            (
                "Confirm the data source for the analysis.",
                "Confirm the goal of the analysis.",
            ),
        )

    def test_confirmed_data_science_inputs_allow_provider_interview(self) -> None:
        draft = RequirementsDraft(
            request="Create an analysis harness.",
            task_types=(TaskType.DATA_SCIENCE,),
            target=AgentTarget.CLAUDE_CODE,
            setup=SetupRequirements(
                data_source=RequirementValue("sales table", confirmed=True),
                analysis_goal=RequirementValue(
                    "compare monthly totals", confirmed=True
                ),
            ),
        )

        self.assertEqual(draft.provider_interview_blockers, ())

    def test_named_confirmed_data_science_answers_satisfy_provider_gate(self) -> None:
        draft = RequirementsDraft(
            request="Create an analysis harness.",
            task_types=(TaskType.DATA_SCIENCE,),
            target=AgentTarget.CLAUDE_CODE,
            task_answers=(
                TaskAnswer("data_source", "sales table", confirmed=True),
                TaskAnswer("analysis_goal", "compare monthly totals", confirmed=True),
            ),
        )

        self.assertEqual(draft.provider_interview_blockers, ())

    def test_material_setup_and_open_choices_block_generation(self) -> None:
        draft = RequirementsDraft(
            request="Create a software harness.",
            purpose=RequirementValue("Support API maintenance", confirmed=True),
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.GITHUB_COPILOT,
            setup=SetupRequirements(
                runtime=RequirementValue("unknown runtime", confirmed=False)
            ),
            unresolved_choices=("runtime",),
        )

        self.assertFalse(draft.ready_for_generation)
        self.assertIn("Resolve material open choices.", draft.generation_blockers)
        self.assertIn(
            "Confirm the runtime or keep it as an open choice.",
            draft.generation_blockers,
        )

    def test_custom_tasks_need_support_assessment(self) -> None:
        draft = RequirementsDraft(
            request="Create a custom harness.",
            purpose=RequirementValue("Support a custom workflow", confirmed=True),
            task_types=(TaskType.CUSTOM,),
            target=AgentTarget.CLAUDE_CODE,
        )
        self.assertIn(
            "Assess whether the clarified custom task is supported.",
            draft.generation_blockers,
        )

        supported = RequirementsDraft(
            request="Create a custom harness.",
            purpose=RequirementValue("Support a custom workflow", confirmed=True),
            requirements_reviewed=True,
            task_types=(TaskType.CUSTOM,),
            target=AgentTarget.CLAUDE_CODE,
            custom_task_support=CustomTaskSupport.SUPPORTED,
            task_answers=(
                TaskAnswer("custom_goal", "Create a report.", confirmed=True),
                TaskAnswer("custom_inputs", "CSV to report.", confirmed=True),
                TaskAnswer("custom_success", "The report is complete.", confirmed=True),
                TaskAnswer("custom_constraints", "Use local files.", confirmed=True),
            ),
        )
        self.assertTrue(supported.ready_for_generation)

        unsupported = RequirementsDraft(
            request="Create a custom harness.",
            purpose=RequirementValue("Support a custom workflow", confirmed=True),
            task_types=(TaskType.CUSTOM,),
            target=AgentTarget.CLAUDE_CODE,
            custom_task_support=CustomTaskSupport.UNSUPPORTED,
        )
        self.assertFalse(unsupported.ready_for_generation)

    def test_path_scoped_output_requires_confirmed_exact_patterns(self) -> None:
        draft = RequirementsDraft(
            request="Create a scoped instruction harness.",
            purpose=RequirementValue("Guide Python work", confirmed=True),
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.CLAUDE_CODE,
            path_scoped_instructions=True,
        )
        self.assertIn(
            "Supply and confirm exact relative path patterns.",
            draft.generation_blockers,
        )

        pattern = "src/**/*.py"
        confirmed = RequirementsDraft(
            request="Create a scoped instruction harness.",
            purpose=RequirementValue("Guide Python work", confirmed=True),
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.CLAUDE_CODE,
            path_scoped_instructions=True,
            path_patterns=(ConfirmedPathPattern(pattern, confirmed=True),),
        )
        self.assertEqual(confirmed.path_patterns[0].pattern, pattern)
        self.assertNotIn(pattern, repr(confirmed))

    def test_rejects_unsafe_or_ambiguous_path_patterns(self) -> None:
        unsafe_patterns = (
            "/src/*.py",
            "../src/*.py",
            "src/../*.py",
            r"src\*.py",
            "C:/src/*.py",
        )
        for pattern in unsafe_patterns:
            with self.subTest(pattern=pattern):
                with self.assertRaises(RequirementsError):
                    ConfirmedPathPattern(pattern, confirmed=True)

    def test_confirmed_answers_must_have_a_value(self) -> None:
        with self.assertRaisesRegex(RequirementsError, "confirmed requirement"):
            RequirementValue(None, confirmed=True)

    def test_unconfirmed_task_answer_blocks_generation(self) -> None:
        draft = RequirementsDraft(
            request="Create a software harness.",
            purpose=RequirementValue("Support API changes", confirmed=True),
            requirements_reviewed=True,
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.CLAUDE_CODE,
            task_answers=(TaskAnswer("verification", "run tests", False),),
        )

        self.assertIn(
            "Confirm each material task-specific answer.",
            draft.generation_blockers,
        )

    def test_checkpoint_serialization_preserves_structured_requirements(self) -> None:
        draft = RequirementsDraft(
            request="Create an API maintenance harness.",
            purpose=RequirementValue("Maintain the API", confirmed=True),
            requirements_reviewed=True,
            task_types=(TaskType.SOFTWARE_DEVELOPMENT, TaskType.MACHINE_LEARNING),
            target=AgentTarget.GITHUB_COPILOT,
            operating_context=RequirementValue("Existing service", True),
            task_answers=(TaskAnswer("evaluation", "Use F1 score", True),),
            constraints=(RequirementValue("No downtime", True),),
            desired_outputs=(RequirementValue("Test plan", True),),
            setup=SetupRequirements(
                platform=RequirementValue("Linux", True),
                runtime=RequirementValue("Python 3.14", True),
                dependencies=(RequirementValue("pytest", True),),
                configuration=(RequirementValue("Use local env vars", True),),
                verification=(RequirementValue("Run unit tests", True),),
            ),
            unresolved_choices=("Hosting platform",),
            task_type_instructions=True,
            path_scoped_instructions=True,
            path_patterns=(ConfirmedPathPattern("src/**/*.py", True),),
        )

        self.assertEqual(
            requirements_from_state(requirements_to_state(draft)),
            draft,
        )

    def test_checkpoint_serialization_rejects_unrecognized_fields(self) -> None:
        values = requirements_to_state(
            RequirementsDraft(request="Create project instructions.")
        )
        values["api_key"] = "must not be checkpointed"

        with self.assertRaises(RequirementsError):
            requirements_from_state(values)


if __name__ == "__main__":
    unittest.main()
