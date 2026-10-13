from __future__ import annotations

import unittest

from harness_gen.errors import UnsupportedArtifactError, UnsupportedTargetError
from harness_gen.targets import (
    AgentTarget,
    ArtifactKind,
    parse_agent_target,
    target_capability,
)


class TargetCapabilityTests(unittest.TestCase):
    def test_claude_code_paths_and_path_frontmatter(self) -> None:
        capability = target_capability(AgentTarget.CLAUDE_CODE)

        self.assertEqual(
            capability.path_for(ArtifactKind.CORE_INSTRUCTIONS), "CLAUDE.md"
        )
        self.assertEqual(capability.path_for(ArtifactKind.SHARED_CONTEXT), "AGENTS.md")
        self.assertEqual(
            capability.path_for(ArtifactKind.PATH_SCOPED_INSTRUCTIONS, "python"),
            ".claude/rules/path-python.md",
        )
        self.assertEqual(
            capability.frontmatter(
                ArtifactKind.PATH_SCOPED_INSTRUCTIONS, "src/**/*.py"
            ),
            '---\npaths:\n  - "src/**/*.py"\n---\n',
        )

    def test_copilot_paths_and_frontmatter(self) -> None:
        capability = target_capability(AgentTarget.GITHUB_COPILOT)

        self.assertEqual(
            capability.path_for(ArtifactKind.CORE_INSTRUCTIONS),
            ".github/copilot-instructions.md",
        )
        self.assertEqual(
            capability.path_for(ArtifactKind.TASK_TYPE_INSTRUCTIONS, "data-science"),
            ".github/instructions/task-data-science.instructions.md",
        )
        self.assertEqual(
            capability.frontmatter(ArtifactKind.TASK_TYPE_INSTRUCTIONS),
            '---\napplyTo: "**"\n---\n',
        )
        self.assertEqual(
            capability.frontmatter(
                ArtifactKind.PATH_SCOPED_INSTRUCTIONS, "analysis/**/*.py"
            ),
            '---\napplyTo: "analysis/**/*.py"\n---\n',
        )

    def test_frontmatter_escapes_user_supplied_pattern(self) -> None:
        capability = target_capability(AgentTarget.GITHUB_COPILOT)

        result = capability.frontmatter(
            ArtifactKind.PATH_SCOPED_INSTRUCTIONS,
            'src/"instructions',
        )

        self.assertEqual(result.count("---"), 2)
        self.assertIn(r'src/\"instructions', result)

    def test_parse_supported_targets_and_reject_provider_names(self) -> None:
        self.assertEqual(parse_agent_target("Claude Code"), AgentTarget.CLAUDE_CODE)
        self.assertEqual(
            parse_agent_target("GitHub Copilot"), AgentTarget.GITHUB_COPILOT
        )
        for provider in (
            "Ollama",
            "OpenAI",
            "Google Gemini",
            "Anthropic Claude",
            "Meta's hosted API",
        ):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(UnsupportedTargetError, "provider"):
                    parse_agent_target(provider)

    def test_unknown_target_error_does_not_echo_input(self) -> None:
        value = "internal-target-value"
        with self.assertRaises(UnsupportedTargetError) as raised:
            parse_agent_target(value)

        self.assertNotIn(value, str(raised.exception))

    def test_target_capabilities_document_limits(self) -> None:
        copilot = target_capability(AgentTarget.GITHUB_COPILOT)
        self.assertTrue(
            any("cloud agent and code review" in item for item in copilot.limitations)
        )
        claude = target_capability(AgentTarget.CLAUDE_CODE)
        self.assertTrue(any("not as enforced" in item for item in claude.limitations))

    def test_scoped_path_requires_pattern(self) -> None:
        capability = target_capability(AgentTarget.CLAUDE_CODE)

        with self.assertRaises(UnsupportedArtifactError):
            capability.frontmatter(ArtifactKind.PATH_SCOPED_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
