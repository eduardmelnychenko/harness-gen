from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from harness_gen.config import Provider, ProviderSettings
from harness_gen.errors import ConfigurationError

PROVIDER_LABELS = {
    Provider.OLLAMA: "Ollama",
    Provider.OPENAI: "OpenAI",
    Provider.AZURE_OPENAI: "Azure OpenAI",
    Provider.META: "Meta hosted API",
    Provider.GOOGLE_GEMINI: "Google Gemini",
    Provider.ANTHROPIC: "Anthropic Claude",
}
PROVIDER_CHOICES = [
    (item.value, PROVIDER_LABELS[item])
    for item in Provider
]


class ProviderProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provider_profile",
    )
    provider = models.CharField(
        max_length=32,
        choices=PROVIDER_CHOICES,
        default=Provider.OLLAMA.value,
    )
    model_name = models.CharField(max_length=200, blank=True)
    endpoint = models.CharField(max_length=500, blank=True)
    api_version = models.CharField(max_length=100, blank=True)
    encrypted_api_key = models.TextField(blank=True, editable=False)

    class Meta:
        app_label = "webapp"

    def __str__(self) -> str:
        return f"Provider settings for account {self.user_id}"

    def encrypt_api_key(self, secret: str) -> None:
        self.encrypted_api_key = _fernet().encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt_api_key(self) -> str | None:
        if not self.encrypted_api_key:
            return None
        try:
            return _fernet().decrypt(
                self.encrypted_api_key.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            raise ConfigurationError(
                "The saved provider credential cannot be decrypted. "
                "Check the credential encryption key."
            ) from None

    def to_provider_settings(self) -> ProviderSettings:
        secret = self.decrypt_api_key()
        values = {
            "HARNESS_LLM_PROVIDER": self.provider,
            "HARNESS_LLM_MODEL": self.model_name,
        }
        if self.provider == Provider.OLLAMA.value:
            if self.endpoint:
                values["HARNESS_OLLAMA_BASE_URL"] = self.endpoint
        elif self.provider == Provider.OPENAI.value:
            values["OPENAI_API_KEY"] = secret or ""
            if self.endpoint:
                values["HARNESS_OPENAI_BASE_URL"] = self.endpoint
        elif self.provider == Provider.AZURE_OPENAI.value:
            values["AZURE_OPENAI_API_KEY"] = secret or ""
            values["AZURE_OPENAI_ENDPOINT"] = self.endpoint
            values["AZURE_OPENAI_API_VERSION"] = (
                self.api_version or "2024-10-21"
            )
        elif self.provider == Provider.META.value:
            values["LLAMA_API_KEY"] = secret or ""
            if self.endpoint:
                values["HARNESS_META_BASE_URL"] = self.endpoint
        elif self.provider == Provider.GOOGLE_GEMINI.value:
            values["GOOGLE_API_KEY"] = secret or ""
        elif self.provider == Provider.ANTHROPIC.value:
            values["ANTHROPIC_API_KEY"] = secret or ""
        return ProviderSettings.from_environment(values)


def get_provider_profile(user: object) -> ProviderProfile:
    User = get_user_model()
    if not isinstance(user, User):
        raise TypeError("A configured authenticated user is required.")
    profile, _ = ProviderProfile.objects.get_or_create(user=user)
    return profile


def _fernet() -> Fernet:
    key = os.environ.get("HARNESS_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ConfigurationError(
            "Set HARNESS_CREDENTIAL_ENCRYPTION_KEY to configure provider credentials."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise ConfigurationError(
            "HARNESS_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key."
        ) from None
