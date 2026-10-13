"""Provider-neutral LangChain chat-model adapters."""

from langchain_core.language_models.chat_models import BaseChatModel

from harness_gen.config import ProviderSettings
from harness_gen.provider_approval import create_authorized_chat_model

__all__ = [
    "BaseChatModel",
    "ProviderSettings",
    "create_authorized_chat_model",
]
