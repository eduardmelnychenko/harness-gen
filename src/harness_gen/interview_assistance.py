"""Provider-backed interview assistance and topic interviews."""

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
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from openai import APIError as OpenAIAPIError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError, ProviderRequestError
from harness_gen.logging_events import log_stage_event
from harness_gen.provider_approval import (
    ProviderApproval,
    create_authorized_interview_chat_model,
)
from harness_gen.question_templates import TASK_PROFILES

MAX_SUGGESTED_QUESTIONS = 3
_MAX_QUESTION_LENGTH = 240
_MAX_CHAT_QUESTION_LENGTH = 2000
_MAX_COACHING_LENGTH = 3000
_SECRET_VALUE_REQUEST = re.compile(
    r"\b(what|which|provide|enter|share|paste|send|include)\b"
    r".{0,48}\b(password|passphrase|secret|credential|api[ -]?key|"
    r"access[ -]?token|private[ -]?key)\b",
    re.IGNORECASE,
)
_MAX_TOPIC_QUESTION_LENGTH = 1200
_MAX_EXTRACTED_REQUIREMENTS = 20
_MAX_OPEN_CHOICES = 20
_INTERVIEW_ANSWER_FIELDS = frozenset(
    {
        "purpose",
        "operating_context",
        "constraints",
        "desired_outputs",
        "dependencies",
        "configuration",
        "custom_support",
        *(
            question.key
            for profile in TASK_PROFILES.values()
            for question in profile.questions
        ),
    }
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


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str
    value: str = Field(min_length=1, max_length=5000)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if value not in _INTERVIEW_ANSWER_FIELDS:
            raise ValueError("The model returned an unsupported requirement field.")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A requirement value cannot be empty.")
        return normalized


class TopicInterviewTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sufficient: bool
    next_question: str | None = Field(
        default=None,
        max_length=_MAX_TOPIC_QUESTION_LENGTH,
    )
    extracted_requirements: list[ExtractedRequirement] = Field(
        max_length=_MAX_EXTRACTED_REQUIREMENTS
    )
    unresolved_choices: list[str] = Field(max_length=_MAX_OPEN_CHOICES)

    @model_validator(mode="after")
    def validate_next_step(self) -> TopicInterviewTurn:
        if self.sufficient == (self.next_question is not None):
            raise ValueError(
                "The completion status and next question are inconsistent."
            )
        if len({item.key for item in self.extracted_requirements}) != len(
            self.extracted_requirements
        ):
            raise ValueError("Duplicate extracted requirement fields.")
        return self

    @field_validator("next_question")
    @classmethod
    def validate_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        question = value.strip()
        if (
            not question
            or question.count("?") != 1
            or not question.endswith("?")
            or _SECRET_VALUE_REQUEST.search(question) is not None
        ):
            raise ValueError("The model returned an invalid interview question.")
        return question

    @field_validator("unresolved_choices")
    @classmethod
    def validate_open_choices(cls, values: list[str]) -> list[str]:
        choices = [value.strip() for value in values]
        if any(not value or len(value) > 1000 for value in choices):
            raise ValueError("The model returned an invalid open choice.")
        return choices


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
            _log_model_input("interview_suggestions", messages)
            result = structured_model.invoke(messages)
            _log_model_output("interview_suggestions", result)
            response = SuggestedQuestions.model_validate(result)
        except (OutputParserException, ValidationError, TypeError, ValueError) as error:
            log_stage_event(
                "interview_suggestions",
                "model_output_rejected",
                code="invalid_model_output",
                output=_rejected_model_output(error),
            )
            raise HarnessError(
                "The model suggestions did not pass validation. "
                "No optional questions were added."
            ) from None
        except _PROVIDER_ERRORS:
            log_stage_event(
                "interview_suggestions",
                "provider_request_failed",
                code="provider_request_failed",
            )
            raise ProviderRequestError() from None
        return validate_suggested_questions(
            response.questions, deterministic_questions
        )

    def interview_topic(
        self,
        settings: ProviderSettings,
        approval: ProviderApproval | None,
        request: str,
        task_types: Sequence[str],
        target: str,
        answers: Mapping[str, object],
        conversation: Sequence[Mapping[str, str]],
        mandatory_gaps: Sequence[str],
    ) -> TopicInterviewTurn:
        model = self._model_factory(settings, approval)
        messages = [
            SystemMessage(
                content=(
                    "Conduct a flexible requirements interview by exploring "
                    "useful topics, not by following a fixed questionnaire. Use "
                    "the request and conversation to choose at most one focused "
                    "next question. Set sufficient=true only when the harness "
                    "requirements are clear enough to review and every mandatory "
                    "gap listed in the context is resolved. Otherwise set "
                    "sufficient=false and ask the most useful next question. "
                    "Extract only requirements stated explicitly by the user; "
                    "do not infer or invent confirmed facts. Use only supported "
                    "requirement field keys. Record material unknowns in "
                    "unresolved_choices and ask about them before stopping. "
                    "Use semicolons between multiple values for list-like "
                    "fields. For custom_support, use only an explicit yes, no, "
                    "or unsure from the user. Do not ask for secrets. When "
                    "sufficient=true, next_question must be null; otherwise "
                    "it must contain one concise question ending in '?'."
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "request": request,
                        "selected_task_types": list(task_types),
                        "agent_target": target,
                        "requirements_collected": answers,
                        "conversation": list(conversation),
                        "mandatory_gaps": list(mandatory_gaps),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            structured_model = model.with_structured_output(TopicInterviewTurn)
            _log_model_input("topic_interview", messages)
            result = structured_model.invoke(messages)
            _log_model_output("topic_interview", result)
            response = TopicInterviewTurn.model_validate(result)
        except (OutputParserException, ValidationError, TypeError, ValueError) as error:
            log_stage_event(
                "topic_interview",
                "model_output_rejected",
                code="invalid_model_output",
                output=_rejected_model_output(error),
            )
            raise HarnessError(
                "The model's interview response did not pass validation. "
                "Your answers were not changed."
            ) from None
        except _PROVIDER_ERRORS:
            log_stage_event(
                "topic_interview",
                "provider_request_failed",
                code="provider_request_failed",
            )
            raise ProviderRequestError() from None
        return response

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
            _log_model_input("interview_coaching", messages)
            result = model.invoke(messages)
            _log_model_output("interview_coaching", result)
        except _PROVIDER_ERRORS:
            log_stage_event(
                "interview_coaching",
                "provider_request_failed",
                code="provider_request_failed",
            )
            raise ProviderRequestError() from None
        try:
            advice = _assistant_text(result, "Your answer is unchanged.")
        except HarnessError:
            log_stage_event(
                "interview_coaching",
                "model_output_rejected",
                code="empty_model_output",
            )
            raise
        log_stage_event("interview_coaching", "assistant_response", text=advice)
        return advice

    def answer_question(
        self,
        settings: ProviderSettings,
        approval: ProviderApproval | None,
        request: str,
        active_question: str,
        conversation: Sequence[Mapping[str, str]],
        user_question: str,
    ) -> str:
        question = user_question.strip()
        if not question or len(question) > _MAX_CHAT_QUESTION_LENGTH:
            raise HarnessError("Enter a question of 2,000 characters or fewer.")
        model = self._model_factory(settings, approval)
        messages = [
            SystemMessage(
                content=(
                    "Answer the user's related question about creating their "
                    "agent harness. Use the interview conversation to understand "
                    "the context. Do not change, confirm, or invent answers for "
                    "the user. Distinguish confirmed requirements from open "
                    "choices. Do not ask for secret values. Give concise, useful "
                    "guidance."
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "harness_request": request,
                        "active_interview_question": active_question,
                        "conversation": list(conversation),
                        "user_question": question,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            _log_model_input("interview_chat", messages)
            result = model.invoke(messages)
            _log_model_output("interview_chat", result)
        except _PROVIDER_ERRORS:
            log_stage_event(
                "interview_chat",
                "provider_request_failed",
                code="provider_request_failed",
            )
            raise ProviderRequestError() from None
        try:
            advice = _assistant_text(
                result,
                "The model did not return usable guidance. "
                "Your answers are unchanged.",
            )
        except HarnessError:
            log_stage_event(
                "interview_chat",
                "model_output_rejected",
                code="empty_model_output",
            )
            raise
        log_stage_event("interview_chat", "assistant_response", text=advice)
        return advice


def _log_model_input(stage: str, messages: Sequence[BaseMessage]) -> None:
    log_stage_event(
        stage,
        "model_input",
        messages=[
            {"role": message.type, "content": message.content}
            for message in messages
        ],
    )


def _log_model_output(stage: str, result: object) -> None:
    output = result.model_dump() if isinstance(result, BaseModel) else result
    log_stage_event(stage, "model_output", output=output)


def _rejected_model_output(error: Exception) -> object:
    if isinstance(error, OutputParserException):
        return getattr(error, "llm_output", None)
    if isinstance(error, ValidationError):
        return error.errors(include_input=True)
    return None


def _assistant_text(result: object, error_message: str) -> str:
    content = getattr(result, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise HarnessError(error_message)
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
