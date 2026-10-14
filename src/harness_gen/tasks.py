"""Task type identifiers."""

from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    SOFTWARE_DEVELOPMENT = "software_development"
    MACHINE_LEARNING = "machine_learning"
    DATA_SCIENCE = "data_science"
    CUSTOM = "custom"
