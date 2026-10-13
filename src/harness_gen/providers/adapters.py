"""Direct provider adapters behind the shared LangChain chat-model contract."""

from __future__ import annotations

from collections.abc import Callable

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from harness_gen.config import Provider, ProviderSettings
from harness_gen.errors import ConfigurationError


ChatModelBuilder = Callable[[ProviderSettings], BaseChatModel]


def _build_ollama(settings: ProviderSettings) -> BaseChatModel:
    return ChatOllama(model=settings.model, base_url=settings.base_url)


def _build_openai(settings: ProviderSettings) -> BaseChatModel:
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def _build_azure_openai(settings: ProviderSettings) -> BaseChatModel:
    return AzureChatOpenAI(
        azure_deployment=settings.model,
        api_version=settings.api_version,
        azure_endpoint=settings.base_url,
        api_key=settings.api_key,
    )


def _build_meta(settings: ProviderSettings) -> BaseChatModel:
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def _build_google_gemini(settings: ProviderSettings) -> BaseChatModel:
    return ChatGoogleGenerativeAI(
        model=settings.model,
        google_api_key=settings.api_key,
    )


def _build_anthropic(settings: ProviderSettings) -> BaseChatModel:
    return ChatAnthropic(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


_BUILDERS: dict[Provider, ChatModelBuilder] = {
    Provider.OLLAMA: _build_ollama,
    Provider.OPENAI: _build_openai,
    Provider.AZURE_OPENAI: _build_azure_openai,
    Provider.META: _build_meta,
    Provider.GOOGLE_GEMINI: _build_google_gemini,
    Provider.ANTHROPIC: _build_anthropic,
}


def _build_chat_model(settings: ProviderSettings) -> BaseChatModel:
    """Build the configured model after the caller passes the approval gate."""
    try:
        builder = _BUILDERS[settings.provider]
    except KeyError:
        raise ConfigurationError(
            "The configured model provider is unsupported."
        ) from None
    return builder(settings)
