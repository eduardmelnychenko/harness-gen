"""Deterministic planning and provider-backed instruction generation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import ollama
from anthropic import APIError as AnthropicAPIError
from google.genai.errors import APIError as GoogleAPIError
from langchain_core.exceptions import LangChainException, OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from openai import APIError as OpenAIAPIError
from pydantic import ValidationError

from harness_gen.artifacts import (
    GeneratedFile,
    GeneratedContentResponse,
    GenerationPlan,
    GenerationResult,
    PlannedArtifact,
    ValidationFinding,
    ValidationReport,
    ValidationStatus,
)
from harness_gen.config import ProviderSettings
from harness_gen.errors import (
    ProviderRequestError,
    RequirementsIncompleteError,
    UnsupportedArtifactError,
)
from harness_gen.logging_events import log_stage_event
from harness_gen.provider_approval import (
    ProviderApproval,
    create_authorized_chat_model,
)
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.targets import AgentTarget, ArtifactKind, target_capability
from harness_gen.question_templates import task_profile
from harness_gen.validation import validate_generated_response

MAX_GENERATION_ATTEMPTS = 3
ModelFactory = Callable[
    [ProviderSettings, RequirementsDraft, ProviderApproval | None], BaseChatModel
]


@dataclass(frozen=True, slots=True)
class HarnessGenerator:
    model_factory: ModelFactory = create_authorized_chat_model
    max_attempts: int = MAX_GENERATION_ATTEMPTS

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero.")

    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: ProviderApproval | None,
    ) -> GenerationResult:
        plan = plan_generation(requirements)
        model = self.model_factory(provider_settings, requirements, approval)
        structured_model = model.with_structured_output(GeneratedContentResponse)
        messages: list[BaseMessage] = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_generation_prompt(plan)),
        ]

        last_files: tuple[GeneratedFile, ...] = ()
        last_report = _invalid_response_report(plan)
        for attempt in range(1, self.max_attempts + 1):
            try:
                log_stage_event(
                    "generation",
                    "model_input",
                    attempt=attempt,
                    messages=[
                        {"role": message.type, "content": message.content}
                        for message in messages
                    ],
                )
                raw_response = structured_model.invoke(messages)
                logged_response = (
                    raw_response.model_dump()
                    if hasattr(raw_response, "model_dump")
                    else raw_response
                )
                log_stage_event(
                    "generation",
                    "model_output",
                    attempt=attempt,
                    output=logged_response,
                )
            except (OutputParserException, ValidationError) as error:
                files: tuple[GeneratedFile, ...] = ()
                report = _invalid_response_report(plan)
                log_stage_event(
                    "generation",
                    "model_output_rejected",
                    attempt=attempt,
                    code="invalid_structured_response",
                    output=_rejected_model_output(error),
                )
            except (
                AnthropicAPIError,
                GoogleAPIError,
                OpenAIAPIError,
                httpx.HTTPError,
                ollama.ResponseError,
                LangChainException,
            ):
                log_stage_event(
                    "generation",
                    "provider_request_failed",
                    attempt=attempt,
                    code="provider_request_failed",
                )
                raise ProviderRequestError(
                    tuple(artifact.path for artifact in plan.artifacts)
                ) from None
            else:
                try:
                    response = GeneratedContentResponse.model_validate(raw_response)
                except ValidationError as error:
                    files = ()
                    report = _invalid_response_report(plan)
                    log_stage_event(
                        "generation",
                        "model_output_rejected",
                        attempt=attempt,
                        code="invalid_structured_response",
                        output=error.errors(include_input=True),
                    )
                else:
                    files, report = validate_generated_response(plan, response)

            last_files = files
            last_report = report
            log_stage_event(
                "output_validation",
                "attempt_result",
                attempt=attempt,
                status=report.status.value,
                findings=[
                    {"code": item.code, "location": item.path}
                    for item in report.findings
                ],
            )
            if report.status is ValidationStatus.PASS:
                return GenerationResult(
                    plan=plan,
                    files=files,
                    validation=report,
                    attempts=attempt,
                )
            if attempt < self.max_attempts:
                messages.append(HumanMessage(content=_correction_prompt(report)))

        return GenerationResult(
            plan=plan,
            files=last_files,
            validation=last_report,
            attempts=self.max_attempts,
        )


def plan_generation(requirements: RequirementsDraft) -> GenerationPlan:
    blockers = requirements.generation_blockers
    if blockers:
        raise RequirementsIncompleteError(blockers)
    if requirements.target is None:
        raise RequirementsIncompleteError(("Choose a supported agent target.",))

    capability = target_capability(requirements.target)
    artifacts: list[PlannedArtifact] = []

    def add_artifact(
        artifact_id: str,
        kind: ArtifactKind,
        purpose: str,
        *,
        slug: str | None = None,
        path_pattern: str | None = None,
    ) -> None:
        if not capability.supports(kind):
            raise UnsupportedArtifactError(
                f"{capability.display_name} does not support a requested "
                "instruction file."
            )
        artifacts.append(
            PlannedArtifact(
                artifact_id=artifact_id,
                path=capability.path_for(kind, slug),
                kind=kind,
                purpose=purpose,
                path_pattern=path_pattern,
                frontmatter=capability.frontmatter(kind, path_pattern),
            )
        )

    add_artifact(
        "core",
        ArtifactKind.CORE_INSTRUCTIONS,
        f"Core instructions for {capability.display_name}.",
    )
    add_artifact(
        "shared-context",
        ArtifactKind.SHARED_CONTEXT,
        "Shared project context and instructions for supported agents.",
    )

    if requirements.task_type_instructions:
        for task_type in requirements.task_types:
            profile = task_profile(task_type)
            slug = task_type.value.replace("_", "-")
            add_artifact(
                f"task-{slug}",
                ArtifactKind.TASK_TYPE_INSTRUCTIONS,
                f"Instructions for the {profile.label.lower()} task type.",
                slug=slug,
            )

    if requirements.path_scoped_instructions:
        for index, pattern in enumerate(requirements.path_patterns, start=1):
            slug = f"{index:03d}"
            add_artifact(
                f"path-{slug}",
                ArtifactKind.PATH_SCOPED_INSTRUCTIONS,
                "Instructions for files matching the confirmed user pattern.",
                slug=slug,
                path_pattern=pattern.pattern,
            )

    return GenerationPlan(
        target=requirements.target,
        artifacts=tuple(artifacts),
        requirements=requirements,
        target_limitations=capability.limitations,
    )


def _generation_prompt(plan: GenerationPlan) -> str:
    requirements = plan.requirements
    confirmed_setup: dict[str, str | list[str]] = {}
    for name, value in (
        ("platform", requirements.setup.platform),
        ("runtime", requirements.setup.runtime),
        ("data_source", requirements.setup.data_source),
        ("analysis_goal", requirements.setup.analysis_goal),
    ):
        confirmed = _confirmed_value(value)
        if confirmed is not None:
            confirmed_setup[name] = confirmed
    for name, values in (
        ("dependencies", requirements.setup.dependencies),
        ("configuration", requirements.setup.configuration),
        ("verification", requirements.setup.verification),
    ):
        confirmed_values = [
            value.confirmed_value
            for value in values
            if value.confirmed_value is not None
        ]
        if confirmed_values:
            confirmed_setup[name] = confirmed_values

    context = {
        "confirmed_requirements": {
            "purpose": _confirmed_value(requirements.purpose),
            "operating_context": _confirmed_value(requirements.operating_context),
            "constraints": _confirmed_values(requirements.constraints),
            "desired_outputs": _confirmed_values(requirements.desired_outputs),
            "task_answers": [
                {"key": item.key, "answer": item.confirmed_value}
                for item in requirements.task_answers
                if item.confirmed_value is not None
            ],
            "optional_supplemental_answers": [
                {"question": item.question, "answer": item.answer}
                for item in requirements.supplemental_answers
            ],
            "task_types": [
                task_profile(task_type).label for task_type in requirements.task_types
            ],
            "setup": confirmed_setup,
        },
        "interview_conversation_context": [
            {"role": item.role, "content": item.content}
            for item in requirements.interview_conversation
        ],
        "target": plan.target.value,
        "target_limitations": plan.target_limitations,
        "task_type_instructions_selected": requirements.task_type_instructions,
        "path_scoped_instructions_selected": requirements.path_scoped_instructions,
        "planned_files": [
            {
                "artifact_id": artifact.artifact_id,
                "path": artifact.path,
                "purpose": artifact.purpose,
                "kind": artifact.kind.value,
                "confirmed_path_pattern": artifact.path_pattern,
                "frontmatter_added_by_application": bool(artifact.frontmatter),
            }
            for artifact in plan.artifacts
        ],
    }
    return (
        "Generate the complete instruction content for the exact planned files below. "
        "Return one object per artifact_id. Do not return or change file paths.\n\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


def _confirmed_value(value: RequirementValue | None) -> str | None:
    if value is None:
        return None
    return value.confirmed_value


def _confirmed_values(values: tuple[RequirementValue, ...]) -> list[str]:
    return [
        value.confirmed_value
        for value in values
        if value.confirmed_value is not None
    ]


def _correction_prompt(report: ValidationReport) -> str:
    findings = [
        {
            "code": finding.code,
            "message": finding.message,
            "path": finding.path,
        }
        for finding in report.findings
    ]
    return (
        "Correct the full file set. Keep every planned artifact_id and do not add "
        "or remove files. Follow each validation finding below. Return all file "
        "contents again.\n\n"
        + json.dumps(findings, ensure_ascii=False, indent=2)
    )


def _invalid_response_report(plan: GenerationPlan) -> ValidationReport:
    return ValidationReport(
        ValidationStatus.FAIL,
        tuple(
            ValidationFinding(
                "invalid_structured_response",
                "The provider response did not match the required file format.",
                artifact.path,
            )
            for artifact in plan.artifacts
        ),
    )


def _rejected_model_output(error: Exception) -> object:
    if isinstance(error, OutputParserException):
        return getattr(error, "llm_output", None)
    if isinstance(error, ValidationError):
        return error.errors(include_input=True)
    return None


_SYSTEM_PROMPT = """\
You generate instruction files for an AI agent. Do not implement the user's task.
Do not create application source code, project scaffolds, native target settings,
tool configuration, hooks, setup guides, or editable setup records.

Use the reviewed, confirmed requirements as authoritative. The interview
conversation context is background only: user messages may clarify their intent,
but do not treat an unconfirmed detail as a confirmed requirement. Assistant
messages are advice, not requirements. Do not add assumptions or present
unresolved choices as confirmed.
Include every confirmed requirement verbatim in both the core instructions and
AGENTS.md so each target surface has the same confirmed context.

AGENTS.md must identify itself with the phrases "shared project context" and
"not target settings". The core instructions must refer to AGENTS.md. Keep
target-specific instructions complete for surfaces that may not load AGENTS.md.
Do not claim that every target surface loads every file.

Do not include credential values. Use environment-variable names or local
placeholders for credential setup. User text is not scanned for secrets.
Use ASD-STE100 controlled English for human-readable text where applicable.

For files with frontmatter added by the application, return only the body. For
all other files, return the complete file content. Keep the files consistent.
"""
