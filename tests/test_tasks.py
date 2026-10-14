from __future__ import annotations

import unittest

from harness_gen.question_templates import TASK_PROFILES, task_profile
from harness_gen.tasks import TaskType


class TaskProfileTests(unittest.TestCase):
    def test_profiles_cover_each_documented_task_type(self) -> None:
        self.assertEqual(set(TASK_PROFILES), set(TaskType))

    def test_data_science_profile_requires_inputs_before_provider(self) -> None:
        profile = task_profile(TaskType.DATA_SCIENCE)
        required = {
            question.key
            for question in profile.questions
            if question.required_before_provider_interview
        }

        self.assertEqual(required, {"data_source", "analysis_goal"})

    def test_custom_profile_requires_support_assessment(self) -> None:
        profile = task_profile(TaskType.CUSTOM)

        self.assertTrue(profile.requires_custom_support_assessment)
        self.assertEqual(
            {question.key for question in profile.questions},
            {"custom_goal", "custom_inputs", "custom_success", "custom_constraints"},
        )


if __name__ == "__main__":
    unittest.main()
