"""Supported agent targets and their instruction artifact capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from harness_gen.config import Provider
from harness_gen.errors import UnsupportedArtifactError, UnsupportedTargetError
from harness_gen.path_rules import validate_relative_glob


class AgentTarget(StrEnum):
    CLAUDE_CODE = "claude_code"
    GITHUB_COPILOT = "github_copilot"


class ArtifactKind(StrEnum):
    CORE_INSTRUCTIONS = "core_instructions"
    SHARED_CONTEXT = "shared_context"
    TASK_TYPE_INSTRUCTIONS = "task_type_instructions"
    PATH_SCOPED_INSTRUCTIONS = "path_scoped_instructions"


_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROVIDER_NAMES = {provider.value for provider in Provider} | {
    "anthropic_claude",
    "claude",
    "google",
    "meta_hosted_api",
    "meta's_hosted_api",
}


@dataclass(frozen=True, slots=True)
class TargetCapability:
    target: AgentTarget
    display_name: str
    core_instruction_path: str
    shared_context_path: str
    task_instruction_path_template: str
    path_instruction_path_template: str
    supported_artifacts: frozenset[ArtifactKind]
    limitations: tuple[str, ...]

    def supports(self, artifact: ArtifactKind) -> bool:
        return artifact in self.supported_artifacts

    def path_for(self, artifact: ArtifactKind, slug: str | None = None) -> str:
        if not self.supports(artifact):
            raise UnsupportedArtifactError(
                f"{self.display_name} does not support the requested instruction file."
            )
        if artifact is ArtifactKind.CORE_INSTRUCTIONS:
            return self.core_instruction_path
        if artifact is ArtifactKind.SHARED_CONTEXT:
            return self.shared_context_path
        if slug is None or not _SAFE_SLUG.fullmatch(slug):
            raise UnsupportedArtifactError(
                "A safe file name is required for a scoped instruction file."
            )
        template = (
            self.task_instruction_path_template
            if artifact is ArtifactKind.TASK_TYPE_INSTRUCTIONS
            else self.path_instruction_path_template
        )
        return template.format(slug=slug)

    def frontmatter(
        self,
        artifact: ArtifactKind,
        path_pattern: str | None = None,
    ) -> str:
        if not self.supports(artifact):
            raise UnsupportedArtifactError(
                f"{self.display_name} does not support the requested instruction file."
            )
        if artifact is ArtifactKind.PATH_SCOPED_INSTRUCTIONS:
            if path_pattern is None:
                raise UnsupportedArtifactError(
                    "A confirmed path pattern is required for a path-scoped file."
                )
            pattern = validate_relative_glob(path_pattern)
            quoted_pattern = json.dumps(pattern)
            if self.target is AgentTarget.CLAUDE_CODE:
                return f"---\npaths:\n  - {quoted_pattern}\n---\n"
            return f"---\napplyTo: {quoted_pattern}\n---\n"
        if artifact is ArtifactKind.TASK_TYPE_INSTRUCTIONS:
            if self.target is AgentTarget.GITHUB_COPILOT:
                return '---\napplyTo: "**"\n---\n'
            return ""
        return ""


_ALL_ARTIFACTS = frozenset(ArtifactKind)

TARGET_CAPABILITIES = MappingProxyType(
    {
        AgentTarget.CLAUDE_CODE: TargetCapability(
            target=AgentTarget.CLAUDE_CODE,
            display_name="Claude Code",
            core_instruction_path="CLAUDE.md",
            shared_context_path="AGENTS.md",
            task_instruction_path_template=".claude/rules/task-{slug}.md",
            path_instruction_path_template=".claude/rules/path-{slug}.md",
            supported_artifacts=_ALL_ARTIFACTS,
            limitations=(
                "Claude Code treats instruction files as context, not as enforced "
                "settings.",
                "Offline checks cannot prove which files a target session will load.",
            ),
        ),
        AgentTarget.GITHUB_COPILOT: TargetCapability(
            target=AgentTarget.GITHUB_COPILOT,
            display_name="GitHub Copilot",
            core_instruction_path=".github/copilot-instructions.md",
            shared_context_path="AGENTS.md",
            task_instruction_path_template=(
                ".github/instructions/task-{slug}.instructions.md"
            ),
            path_instruction_path_template=(
                ".github/instructions/path-{slug}.instructions.md"
            ),
            supported_artifacts=_ALL_ARTIFACTS,
            limitations=(
                "On GitHub.com, path-specific instructions are supported only by "
                "Copilot cloud agent and code review.",
                "Other Copilot surfaces may not load every supported instruction file.",
            ),
        ),
    }
)


def parse_agent_target(value: str) -> AgentTarget:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _PROVIDER_NAMES:
        raise UnsupportedTargetError(
            "That name is an LLM provider, not an agent target. Choose Claude Code "
            "or GitHub Copilot."
        )
    try:
        return AgentTarget(normalized)
    except ValueError:
        raise UnsupportedTargetError(
            "Choose a supported agent target: Claude Code or GitHub Copilot."
        ) from None


def target_capability(target: AgentTarget) -> TargetCapability:
    return TARGET_CAPABILITIES[target]
