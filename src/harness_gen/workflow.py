"""Typed LangGraph orchestration for reviewed and approved harness generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from harness_gen.artifact_manager import (
    apply_artifact_review,
    deserialize_artifact_review,
    format_artifact_review,
    prepare_artifact_review,
    record_write_approval,
    serialize_artifact_review,
)
from harness_gen.artifacts import (
    GeneratedFile,
    GenerationResult,
    ValidationFinding,
    ValidationReport,
    ValidationStatus,
)
from harness_gen.config import ProviderSettings
from harness_gen.errors import (
    HarnessError,
    PartialArtifactWriteError,
    RequirementsError,
    StaleArtifactReview,
    UnvalidatedArtifactsError,
)
from harness_gen.generation import HarnessGenerator, plan_generation
from harness_gen.provider_approval import (
    ProviderApproval,
    describe_provider,
    record_provider_approval,
)
from harness_gen.requirements import (
    RequirementsDraft,
    requirements_from_state,
    requirements_to_state,
)
from harness_gen.targets import ArtifactKind
from harness_gen.validation import validate_generated_files


class ProviderApprovalState(TypedDict):
    provider: str
    endpoint_fingerprint: str
    approved: bool


class ProviderDisclosureState(TypedDict):
    provider: str
    endpoint_fingerprint: str
    disclosure: str
    endpoint_is_local: bool


class HarnessWorkflowState(TypedDict, total=False):
    request: str
    interview_data: dict[str, object]
    requirements: dict[str, object]
    task_types: list[str]
    target_agent: str
    task_type_instructions: bool
    path_scoped_instructions: bool
    path_patterns: list[str]
    unresolved_choices: list[str]
    requirements_reviewed: bool
    requirements_review_fingerprint: str | None
    provider_pending: ProviderDisclosureState | None
    provider_approval: ProviderApprovalState | None
    phase: str
    workflow_enabled: bool
    planned_artifacts: list[dict[str, object]]
    generated_files: list[dict[str, str]]
    generation_attempts: int
    validation_status: str | None
    validation_findings: list[dict[str, object]]
    destination: str
    artifact_review: dict[str, object] | None
    review_summary: list[str]
    write_approved: bool
    write_approval_fingerprint: str | None
    write_result: dict[str, object] | None
    pending_prompt: dict[str, object] | None
    last_response: object
    outcome: str
    error_message: str


class GenerationRunner(Protocol):
    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: ProviderApproval | None,
    ) -> GenerationResult: ...


def build_workflow_graph(
    checkpointer: BaseCheckpointSaver,
    provider_settings: ProviderSettings | None = None,
    generator: GenerationRunner | None = None,
) -> CompiledStateGraph[
    HarnessWorkflowState,
    object,
    HarnessWorkflowState,
    HarnessWorkflowState,
]:
    """Compile the guarded harness workflow with resumable human decisions."""
    generation_runner = generator or HarnessGenerator()
    builder = StateGraph(HarnessWorkflowState)
    builder.add_node("checkpoint", _checkpoint)
    builder.add_node("await_user_input", _await_user_input)
    builder.add_node(
        "requirements_review",
        _requirements_review,
    )
    builder.add_node(
        "provider_approval",
        lambda state: _prepare_provider_approval(state, provider_settings),
    )
    builder.add_node(
        "provider_approval_wait",
        lambda state: _wait_for_provider_approval(state, provider_settings),
    )
    builder.add_node(
        "generation",
        lambda state: _generate(
            state,
            provider_settings,
            generation_runner,
        ),
    )
    builder.add_node("validation", _validate_generation)
    builder.add_node("destination_selection", _select_destination)
    builder.add_node("artifact_review", _review_artifacts)
    builder.add_node("write_approval", _approve_write)
    builder.add_node("apply", _apply_write)
    builder.add_node("done", _checkpoint)
    builder.add_node("invalid_phase", _invalid_phase)
    builder.add_conditional_edges(START, _entry_node, _phase_routes())
    for node in (
        "requirements_review",
        "provider_approval",
        "provider_approval_wait",
        "generation",
        "validation",
        "destination_selection",
        "artifact_review",
        "write_approval",
        "apply",
    ):
        builder.add_conditional_edges(node, _next_node, _phase_routes())
    builder.add_edge("checkpoint", END)
    builder.add_edge("await_user_input", END)
    builder.add_edge("done", END)
    builder.add_edge("invalid_phase", END)
    return builder.compile(checkpointer=checkpointer)


def _phase_routes() -> dict[str, str]:
    return {
        "checkpoint": "checkpoint",
        "user_input": "await_user_input",
        "requirements_review": "requirements_review",
        "provider_approval": "provider_approval",
        "provider_approval_wait": "provider_approval_wait",
        "generation": "generation",
        "validation": "validation",
        "destination_selection": "destination_selection",
        "artifact_review": "artifact_review",
        "write_approval": "write_approval",
        "apply": "apply",
        "done": "done",
        "invalid_phase": "invalid_phase",
    }


def _entry_node(state: HarnessWorkflowState) -> str:
    phase = state.get("phase", "checkpoint")
    return phase if phase in _phase_routes() else "invalid_phase"


def _next_node(state: HarnessWorkflowState) -> str:
    phase = state.get("phase", "invalid_phase")
    return phase if phase in _phase_routes() else "invalid_phase"


def _checkpoint(_: HarnessWorkflowState) -> HarnessWorkflowState:
    return {}


def _await_user_input(state: HarnessWorkflowState) -> HarnessWorkflowState:
    prompt = state.get("pending_prompt")
    if prompt is None:
        return {"phase": "checkpoint"}
    response = interrupt(prompt)
    return {
        "pending_prompt": None,
        "last_response": response,
        "phase": "checkpoint",
    }


def _requirements_review(state: HarnessWorkflowState) -> HarnessWorkflowState:
    try:
        requirements = _read_requirements(state)
    except RequirementsError as error:
        return _failure("invalid_requirements", str(error))

    response = interrupt(
        {
            "kind": "requirements_review",
            "message": "Review the requirements. Confirm them or provide corrections.",
            "request": requirements.request,
            "summary": _requirements_summary(requirements),
            "unresolved_choices": list(requirements.unresolved_choices),
            "blockers": list(requirements.generation_blockers),
        }
    )
    if not isinstance(response, Mapping):
        return _failure(
            "requirements_not_confirmed",
            "Confirm or correct the requirements before continuing.",
        )

    corrected_state = response.get("requirements")
    if corrected_state is not None:
        try:
            corrected = requirements_from_state(_state_mapping(corrected_state))
        except (RequirementsError, TypeError):
            return _failure(
                "invalid_requirements",
                "The corrected requirements are invalid. Review and correct them.",
            )
        corrected = _replace_requirements(corrected, requirements_reviewed=False)
        return {
            "request": corrected.request,
            "requirements": requirements_to_state(corrected),
            "requirements_reviewed": False,
            "requirements_review_fingerprint": None,
            "provider_pending": None,
            "provider_approval": None,
            "phase": "requirements_review",
            "outcome": "",
            "error_message": "",
        }

    if response.get("approved") is not True:
        return _failure(
            "requirements_not_confirmed",
            "Confirm the requirements before continuing.",
        )

    confirmed = replace(
        requirements,
        requirements_reviewed=True,
        path_patterns=tuple(
            replace(pattern, confirmed=True)
            for pattern in requirements.path_patterns
        ),
    )
    confirmed_state = requirements_to_state(confirmed)
    return {
        "requirements": confirmed_state,
        "requirements_reviewed": True,
        "requirements_review_fingerprint": _requirements_fingerprint(
            confirmed_state
        ),
        "provider_pending": None,
        "provider_approval": None,
        "phase": "provider_approval",
        "outcome": "",
        "error_message": "",
    }


def _prepare_provider_approval(
    state: HarnessWorkflowState,
    provider_settings: ProviderSettings | None,
) -> HarnessWorkflowState:
    try:
        requirements = _read_reviewed_requirements(state)
    except RequirementsError as error:
        return _failure("requirements_incomplete", str(error))
    if requirements.generation_blockers:
        return _failure(
            "requirements_incomplete",
            "Resolve these requirements before generation: "
            + " ".join(requirements.generation_blockers),
        )
    if provider_settings is None:
        return _failure(
            "provider_configuration_error",
            "Configure the model provider before generation.",
        )

    disclosure = describe_provider(provider_settings)
    if not disclosure.requires_approval:
        return {
            "provider_pending": None,
            "provider_approval": None,
            "phase": "generation",
        }
    return {
        "provider_pending": {
            "provider": disclosure.provider.value,
            "endpoint_fingerprint": disclosure._approval_scope,
            "disclosure": disclosure.render(),
            "endpoint_is_local": disclosure.endpoint_is_local,
        },
        "provider_approval": None,
        "phase": "provider_approval_wait",
    }


def _wait_for_provider_approval(
    state: HarnessWorkflowState,
    provider_settings: ProviderSettings | None,
) -> HarnessWorkflowState:
    pending = state.get("provider_pending")
    if provider_settings is None or pending is None:
        return _failure(
            "provider_approval_expired",
            "Provider approval is no longer available. Review the provider settings.",
        )
    disclosure = describe_provider(provider_settings)
    if (
        pending["provider"] != disclosure.provider.value
        or pending["endpoint_fingerprint"] != disclosure._approval_scope
    ):
        return _failure(
            "provider_approval_expired",
            "The provider settings changed. Review the disclosure and approve again.",
        )

    response = interrupt(
        {
            "kind": "provider_approval",
            "message": pending["disclosure"],
            "provider": pending["provider"],
            "approved": False,
        }
    )
    approved = isinstance(response, Mapping) and response.get("approved") is True
    if not approved:
        return _failure(
            "provider_approval_declined",
            "No provider request was sent. Approve the provider before continuing.",
        )

    approval = record_provider_approval(disclosure, approved=True)
    if approval is None:
        return {
            "provider_approval": None,
            "phase": "generation",
        }
    return {
        "provider_approval": {
            "provider": approval.provider.value,
            "endpoint_fingerprint": approval._scope,
            "approved": True,
        },
        "phase": "generation",
    }


def _generate(
    state: HarnessWorkflowState,
    provider_settings: ProviderSettings | None,
    generator: GenerationRunner,
) -> HarnessWorkflowState:
    if provider_settings is None:
        return _failure(
            "provider_configuration_error",
            "Configure the model provider before generation.",
        )
    try:
        requirements = _read_reviewed_requirements(state)
        approval = _current_provider_approval(state, provider_settings)
        result = generator.generate(requirements, provider_settings, approval)
    except HarnessError as error:
        return _failure("generation_failed", str(error))
    return {
        "planned_artifacts": [
            {
                "artifact_id": item.artifact_id,
                "path": item.path,
                "kind": item.kind.value,
                "purpose": item.purpose,
                "path_pattern": item.path_pattern,
                "frontmatter": item.frontmatter,
            }
            for item in result.plan.artifacts
        ],
        "generated_files": [
            {
                "artifact_id": item.artifact_id,
                "path": item.path,
                "kind": item.kind.value,
                "purpose": item.purpose,
                "content": item.content,
            }
            for item in result.files
        ],
        "generation_attempts": result.attempts,
        "validation_status": result.validation.status.value,
        "validation_findings": _findings_to_state(result.validation.findings),
        "phase": "validation",
        "outcome": "",
        "error_message": "",
    }


def _validate_generation(state: HarnessWorkflowState) -> HarnessWorkflowState:
    if state.get("validation_status") != ValidationStatus.PASS.value:
        return _failure(
            "validation_failed",
            "Generated files did not pass validation. Review the findings.",
        )
    try:
        result = _generation_from_state(state)
    except (HarnessError, KeyError, TypeError, ValueError):
        return _failure(
            "validation_failed",
            "The generated files could not be restored for validation.",
        )
    report = validate_generated_files(result.plan, result.files)
    update: HarnessWorkflowState = {
        "validation_status": report.status.value,
        "validation_findings": _findings_to_state(report.findings),
    }
    if report.status is not ValidationStatus.PASS:
        update.update(
            _failure(
                "validation_failed",
                "Generated files did not pass validation. Review the findings.",
            )
        )
        return update
    update["phase"] = "destination_selection"
    return update


def _select_destination(state: HarnessWorkflowState) -> HarnessWorkflowState:
    response = interrupt(
        {
            "kind": "destination_selection",
            "message": "Select the existing project directory for the generated files.",
        }
    )
    if not isinstance(response, Mapping):
        return _failure(
            "destination_not_selected",
            "Select an existing project directory before continuing.",
        )
    destination = response.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        return _failure(
            "destination_not_selected",
            "Select an existing project directory before continuing.",
        )
    return {
        "destination": destination,
        "phase": "artifact_review",
    }


def _review_artifacts(state: HarnessWorkflowState) -> HarnessWorkflowState:
    try:
        generation = _generation_from_state(state)
        review = prepare_artifact_review(
            Path(state["destination"]),
            generation,
        )
    except (HarnessError, KeyError, TypeError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, HarnessError)
            else "The generated files could not be prepared for review."
        )
        return _failure("artifact_review_failed", message)
    return {
        "artifact_review": serialize_artifact_review(review),
        "review_summary": list(format_artifact_review(review)),
        "phase": "write_approval",
    }


def _approve_write(state: HarnessWorkflowState) -> HarnessWorkflowState:
    try:
        review = deserialize_artifact_review(
            _state_mapping(state["artifact_review"])
        )
    except (HarnessError, KeyError, TypeError, ValueError):
        return _failure(
            "artifact_review_stale",
            "Review the current complete file set again before writing.",
        )
    response = interrupt(
        {
            "kind": "write_approval",
            "message": "Approve this complete create-or-replace set?",
            "files": list(format_artifact_review(review)),
        }
    )
    approved = isinstance(response, Mapping) and response.get("approved") is True
    if not approved:
        return _failure(
            "write_declined",
            "No files were written because approval was not granted.",
        )
    return {
        "write_approved": True,
        "write_approval_fingerprint": review.review_fingerprint,
        "phase": "apply",
    }


def _apply_write(state: HarnessWorkflowState) -> HarnessWorkflowState:
    try:
        review = deserialize_artifact_review(
            _state_mapping(state["artifact_review"])
        )
        if (
            state.get("write_approved") is not True
            or state.get("write_approval_fingerprint")
            != review.review_fingerprint
        ):
            raise StaleArtifactReview()
        generation = _generation_from_state(state)
        approval = record_write_approval(review, approved=True)
        result = apply_artifact_review(review, generation, approval)
    except PartialArtifactWriteError as error:
        return _failure("partial_write", str(error))
    except (HarnessError, KeyError, TypeError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, HarnessError)
            else "The approved files could not be applied."
        )
        return _failure("write_failed", message)
    return {
        "write_result": {
            "destination": str(result.destination),
            "written": [
                {"path": item.path, "action": item.action.value}
                for item in result.written
            ],
            "created_directories": list(result.created_directories),
        },
        "phase": "done",
        "outcome": "complete",
        "error_message": "",
    }


def _current_provider_approval(
    state: HarnessWorkflowState,
    settings: ProviderSettings,
) -> ProviderApproval | None:
    disclosure = describe_provider(settings)
    if not disclosure.requires_approval:
        return None
    approval_state = state.get("provider_approval")
    if (
        approval_state is None
        or approval_state["approved"] is not True
        or approval_state["provider"] != disclosure.provider.value
        or approval_state["endpoint_fingerprint"] != disclosure._approval_scope
    ):
        raise HarnessError(
            "Provider approval is missing or no longer matches the configuration."
        )
    approval = record_provider_approval(disclosure, approved=True)
    if approval is None:
        raise HarnessError("Provider approval is required before generation.")
    return approval


def _generation_from_state(state: HarnessWorkflowState) -> GenerationResult:
    if state.get("validation_status") != ValidationStatus.PASS.value:
        raise UnvalidatedArtifactsError()
    requirements = _read_reviewed_requirements(state)
    plan = plan_generation(requirements)
    file_records = state.get("generated_files")
    if not isinstance(file_records, list):
        raise ValueError("The generated file list is invalid.")
    artifacts_by_id = {item.artifact_id: item for item in plan.artifacts}
    files: list[GeneratedFile] = []
    for record in file_records:
        if not isinstance(record, Mapping):
            raise ValueError("A generated file record is invalid.")
        artifact_id = _required_string(record, "artifact_id")
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            raise ValueError("A generated file is not in the plan.")
        files.append(
            GeneratedFile(
                artifact_id=artifact_id,
                path=_required_string(record, "path"),
                kind=ArtifactKind(_required_string(record, "kind")),
                purpose=_required_string(record, "purpose"),
                content=_required_string(record, "content"),
            )
        )
    attempts = state.get("generation_attempts", 0)
    if type(attempts) is not int or attempts < 1:
        raise ValueError("The generation attempt count is invalid.")
    return GenerationResult(
        plan=plan,
        files=tuple(files),
        validation=ValidationReport(ValidationStatus.PASS),
        attempts=attempts,
    )


def _read_requirements(state: HarnessWorkflowState) -> RequirementsDraft:
    requirements_data = state.get("requirements")
    if not isinstance(requirements_data, Mapping):
        raise RequirementsError("Add structured requirements before continuing.")
    return requirements_from_state(requirements_data)


def _read_reviewed_requirements(
    state: HarnessWorkflowState,
) -> RequirementsDraft:
    requirements = _read_requirements(state)
    fingerprint = state.get("requirements_review_fingerprint")
    if (
        state.get("requirements_reviewed") is not True
        or requirements.requirements_reviewed is not True
        or fingerprint != _requirements_fingerprint(
            requirements_to_state(requirements)
        )
    ):
        raise RequirementsError(
            "Review and confirm the current requirements before generation."
        )
    return requirements


def _requirements_fingerprint(requirements: Mapping[str, object]) -> str:
    encoded = json.dumps(
        requirements,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _requirements_summary(
    requirements: RequirementsDraft,
) -> dict[str, object]:
    values = requirements_to_state(requirements)
    return {
        "purpose": values["purpose"],
        "task_types": values["task_types"],
        "target": values["target"],
        "operating_context": values["operating_context"],
        "task_answers": values["task_answers"],
        "supplemental_answers": values["supplemental_answers"],
        "constraints": values["constraints"],
        "desired_outputs": values["desired_outputs"],
        "setup": values["setup"],
        "task_type_instructions": values["task_type_instructions"],
        "path_scoped_instructions": values["path_scoped_instructions"],
        "path_patterns": values["path_patterns"],
        "custom_task_support": values["custom_task_support"],
    }


def _replace_requirements(
    requirements: RequirementsDraft,
    *,
    requirements_reviewed: bool,
) -> RequirementsDraft:
    return replace(
        requirements,
        requirements_reviewed=requirements_reviewed,
    )


def _findings_to_state(
    findings: tuple[ValidationFinding, ...],
) -> list[dict[str, object]]:
    return [
        {"code": item.code, "message": item.message, "path": item.path}
        for item in findings
    ]


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError("The generated file record is invalid.")
    return value


def _state_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError("Expected a string-keyed mapping.")
    return value


def _failure(outcome: str, message: str) -> HarnessWorkflowState:
    return {
        "phase": "done",
        "outcome": outcome,
        "error_message": message,
    }


def _invalid_phase(_: HarnessWorkflowState) -> HarnessWorkflowState:
    return _failure(
        "invalid_workflow_phase",
        "The session state is invalid. Restart the session before continuing.",
    )
