"""Typed planning and validation records for generated instruction files."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from harness_gen.requirements import RequirementsDraft
from harness_gen.targets import AgentTarget, ArtifactKind


@dataclass(frozen=True, slots=True)
class PlannedArtifact:
    artifact_id: str
    path: str
    kind: ArtifactKind
    purpose: str
    path_pattern: str | None = field(default=None, repr=False)
    frontmatter: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    target: AgentTarget
    artifacts: tuple[PlannedArtifact, ...]
    requirements: RequirementsDraft = field(repr=False)
    target_limitations: tuple[str, ...] = ()


class ArtifactBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_id: str
    content: str


class GeneratedContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    files: list[ArtifactBody]


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    artifact_id: str
    path: str
    kind: ArtifactKind
    purpose: str
    content: str = field(repr=False)


class ValidationStatus(StrEnum):
    PASS = "pass"
    INCOMPLETE = "incomplete"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    status: ValidationStatus
    findings: tuple[ValidationFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationResult:
    plan: GenerationPlan
    files: tuple[GeneratedFile, ...]
    validation: ValidationReport
    attempts: int

    @property
    def writable(self) -> bool:
        return self.validation.status is ValidationStatus.PASS
