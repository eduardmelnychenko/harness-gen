"""Deterministic task profiles and interview question templates."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from harness_gen.tasks import TaskType


@dataclass(frozen=True, slots=True)
class FollowUpQuestion:
    key: str
    prompt: str
    required_before_provider_interview: bool = False
    ask_only_when_relevant: bool = True


@dataclass(frozen=True, slots=True)
class TaskProfile:
    task_type: TaskType
    label: str
    description: str
    questions: tuple[FollowUpQuestion, ...]
    requires_custom_support_assessment: bool = False


TASK_PROFILES = MappingProxyType(
    {
        TaskType.SOFTWARE_DEVELOPMENT: TaskProfile(
            task_type=TaskType.SOFTWARE_DEVELOPMENT,
            label="Software development",
            description=(
                "Build or change software while following the user's project needs."
            ),
            questions=(
                FollowUpQuestion(
                    "workflows",
                    "Which important user or system workflows must the work support?",
                ),
                FollowUpQuestion(
                    "runtime",
                    "Which language or runtime must the work use, if already known?",
                ),
                FollowUpQuestion(
                    "verification",
                    "Which checks must confirm that the work is correct, if known?",
                ),
                FollowUpQuestion(
                    "platform",
                    "Does a platform limit how this work is built or used?",
                ),
            ),
        ),
        TaskType.MACHINE_LEARNING: TaskProfile(
            task_type=TaskType.MACHINE_LEARNING,
            label="Machine learning",
            description="Develop, train, evaluate, or use a machine-learning system.",
            questions=(
                FollowUpQuestion(
                    "ml_objective",
                    "What result must the machine-learning task produce?",
                ),
                FollowUpQuestion(
                    "data_source",
                    "Which data source will the task use, if known?",
                ),
                FollowUpQuestion(
                    "ml_framework",
                    "Which machine-learning framework must the work use, if known?",
                ),
                FollowUpQuestion(
                    "evaluation",
                    "Which measure will show that the model meets its goal?",
                ),
                FollowUpQuestion(
                    "runtime",
                    "Which runtime or hardware limit affects this task, if any?",
                ),
            ),
        ),
        TaskType.DATA_SCIENCE: TaskProfile(
            task_type=TaskType.DATA_SCIENCE,
            label="Data science",
            description="Analyze data and report results that address a clear goal.",
            questions=(
                FollowUpQuestion(
                    "data_source",
                    "Which data source will the analysis use?",
                    required_before_provider_interview=True,
                    ask_only_when_relevant=False,
                ),
                FollowUpQuestion(
                    "analysis_goal",
                    "What question must the analysis answer?",
                    required_before_provider_interview=True,
                    ask_only_when_relevant=False,
                ),
                FollowUpQuestion(
                    "output_format",
                    "Which form should the analysis results use, if known?",
                ),
                FollowUpQuestion(
                    "platform",
                    "Does a platform limit how the data can be accessed or analyzed?",
                ),
            ),
        ),
        TaskType.CUSTOM: TaskProfile(
            task_type=TaskType.CUSTOM,
            label="Other or custom",
            description="Clarify a task that does not fit the listed task types.",
            questions=(
                FollowUpQuestion(
                    "custom_goal",
                    "What result must this task produce?",
                    required_before_provider_interview=True,
                    ask_only_when_relevant=False,
                ),
                FollowUpQuestion(
                    "custom_inputs",
                    "What inputs and outputs does this task need?",
                    required_before_provider_interview=True,
                    ask_only_when_relevant=False,
                ),
                FollowUpQuestion(
                    "custom_success",
                    "How will you know that the task is complete?",
                    required_before_provider_interview=True,
                    ask_only_when_relevant=False,
                ),
                FollowUpQuestion(
                    "custom_constraints",
                    "Which limits or constraints must this task follow?",
                    required_before_provider_interview=True,
                ),
            ),
            requires_custom_support_assessment=True,
        ),
    }
)

CORE_QUESTION_PROMPTS = MappingProxyType(
    {
        "purpose": "What is the main purpose of the harness? Give one clear result.",
        "task_types": (
            "Select one or more task types. The agent target is a separate choice."
        ),
        "target": (
            "Choose the agent target. The model provider is separate; Ollama is a "
            "provider, not an agent target."
        ),
        "operating_context": (
            "What operating context should the instructions know? (Optional.)"
        ),
        "constraints": "List important constraints, separated by ';'. (Optional.)",
        "desired_outputs": (
            "List desired agent outputs, separated by ';'. (Optional.)"
        ),
        "dependencies": "List confirmed dependencies, separated by ';'. (Optional.)",
        "configuration": (
            "List required configuration, using local placeholders only. (Optional.)"
        ),
        "task_type_instructions": (
            "Create separate task-type instruction files? Answer yes or no."
        ),
        "path_scoped_instructions": (
            "Create path-scoped instruction files? Answer yes or no."
        ),
        "custom_support": (
            "Can the clarified custom task be supported by harness instructions? "
            "Choose yes, no, or unsure."
        ),
        "path_patterns": (
            "Enter exact relative glob patterns, separated by ';'. Do not enter "
            "directories unless that exact pattern is intended."
        ),
    }
)

INTERVIEW_CHOICES = MappingProxyType(
    {
        "task_types": (
            "1. Software development",
            "2. Machine learning",
            "3. Data science",
            "4. Other/custom",
        ),
        "target": ("1. Claude Code", "2. GitHub Copilot"),
    }
)

INTERVIEW_OPTIONS = MappingProxyType(
    {
        "task_types": (
            ("1", "Software development"),
            ("2", "Machine learning"),
            ("3", "Data science"),
            ("4", "Other/custom"),
        ),
        "target": (("1", "Claude Code"), ("2", "GitHub Copilot")),
        "task_type_instructions": (("yes", "Yes"), ("no", "No")),
        "path_scoped_instructions": (("yes", "Yes"), ("no", "No")),
        "custom_support": (
            ("yes", "Yes"),
            ("no", "No"),
            ("unsure", "Unsure"),
        ),
    }
)


def task_profile(task_type: TaskType) -> TaskProfile:
    return TASK_PROFILES[task_type]
