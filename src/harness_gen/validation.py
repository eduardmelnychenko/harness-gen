"""Offline checks for planned and generated instruction artifacts."""

from __future__ import annotations

import re
from collections import Counter

from harness_gen.artifacts import (
    GeneratedContentResponse,
    GeneratedFile,
    GenerationPlan,
    PlannedArtifact,
    ValidationFinding,
    ValidationReport,
    ValidationStatus,
)
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.targets import ArtifactKind


def is_safe_output_path(path: str) -> bool:
    if (
        not path
        or path != path.strip()
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(ord(character) < 32 for character in path)
    ):
        return False
    parts = path.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def confirmed_details(requirements: RequirementsDraft) -> tuple[str, ...]:
    values: list[RequirementValue | None] = [
        requirements.purpose,
        requirements.operating_context,
        requirements.setup.platform,
        requirements.setup.runtime,
        requirements.setup.data_source,
        requirements.setup.analysis_goal,
    ]
    values.extend(requirements.constraints)
    values.extend(requirements.desired_outputs)
    values.extend(
        RequirementValue(value=item.confirmed_value, confirmed=True)
        for item in requirements.task_answers
        if item.confirmed_value is not None
    )
    values.extend(requirements.setup.dependencies)
    values.extend(requirements.setup.configuration)
    values.extend(requirements.setup.verification)

    details: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item is None or item.confirmed_value is None:
            continue
        if item.confirmed_value not in seen:
            details.append(item.confirmed_value)
            seen.add(item.confirmed_value)
    return tuple(details)


def validate_generated_response(
    plan: GenerationPlan,
    response: GeneratedContentResponse,
) -> tuple[tuple[GeneratedFile, ...], ValidationReport]:
    findings: list[ValidationFinding] = []
    if not plan.artifacts:
        findings.append(
            ValidationFinding(
                "empty_generation_plan",
                "The generation plan must contain core instructions and shared "
                "context.",
            )
        )
    expected: dict[str, PlannedArtifact] = {}
    plan_paths: set[str] = set()
    plan_kinds: set[ArtifactKind] = set()
    for artifact in plan.artifacts:
        if artifact.artifact_id in expected:
            findings.append(
                ValidationFinding(
                    "duplicate_planned_id",
                    "The generation plan contains a duplicate artifact identifier.",
                    artifact.path,
                )
            )
        expected[artifact.artifact_id] = artifact
        plan_kinds.add(artifact.kind)
        if not is_safe_output_path(artifact.path):
            findings.append(
                ValidationFinding(
                    "unsafe_planned_path",
                    "The generation plan contains an unsafe output path.",
                    artifact.path,
                )
            )
        if artifact.path in plan_paths:
            findings.append(
                ValidationFinding(
                    "duplicate_planned_path",
                    "The generation plan contains duplicate output paths.",
                    artifact.path,
                )
            )
        plan_paths.add(artifact.path)
    for required_kind in (
        ArtifactKind.CORE_INSTRUCTIONS,
        ArtifactKind.SHARED_CONTEXT,
    ):
        if required_kind not in plan_kinds:
            findings.append(
                ValidationFinding(
                    "missing_required_plan_artifact",
                    "The generation plan must include core instructions and shared "
                    "context.",
                )
            )

    counts = Counter(item.artifact_id for item in response.files)
    for artifact_id, count in counts.items():
        if artifact_id not in expected:
            findings.append(
                ValidationFinding(
                    "unexpected_artifact",
                    "The provider returned an artifact that was not requested.",
                )
            )
        elif count > 1:
            findings.append(
                ValidationFinding(
                    "duplicate_artifact",
                    "The provider returned an artifact more than once.",
                    expected[artifact_id].path,
                )
            )

    files: list[GeneratedFile] = []
    for artifact in plan.artifacts:
        matching = [
            item
            for item in response.files
            if item.artifact_id == artifact.artifact_id
        ]
        if not matching:
            findings.append(
                ValidationFinding(
                    "missing_artifact",
                    "The provider did not return a required instruction file.",
                    artifact.path,
                )
            )
            continue

        body = matching[0].content
        if artifact.frontmatter and body.lstrip().startswith("---"):
            findings.append(
                ValidationFinding(
                    "duplicate_frontmatter",
                    "Return the instruction body without a frontmatter block.",
                    artifact.path,
                )
            )
        content = artifact.frontmatter + body
        files.append(
            GeneratedFile(
                artifact_id=artifact.artifact_id,
                path=artifact.path,
                kind=artifact.kind,
                purpose=artifact.purpose,
                content=content,
            )
        )

    findings.extend(validate_generated_files(plan, tuple(files)).findings)
    status = _status_for(findings)
    return tuple(files), ValidationReport(status=status, findings=_unique(findings))


