from __future__ import annotations

import unittest

from harness_gen.config import Provider, ProviderSettings
from harness_gen.errors import ProviderApprovalRequired, RequirementsIncompleteError
from harness_gen.provider_approval import (
    authorize_provider_request,
    create_authorized_chat_model,
    create_authorized_interview_chat_model,
    describe_provider,
    record_provider_approval,
)
from harness_gen.requirements import (
    RequirementValue,
    RequirementsDraft,
    SetupRequirements,
)
from harness_gen.targets import AgentTarget
from harness_gen.tasks import TaskType


def _requirements(
    task_type: TaskType = TaskType.SOFTWARE_DEVELOPMENT,
    *,
    data_source: RequirementValue | None = None,
    analysis_goal: RequirementValue | None = None,
) -> RequirementsDraft:
    return RequirementsDraft(
        request="Create a harness for a confirmed project request.",
        purpose=RequirementValue("Support the project task", confirmed=True),
        task_types=(task_type,),
        target=AgentTarget.CLAUDE_CODE,
        setup=SetupRequirements(
            data_source=data_source,
            analysis_goal=analysis_goal,
        ),
    )


class ProviderApprovalTests(unittest.TestCase):
    def test_local_ollama_does_not_require_external_provider_approval(self) -> None:
        settings = ProviderSettings.from_environment({})
        disclosure = describe_provider(settings)

        self.assertFalse(disclosure.requires_approval)
        self.assertIn("local Ollama", disclosure.render())
        self.assertIn("does not find or remove", disclosure.render())
        self.assertIsNone(record_provider_approval(disclosure, approved=False))
        self.assertEqual(
            authorize_provider_request(settings, _requirements(), None),
            disclosure,
        )

    def test_external_provider_requires_approval_bound_to_configuration(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "secret-test-key",
            }
        )
        disclosure = describe_provider(settings)

        self.assertTrue(disclosure.requires_approval)
        self.assertIn("OpenAI", disclosure.render())
        self.assertIn("api.openai.com", disclosure.render())
        for category in (
            "the initial request",
            "selected choices",
            "follow-up answers",
            "interview questions and answers",
            "your questions and the model's replies",
            "confirmed setup details",
        ):
            self.assertIn(category, disclosure.render())
        self.assertIn("follow-up answers", disclosure.render())
        self.assertIn("Do not enter secret values.", disclosure.render())
        self.assertIn("does not find or remove", disclosure.render())
        self.assertIn("cannot verify", disclosure.render())
        self.assertNotIn("secret-test-key", disclosure.render())
        self.assertNotIn("secret-test-key", repr(disclosure))
        self.assertNotIn("api.openai.com", repr(disclosure))
        with self.assertRaises(ProviderApprovalRequired):
            authorize_provider_request(settings, _requirements(), None)
        with self.assertRaises(ProviderApprovalRequired):
            record_provider_approval(disclosure, approved=False)

        approval = record_provider_approval(disclosure, approved=True)
        self.assertIsNotNone(approval)
        self.assertEqual(
            authorize_provider_request(settings, _requirements(), approval),
            disclosure,
        )
        changed_settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "secret-test-key",
                "HARNESS_OPENAI_BASE_URL": "https://api-alt.example/v1",
            }
        )
        with self.assertRaises(ProviderApprovalRequired):
            authorize_provider_request(changed_settings, _requirements(), approval)

    def test_authorized_factory_checks_before_creating_external_model(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
            }
        )
        disclosure = describe_provider(settings)
        approval = record_provider_approval(disclosure, approved=True)

        with self.assertRaises(ProviderApprovalRequired):
            create_authorized_chat_model(settings, _requirements(), None)

        model = create_authorized_chat_model(settings, _requirements(), approval)
        self.assertEqual(type(model).__name__, "ChatOpenAI")

    def test_interview_model_factory_requires_its_own_external_approval(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
            }
        )
        approval = record_provider_approval(
            describe_provider(settings), approved=True
        )

        with self.assertRaises(ProviderApprovalRequired):
            create_authorized_interview_chat_model(settings, None)

        model = create_authorized_interview_chat_model(settings, approval)
        self.assertEqual(type(model).__name__, "ChatOpenAI")

    def test_remote_ollama_requires_approval(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "ollama",
                "HARNESS_OLLAMA_BASE_URL": "https://ollama.example",
            }
        )
        disclosure = describe_provider(settings)

        self.assertTrue(disclosure.requires_approval)
        self.assertIn("remote endpoint", disclosure.provider_name)
        approval = record_provider_approval(disclosure, approved=True)
        self.assertEqual(
            authorize_provider_request(settings, _requirements(), approval),
            disclosure,
        )

    def test_data_science_blocks_provider_request_until_confirmed(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
            }
        )
        draft = _requirements(
            TaskType.DATA_SCIENCE,
            data_source=RequirementValue("customer database", confirmed=False),
            analysis_goal=RequirementValue("measure retention", confirmed=False),
        )
        approval = record_provider_approval(
            describe_provider(settings), approved=True
        )

        with self.assertRaises(RequirementsIncompleteError) as raised:
            authorize_provider_request(settings, draft, approval)

        self.assertIn("Confirm the data source", raised.exception.blockers[0])
        self.assertIn("Confirm the goal", raised.exception.blockers[1])

    def test_missing_task_or_target_blocks_provider_request(self) -> None:
        settings = ProviderSettings.from_environment({})
        draft = RequirementsDraft(request="A request without task choices.")

        with self.assertRaises(RequirementsIncompleteError) as raised:
            authorize_provider_request(settings, draft, None)

        self.assertEqual(len(raised.exception.blockers), 2)


if __name__ == "__main__":
    unittest.main()
