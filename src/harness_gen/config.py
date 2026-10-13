"""Runtime configuration for the application and LLM providers."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import load_dotenv

from harness_gen.errors import ConfigurationError

DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_META_BASE_URL = "https://api.llama.com/compat/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_AZURE_API_VERSION = "2024-10-21"


class Provider(StrEnum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    META = "meta"
    GOOGLE_GEMINI = "google_gemini"
    ANTHROPIC = "anthropic"


_CREDENTIAL_ENV_NAMES = {
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.AZURE_OPENAI: "AZURE_OPENAI_API_KEY",
    Provider.META: "LLAMA_API_KEY",
    Provider.GOOGLE_GEMINI: "GOOGLE_API_KEY",
    Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
}


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    provider: Provider
    model: str = field(repr=False)
    base_url: str | None = field(default=None, repr=False)
    api_version: str | None = None
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider):
            raise ConfigurationError("The configured model provider is unsupported.")
        if not self.model.strip():
            raise ConfigurationError("A model or deployment name must be configured.")
        if self.provider is not Provider.OLLAMA:
            credential_name = _CREDENTIAL_ENV_NAMES[self.provider]
            if self.api_key is None or not self.api_key.strip():
                raise ConfigurationError(
                    f"Set {credential_name} in the runtime environment "
                    "for the selected provider."
                )
        if self.provider in {
            Provider.OLLAMA,
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.META,
            Provider.ANTHROPIC,
        }:
            if self.base_url is None:
                raise ConfigurationError(
                    "The configured provider endpoint is missing."
                )
            object.__setattr__(
                self,
                "base_url",
                _validated_endpoint(self.base_url, "Configured provider endpoint"),
            )
        if self.provider is Provider.AZURE_OPENAI:
            if self.api_version is None or not self.api_version.strip():
                raise ConfigurationError(
                    "AZURE_OPENAI_API_VERSION must be configured."
                )
            object.__setattr__(self, "api_version", self.api_version.strip())

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> ProviderSettings:
        if environ is None:
            load_dotenv(override=False)
            values = os.environ
        else:
            values = environ

        provider_value = values.get("HARNESS_LLM_PROVIDER", Provider.OLLAMA.value)
        try:
            provider = Provider(provider_value.strip().lower())
        except ValueError:
            choices = ", ".join(item.value for item in Provider)
            raise ConfigurationError(
                f"HARNESS_LLM_PROVIDER must be one of: {choices}."
            ) from None

        model = values.get("HARNESS_LLM_MODEL", "").strip()
        if not model and provider is Provider.OLLAMA:
            model = DEFAULT_OLLAMA_MODEL
        if not model:
            raise ConfigurationError(
                "Set HARNESS_LLM_MODEL to a model or deployment for the "
                "selected provider."
            )

        if provider is Provider.OLLAMA:
            base_url = _validated_endpoint(
                values.get("HARNESS_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
                "HARNESS_OLLAMA_BASE_URL",
            )
            return cls(provider=provider, model=model, base_url=base_url)

        if provider is Provider.OPENAI:
            return cls(
                provider=provider,
                model=model,
                base_url=_validated_endpoint(
                    values.get("HARNESS_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
                    "HARNESS_OPENAI_BASE_URL",
                ),
                api_key=_required_secret(values, "OPENAI_API_KEY"),
            )

        if provider is Provider.AZURE_OPENAI:
            endpoint = values.get("AZURE_OPENAI_ENDPOINT", "")
            if not endpoint.strip():
                raise ConfigurationError(
                    "Set AZURE_OPENAI_ENDPOINT for the selected Azure OpenAI provider."
                )
            api_version = values.get(
                "AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION
            ).strip()
            if not api_version:
                raise ConfigurationError(
                    "AZURE_OPENAI_API_VERSION must not be empty."
                )
            return cls(
                provider=provider,
                model=model,
                base_url=_validated_endpoint(endpoint, "AZURE_OPENAI_ENDPOINT"),
                api_version=api_version,
                api_key=_required_secret(values, "AZURE_OPENAI_API_KEY"),
            )

        if provider is Provider.META:
            return cls(
                provider=provider,
                model=model,
                base_url=_validated_endpoint(
                    values.get("HARNESS_META_BASE_URL", DEFAULT_META_BASE_URL),
                    "HARNESS_META_BASE_URL",
                ),
                api_key=_required_secret(values, "LLAMA_API_KEY"),
            )

        if provider is Provider.GOOGLE_GEMINI:
            return cls(
                provider=provider,
                model=model,
                api_key=_required_secret(values, "GOOGLE_API_KEY"),
            )

        return cls(
            provider=provider,
            model=model,
            base_url=DEFAULT_ANTHROPIC_BASE_URL,
            api_key=_required_secret(values, "ANTHROPIC_API_KEY"),
        )


def _required_secret(environ: Mapping[str, str], name: str) -> str:
    secret = environ.get(name, "").strip()
    if not secret:
        raise ConfigurationError(
            f"Set {name} in the runtime environment for the selected provider."
        )
    return secret


def _validated_endpoint(value: str, variable_name: str) -> str:
    if not value or value != value.strip() or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        raise ConfigurationError(
            f"{variable_name} must be a valid HTTP or HTTPS endpoint URL."
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfigurationError(
            f"{variable_name} must be a valid HTTP or HTTPS endpoint URL."
        ) from None

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
        or "?" in value
        or "#" in value
    ):
        raise ConfigurationError(
            f"{variable_name} must be a valid HTTP or HTTPS endpoint URL "
            "without user information, a query, or a fragment."
        )
    return value.rstrip("/")


def is_loopback_ollama_endpoint(endpoint: str) -> bool:
    """Return whether an Ollama URL uses an explicit loopback host."""
    try:
        host = urlsplit(_validated_endpoint(endpoint, "Ollama endpoint")).hostname
        if host is None:
            return False
        normalized_host = host.rstrip(".").lower()
        if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
            return True
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False
