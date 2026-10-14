"""Optional, provider-backed question suggestions and interview coaching."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable, Mapping, Sequence

import httpx
import ollama
from anthropic import APIError as AnthropicAPIError
from google.genai.errors import APIError as GoogleAPIError
from langchain_core.exceptions import LangChainException, OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIError as OpenAIAPIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError, ProviderRequestError
from harness_gen.provider_approval import (
    ProviderApproval,
    create_authorized_interview_chat_model,
)

MAX_SUGGESTED_QUESTIONS = 3
_MAX_QUESTION_LENGTH = 240
_MAX_COACHING_LENGTH = 3000
_SECRET_VALUE_REQUEST = re.compile(
    r"\b(what|which|provide|enter|share|paste|send|include)\b"
    r".{0,48}\b(password|passphrase|secret|credential|api[ -]?key|"
    r"access[ -]?token|private[ -]?key)\b",
    re.IGNORECASE,
)
ModelFactory = Callable[[ProviderSettings, ProviderApproval | None], BaseChatModel]
_PROVIDER_ERRORS = (
    AnthropicAPIError,
    GoogleAPIError,
    OpenAIAPIError,
    httpx.HTTPError,
    ollama.ResponseError,
    LangChainException,
)


class SuggestedQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    questions: list[str] = Field(max_length=MAX_SUGGESTED_QUESTIONS)


class InterviewAssistant:
    def __init__(
        self,
        model_factory: ModelFactory = create_authorized_interview_chat_model,
    ) -> None:
        self._model_factory = model_factory

    def suggest_questions(
        self,
        settings: ProviderSettings,
        approval: ProviderApproval | None,
        request: str,
        answers: Mapping[str, object],
        deterministic_questions: Sequence[str],
    ) -> tuple[str, ...]:
        model = self._model_factory(settings, approval)
        messages = [
            SystemMessage(
                content=(
                    "Suggest up to three concise, relevant optional interview "
                    "questions that add useful detail. Do not repeat or rephrase "
                    "the deterministic questions. Do not ask for secrets. Return "
                    "an empty list when no useful question remains."
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "request": request,
                        "answers_so_far": answers,
                        "authoritative_questions": list(deterministic_questions),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            structured_model = model.with_structured_output(SuggestedQuestions)
            result = structured_model.invoke(messages)
            response = SuggestedQuestions.model_validate(result)
        except (OutputParserException, ValidationError, TypeError, ValueError):
            raise HarnessError(
                "The model suggestions did not pass validation. "
                "No optional questions were added."
            ) from None
        except _PROVIDER_ERRORS:
            raise ProviderRequestError() from None
        return validate_suggested_questions(
            response.questions, deterministic_questions
        )

    def coach(
        self,
        settings: ProviderSettings,
        approval: ProviderApproval | None,
        request: str,
        active_question: str,
        user_question: str,
    ) -> str:
        model = self._model_factory(settings, approval)
        messages = [
            SystemMessage(
                content=(
                    "Help the user understand how to answer the active "
                    "requirements question. Give concise advice and an example "
                    "structure, not a fabricated answer. Do not answer or confirm "
                    "the question for the user. Do not ask for secret values."
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "harness_request": request,
                        "active_question": active_question,
                        "user_question": user_question,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            result = model.invoke(messages)
        except _PROVIDER_ERRORS:
            raise ProviderRequestError() from None
        content = getattr(result, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise HarnessError(
                "The model did not return usable coaching. Your answer is unchanged."
            )
        advice = content.strip()
        if len(advice) > _MAX_COACHING_LENGTH:
            advice = advice[:_MAX_COACHING_LENGTH].rsplit(" ", 1)[0].rstrip()
        return advice


def validate_suggested_questions(
    questions: Sequence[str],
    deterministic_questions: Sequence[str],
) -> tuple[str, ...]:
    """Reject empty, duplicated, overlong, or deterministic-like suggestions."""
    if len(questions) > MAX_SUGGESTED_QUESTIONS:
        raise HarnessError(
            "The model returned too many optional questions. "
            "No optional questions were added."
        )
    deterministic = tuple(
        _normalized_question(item) for item in deterministic_questions
    )
    suggestions: list[str] = []
    seen: set[str] = set()
    for item in questions:
        if not isinstance(item, str):
            raise HarnessError(
                "The model suggestions did not pass validation. "
                "No optional questions were added."
            )
        question = item.strip()
        normalized = _normalized_question(question)
        if (
            not question
            or len(question) > _MAX_QUESTION_LENGTH
            or question.count("?") != 1
            or not question.endswith("?")
            or _SECRET_VALUE_REQUEST.search(question) is not None
            or normalized in seen
            or any(
                normalized == core
                or difflib.SequenceMatcher(None, normalized, core).ratio() >= 0.82
                for core in deterministic
            )
        ):
            raise HarnessError(
                "The model suggestions did not pass validation. "
                "No optional questions were added."
            )
        seen.add(normalized)
        suggestions.append(question)
    return tuple(suggestions)


def _normalized_question(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