def validate_generated_files(
    plan: GenerationPlan,
    files: tuple[GeneratedFile, ...],
) -> ValidationReport:
    findings: list[ValidationFinding] = []
    expected = {item.artifact_id: item for item in plan.artifacts}
    planned_id_counts = Counter(item.artifact_id for item in plan.artifacts)
    planned_kinds = {item.kind for item in plan.artifacts}
    planned_paths: set[str] = set()
    if not plan.artifacts:
        findings.append(
            ValidationFinding(
                "empty_generation_plan",
                "The generation plan must contain core instructions and shared "
                "context.",
            )
        )
    for artifact in plan.artifacts:
        if planned_id_counts[artifact.artifact_id] > 1:
            findings.append(
                ValidationFinding(
                    "duplicate_planned_id",
                    "The generation plan contains a duplicate artifact identifier.",
                    artifact.path,
                )
            )
        if not is_safe_output_path(artifact.path):
            findings.append(
                ValidationFinding(
                    "unsafe_planned_path",
                    "The generation plan contains an unsafe output path.",
                    artifact.path,
                )
            )
        if artifact.path in planned_paths:
            findings.append(
                ValidationFinding(
                    "duplicate_planned_path",
                    "The generation plan contains duplicate output paths.",
                    artifact.path,
                )
            )
        planned_paths.add(artifact.path)
    if not {
        ArtifactKind.CORE_INSTRUCTIONS,
        ArtifactKind.SHARED_CONTEXT,
    }.issubset(planned_kinds):
        findings.append(
            ValidationFinding(
                "missing_required_plan_artifact",
                "The generation plan must include core instructions and shared "
                "context.",
            )
        )
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    details = confirmed_details(plan.requirements)

    for file in files:
        planned = expected.get(file.artifact_id)
        if planned is None:
            findings.append(
                ValidationFinding(
                    "unexpected_artifact",
                    "The generated file was not in the approved plan.",
                    file.path,
                )
            )
            continue
        if file.artifact_id in seen_ids:
            findings.append(
                ValidationFinding(
                    "duplicate_artifact",
                    "The generated file identifier is not unique.",
                    file.path,
                )
            )
        seen_ids.add(file.artifact_id)

        if not is_safe_output_path(file.path):
            findings.append(
                ValidationFinding(
                    "unsafe_output_path",
                    "The generated file has an unsafe path.",
                    file.path,
                )
            )
        if file.path != planned.path or file.kind is not planned.kind:
            findings.append(
                ValidationFinding(
                    "artifact_plan_mismatch",
                    "The generated file does not match its planned target path or "
                    "type.",
                    planned.path,
                )
            )
        if file.path in seen_paths:
            findings.append(
                ValidationFinding(
                    "duplicate_output_path",
                    "The generated output path is not unique.",
                    file.path,
                )
            )
        seen_paths.add(file.path)
        if not file.content.strip():
            findings.append(
                ValidationFinding(
                    "empty_content",
                    "The generated instruction file is empty.",
                    planned.path,
                )
            )
            continue

        if planned.frontmatter and not file.content.startswith(planned.frontmatter):
            findings.append(
                ValidationFinding(
                    "invalid_frontmatter",
                    "The target-specific frontmatter does not match the confirmed "
                    "path.",
                    planned.path,
                )
            )
        if file.kind in {
            ArtifactKind.CORE_INSTRUCTIONS,
            ArtifactKind.SHARED_CONTEXT,
        }:
            normalized_content = _normalize(file.content)
            for detail in details:
                if _normalize(detail) not in normalized_content:
                    findings.append(
                        ValidationFinding(
                            "missing_confirmed_detail",
                            "A confirmed requirement is missing from a required file.",
                            planned.path,
                        )
                    )
        if file.kind is ArtifactKind.CORE_INSTRUCTIONS and not re.search(
            r"\bAGENTS\.md\b", file.content, re.IGNORECASE
        ):
            findings.append(
                ValidationFinding(
                    "missing_shared_context_reference",
                    "The core instruction file must refer to AGENTS.md.",
                    planned.path,
                )
            )
        if file.kind is ArtifactKind.SHARED_CONTEXT:
            normalized_content = _normalize(file.content)
            if "shared project context" not in normalized_content:
                findings.append(
                    ValidationFinding(
                        "missing_shared_context_label",
                        "AGENTS.md must identify its shared project context.",
                        planned.path,
                    )
                )
            if "not target settings" not in normalized_content:
                findings.append(
                    ValidationFinding(
                        "missing_settings_distinction",
                        "AGENTS.md must state that it is not target settings.",
                        planned.path,
                    )
                )

    for artifact in plan.artifacts:
        if artifact.artifact_id not in seen_ids:
            findings.append(
                ValidationFinding(
                    "missing_artifact",
                    "The generated file set is incomplete.",
                    artifact.path,
                )
            )
    return ValidationReport(
        status=_status_for(findings),
        findings=_unique(findings),
    )


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _status_for(findings: list[ValidationFinding]) -> ValidationStatus:
    if any(
        finding.code
        in {
            "duplicate_planned_id",
            "empty_generation_plan",
            "missing_required_plan_artifact",
            "unsafe_planned_path",
            "duplicate_planned_path",
            "unexpected_artifact",
            "duplicate_artifact",
            "duplicate_frontmatter",
            "unsafe_output_path",
            "artifact_plan_mismatch",
            "duplicate_output_path",
            "invalid_frontmatter",
        }
        for finding in findings
    ):
        return ValidationStatus.FAIL
    if findings:
        return ValidationStatus.INCOMPLETE
    return ValidationStatus.PASS


def _unique(
    findings: list[ValidationFinding],
) -> tuple[ValidationFinding, ...]:
    unique: list[ValidationFinding] = []
    seen: set[tuple[str, str | None]] = set()
    for finding in findings:
        key = finding.code, finding.path
        if key not in seen:
            unique.append(finding)
            seen.add(key)
    return tuple(unique)
