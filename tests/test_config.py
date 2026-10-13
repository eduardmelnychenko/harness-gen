from __future__ import annotations

import unittest

from harness_gen.config import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_META_BASE_URL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    Provider,
    ProviderSettings,
    is_loopback_ollama_endpoint,
)
from harness_gen.errors import ConfigurationError


class ProviderSettingsTests(unittest.TestCase):
    def test_defaults_to_local_ollama(self) -> None:
        settings = ProviderSettings.from_environment({})

        self.assertEqual(settings.provider, Provider.OLLAMA)
        self.assertEqual(settings.model, DEFAULT_OLLAMA_MODEL)
        self.assertEqual(settings.base_url, DEFAULT_OLLAMA_BASE_URL)

    def test_requires_provider_specific_model_and_credentials(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "HARNESS_LLM_MODEL"):
            ProviderSettings.from_environment({"HARNESS_LLM_PROVIDER": "openai"})

        with self.assertRaisesRegex(ConfigurationError, "OPENAI_API_KEY"):
            ProviderSettings.from_environment(
                {
                    "HARNESS_LLM_PROVIDER": "openai",
                    "HARNESS_LLM_MODEL": "test-model",
                }
            )

    def test_rejects_unknown_provider_without_echoing_value(self) -> None:
        secret_like_value = "do-not-echo-this"
        with self.assertRaises(ConfigurationError) as raised:
            ProviderSettings.from_environment(
                {"HARNESS_LLM_PROVIDER": secret_like_value}
            )

        self.assertNotIn(secret_like_value, str(raised.exception))

    def test_validates_endpoint_without_echoing_it(self) -> None:
        invalid_urls = (
            "https://user:password@example.test/v1",
            "https://example.test/v1?key=secret",
            "https://example.test/v1#token",
            "https://example.test/v1\n",
            "file:///tmp/provider",
        )
        for endpoint in invalid_urls:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ConfigurationError) as raised:
                    ProviderSettings.from_environment(
                        {
                            "HARNESS_OLLAMA_BASE_URL": endpoint,
                        }
                    )
                self.assertNotIn(endpoint, str(raised.exception))

    def test_validates_meta_endpoint_and_secret(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "meta",
                "HARNESS_LLM_MODEL": "llama-model",
                "LLAMA_API_KEY": "private-key",
            }
        )

        self.assertEqual(settings.base_url, DEFAULT_META_BASE_URL)
        self.assertNotIn("private-key", repr(settings))

    def test_runtime_settings_repr_hides_model_and_endpoint_details(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "private-model-name",
                "OPENAI_API_KEY": "private-key",
                "HARNESS_OPENAI_BASE_URL": "https://provider.example/private-path",
            }
        )

        self.assertNotIn("private-model-name", repr(settings))
        self.assertNotIn("private-path", repr(settings))

    def test_uses_validated_openai_endpoint_not_sdk_environment_override(self) -> None:
        settings = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://user:secret@invalid.example",
            }
        )

        self.assertEqual(settings.base_url, DEFAULT_OPENAI_BASE_URL)

    def test_only_loopback_ollama_endpoints_are_local(self) -> None:
        self.assertTrue(is_loopback_ollama_endpoint("http://localhost:11434"))
        self.assertTrue(is_loopback_ollama_endpoint("http://127.0.0.1:11434"))
        self.assertTrue(is_loopback_ollama_endpoint("http://[::1]:11434"))
        self.assertFalse(is_loopback_ollama_endpoint("https://ollama.example"))
        self.assertFalse(is_loopback_ollama_endpoint("http://192.168.1.2:11434"))


if __name__ == "__main__":
    unittest.main()
