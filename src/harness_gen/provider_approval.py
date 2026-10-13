"""External-provider disclosure and approval policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from langchain_core.language_models.chat_models import BaseChatModel

from harness_gen.config import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_META_BASE_URL,
    Provider,
    ProviderSettings,
    is_loopback_ollama_endpoint,
)
from harness_gen.errors import (
    ProviderApprovalRequired,
    RequirementsIncompleteError,
)
from harness_gen.requirements import RequirementsDraft

_DATA_CATEGORIES = (
    "the initial request",
    "selected choices",
    "follow-up answers",
    "confirmed setup details",
)
_DISPLAY_NAMES = {
    Provider.OLLAMA: "Ollama",
    Provider.OPENAI: "OpenAI",
    Provider.AZURE_OPENAI: "Azure OpenAI",
    Provider.META: "Meta's hosted API",
    Provider.GOOGLE_GEMINI: "Google Gemini",
    Provider.ANTHROPIC: "Anthropic Claude",
}
_DEFAULT_ENDPOINTS = {
    Provider.META: DEFAULT_META_BASE_URL,
    Provider.ANTHROPIC: DEFAULT_ANTHROPIC_BASE_URL,
    Provider.GOOGLE_GEMINI: "https://generativelanguage.googleapis.com",
}


@dataclass(frozen=True, slots=True)
class ProviderDisclosure:
    provider: Provider
    provider_name: str
    endpoint_is_local: bool
    endpoint_host: str = field(repr=False)
    data_categories: tuple[str, ...] = _DATA_CATEGORIES
    _approval_scope: str = field(default="", repr=False)

    @property
    def requires_approval(self) -> bool:
        return not self.endpoint_is_local

    def render(self) -> str:
        content = ", ".join(self.data_categories[:-1])
        content += f", and {self.data_categories[-1]}"
        if self.endpoint_is_local:
            return (
                f"The request will go to your local {self.provider_name} endpoint "
                f"({self.endpoint_host}). "
                f"It can include {content}. Do not enter secret values. "
                "The application does not find or remove secret values from user text. "
                "User text can be saved or included in generated files."
            )
        return (
            f"The request will go to {self.provider_name} ({self.endpoint_host}). "
            f"It can include {content}. "
            "Do not enter secret values. The application does not find or remove "
            "secret values from user text. This text can be saved, sent to this "
            "provider, or included in generated files. The application cannot verify "
            "how the provider stores or removes data. Read the provider's terms before "
            "you approve."
        )


@dataclass(frozen=True, slots=True)
class ProviderApproval:
    provider: Provider
    _scope: str = field(repr=False)


def describe_provider(settings: ProviderSettings) -> ProviderDisclosure:
    endpoint_is_local = (
        settings.provider is Provider.OLLAMA
        and settings.base_url is not None
        and is_loopback_ollama_endpoint(settings.base_url)
    )
    endpoint = settings.base_url or _DEFAULT_ENDPOINTS.get(settings.provider, "")
    endpoint_host = urlsplit(endpoint).hostname or "configured endpoint"
    scope_material = f"{settings.provider.value}\0{endpoint}".encode("utf-8")
    scope = hashlib.sha256(scope_material).hexdigest()
    provider_name = _DISPLAY_NAMES[settings.provider]
    if settings.provider is Provider.OLLAMA and not endpoint_is_local:
        provider_name = "Ollama at a remote endpoint"
    return ProviderDisclosure(
        provider=settings.provider,
        provider_name=provider_name,
        endpoint_is_local=endpoint_is_local,
        endpoint_host=endpoint_host,
        _approval_scope=scope,
    )


def record_provider_approval(
    disclosure: ProviderDisclosure, approved: bool
) -> ProviderApproval | None:
    if not disclosure.requires_approval:
        return None
    if not approved:
        raise ProviderApprovalRequired()
    return ProviderApproval(disclosure.provider, disclosure._approval_scope)


def authorize_provider_request(
    settings: ProviderSettings,
    requirements: RequirementsDraft,
    approval: ProviderApproval | None,
) -> ProviderDisclosure:
    blockers = requirements.provider_interview_blockers
    if blockers:
        raise RequirementsIncompleteError(blockers)

    disclosure = describe_provider(settings)
    if disclosure.requires_approval and (
        approval is None
        or approval.provider is not disclosure.provider
        or approval._scope != disclosure._approval_scope
    ):
        raise ProviderApprovalRequired()
    return disclosure


def create_authorized_chat_model(
    settings: ProviderSettings,
    requirements: RequirementsDraft,
    approval: ProviderApproval | None,
) -> BaseChatModel:
    """Create a chat model only after its provider and request are authorized."""
    authorize_provider_request(settings, requirements, approval)
    from harness_gen.providers.adapters import _build_chat_model

    return _build_chat_model(settings)
