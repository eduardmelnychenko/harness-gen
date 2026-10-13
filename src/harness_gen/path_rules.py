"""Validation for user-confirmed, relative instruction glob patterns."""

from __future__ import annotations

from harness_gen.errors import RequirementsError


def validate_relative_glob(pattern: str) -> str:
    """Validate a relative glob without changing or broadening it."""
    if (
        not pattern
        or pattern != pattern.strip()
        or pattern.startswith("/")
        or "\\" in pattern
        or ":" in pattern
        or any(ord(character) < 32 for character in pattern)
        or any(part in {".", ".."} for part in pattern.split("/"))
    ):
        raise RequirementsError(
            "Path-scoped instructions need an exact relative glob pattern."
        )
    return pattern
