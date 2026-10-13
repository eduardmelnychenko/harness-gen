from __future__ import annotations

import unittest

from harness_gen.config import Provider, ProviderSettings
from harness_gen.provider_approval import (
    create_authorized_chat_model,
    describe_provider,
    record_provider_approval,
)
from harness_gen.providers import adapters
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.targets import AgentTarget
from harness_gen.tasks import TaskType


class ProviderAdapterTests(unittest.TestCase):
    @staticmethod
    def _requirements() -> RequirementsDraft:
        return RequirementsDraft(
            request="Create a test harness.",
            purpose=RequirementValue("Support the test task", confirmed=True),
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.CLAUDE_CODE,
        )

    def test_each_provider_uses_its_direct_adapter(self) -> None:
        cases = (
            (
                Provider.OLLAMA,
                adapters.ChatOllama,
                {"HARNESS_OLLAMA_BASE_URL": "http://localhost:11434"},
            ),
            (Provider.OPENAI, adapters.ChatOpenAI, {"OPENAI_API_KEY": "test-key"}),
            (
                Provider.AZURE_OPENAI,
                adapters.AzureChatOpenAI,
                {
                    "AZURE_OPENAI_API_KEY": "test-key",
                    "AZURE_OPENAI_ENDPOINT": "https://azure.example",
                },
            ),
            (Provider.META, adapters.ChatOpenAI, {"LLAMA_API_KEY": "test-key"}),
            (
                Provider.GOOGLE_GEMINI,
                adapters.ChatGoogleGenerativeAI,
                {"GOOGLE_API_KEY": "test-key"},
            ),
            (
                Provider.ANTHROPIC,
                adapters.ChatAnthropic,
                {"ANTHROPIC_API_KEY": "test-key"},
            ),
        )
        for provider, expected_type, required_environment in cases:
            with self.subTest(provider=provider):
                settings = ProviderSettings.from_environment(
                    {
                        "HARNESS_LLM_PROVIDER": provider.value,
                        "HARNESS_LLM_MODEL": "test-model",
                        **required_environment,
                    }
                )
                disclosure = describe_provider(settings)
                approval = record_provider_approval(
                    disclosure, approved=disclosure.requires_approval
                )
                model = create_authorized_chat_model(
                    settings,
                    self._requirements(),
                    approval,
                )

                self.assertIsInstance(model, expected_type)


if __name__ == "__main__":
    unittest.main()
