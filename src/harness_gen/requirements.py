"""Structured requirements and readiness gates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from harness_gen.errors import RequirementsError
from harness_gen.path_rules import validate_relative_glob
from harness_gen.targets import AgentTarget
from harness_gen.question_templates import TASK_PROFILES
from harness_gen.tasks import TaskType


class CustomTaskSupport(StrEnum):
    NOT_ASSESSED = "not_assessed"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RequirementValue:
    value: str | None = field(default=None, repr=False)
    confirmed: bool = False

    def __post_init__(self) -> None:
        if self.confirmed and (self.value is None or not self.value.strip()):
            raise RequirementsError("A confirmed requirement must have a value.")

    @property
    def confirmed_value(self) -> str | None:
        if not self.confirmed:
            return None
        return self.value


@dataclass(frozen=True, slots=True)
class TaskAnswer:
    key: str
    value: str | None = field(default=None, repr=False)
    confirmed: bool = False

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise RequirementsError("A task answer must have a field name.")
        if self.confirmed and (self.value is None or not self.value.strip()):
            raise RequirementsError("A confirmed task answer must have a value.")

    @property
    def confirmed_value(self) -> str | None:
        if not self.confirmed:
            return None
        return self.value


@dataclass(frozen=True, slots=True)
class SupplementalAnswer:
    question: str = field(repr=False)
    answer: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.question.strip() or not self.answer.strip():
            raise RequirementsError(
                "A supplemental answer must include its question and answer."
            )


@dataclass(frozen=True, slots=True)
class SetupRequirements:
    platform: RequirementValue | None = None
    runtime: RequirementValue | None = None
    dependencies: tuple[RequirementValue, ...] = ()
    configuration: tuple[RequirementValue, ...] = ()
    verification: tuple[RequirementValue, ...] = ()
    data_source: RequirementValue | None = None
    analysis_goal: RequirementValue | None = None


@dataclass(frozen=True, slots=True)
class ConfirmedPathPattern:
    pattern: str = field(repr=False)
    confirmed: bool = False

    def __post_init__(self) -> None:
        validate_relative_glob(self.pattern)


@dataclass(frozen=True, slots=True)
class RequirementsDraft:
    request: str = field(repr=False)
    purpose: RequirementValue | None = None
    requirements_reviewed: bool = False
    task_types: tuple[TaskType, ...] = ()
    target: AgentTarget | None = None
    operating_context: RequirementValue | None = None
    task_answers: tuple[TaskAnswer, ...] = ()
    supplemental_answers: tuple[SupplementalAnswer, ...] = ()
    constraints: tuple[RequirementValue, ...] = ()
    desired_outputs: tuple[RequirementValue, ...] = ()
    setup: SetupRequirements = field(default_factory=SetupRequirements)
    unresolved_choices: tuple[str, ...] = field(default=(), repr=False)
    task_type_instructions: bool = False
    path_scoped_instructions: bool = False
    path_patterns: tuple[ConfirmedPathPattern, ...] = ()
    custom_task_support: CustomTaskSupport = CustomTaskSupport.NOT_ASSESSED

    def __post_init__(self) -> None:
        if not self.request.strip():
            raise RequirementsError("Enter a request before gathering requirements.")
        if len(set(self.task_types)) != len(self.task_types):
            raise RequirementsError("Select each task type only once.")
        task_answer_keys = [item.key for item in self.task_answers]
        if len(set(task_answer_keys)) != len(task_answer_keys):
            raise RequirementsError("Each task answer field must be unique.")
        supplemental_questions = [
            item.question.casefold().strip() for item in self.supplemental_answers
        ]
        if len(set(supplemental_questions)) != len(supplemental_questions):
            raise RequirementsError("Each supplemental question must be unique.")
        patterns = [item.pattern for item in self.path_patterns]
        if len(set(patterns)) != len(patterns):
            raise RequirementsError("Each path-scoped glob pattern must be unique.")

    @property
    def data_science_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if TaskType.DATA_SCIENCE in self.task_types:
            if not self._is_data_science_field_confirmed(
                "data_source", self.setup.data_source
            ):
                blockers.append("Confirm the data source for the analysis.")
            if not self._is_data_science_field_confirmed(
                "analysis_goal", self.setup.analysis_goal
            ):
                blockers.append("Confirm the goal of the analysis.")
        return tuple(blockers)

    def _is_data_science_field_confirmed(
        self,
        key: str,
        setup_value: RequirementValue | None,
    ) -> bool:
        if setup_value is not None:
            return _is_confirmed(setup_value)
        return any(
            answer.key == key and answer.confirmed_value is not None
            for answer in self.task_answers
        )

    @property
    def provider_interview_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.task_types:
            blockers.append(
                "Choose at least one task type before the provider interview."
            )
        if self.target is None:
            blockers.append(
                "Choose a supported agent target before the provider interview."
            )
        blockers.extend(self.data_science_blockers)
        blockers.extend(self.custom_task_blockers)
        return tuple(blockers)

    @property
    def generation_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.requirements_reviewed:
            blockers.append("Review and confirm the requirements before generation.")
        if not _is_confirmed(self.purpose):
            blockers.append("Confirm the purpose of the harness.")
        if not self.task_types:
            blockers.append("Choose at least one task type.")
        if self.target is None:
            blockers.append("Choose a supported agent target.")
        if self.unresolved_choices:
            blockers.append("Resolve material open choices.")
        if self.operating_context is not None and not self.operating_context.confirmed:
            blockers.append(
                "Confirm the operating context or keep it as an open choice."
            )
        if any(not item.confirmed for item in self.constraints):
            blockers.append("Confirm each material constraint.")
        if any(not item.confirmed for item in self.desired_outputs):
            blockers.append("Confirm each material desired output.")
        if any(not item.confirmed for item in self.task_answers):
            blockers.append("Confirm each material task-specific answer.")

        if TaskType.DATA_SCIENCE in self.task_types:
            blockers.extend(self.data_science_blockers)

        if TaskType.CUSTOM in self.task_types:
            blockers.extend(self.custom_task_blockers)
            if self.custom_task_support is CustomTaskSupport.NOT_ASSESSED:
                blockers.append(
                    "Assess whether the clarified custom task is supported."
                )
            elif self.custom_task_support is CustomTaskSupport.UNSUPPORTED:
                blockers.append(
                    "The clarified custom task is unsupported; explain the limitation."
                )

        for name, requirement in (
            ("platform", self.setup.platform),
            ("runtime", self.setup.runtime),
        ):
            if requirement is not None and not requirement.confirmed:
                blockers.append(f"Confirm the {name} or keep it as an open choice.")
        if TaskType.DATA_SCIENCE not in self.task_types:
            for name, requirement in (
                ("data source", self.setup.data_source),
                ("analysis goal", self.setup.analysis_goal),
            ):
                if requirement is not None and not requirement.confirmed:
                    blockers.append(
                        f"Confirm the {name} or keep it as an open choice."
                    )
        for name, requirements in (
            ("dependency", self.setup.dependencies),
            ("configuration", self.setup.configuration),
            ("verification", self.setup.verification),
        ):
            if any(not requirement.confirmed for requirement in requirements):
                blockers.append(f"Confirm each material {name} requirement.")

        if self.path_scoped_instructions and not self.path_patterns:
            blockers.append("Supply and confirm exact relative path patterns.")
        elif self.path_scoped_instructions and any(
            not item.confirmed for item in self.path_patterns
        ):
            blockers.append("Confirm each exact relative path pattern.")
        return tuple(blockers)

    @property
    def ready_for_generation(self) -> bool:
        return not self.generation_blockers

    @property
    def custom_task_blockers(self) -> tuple[str, ...]:
        if TaskType.CUSTOM not in self.task_types:
            return ()
        answers = {item.key: item.confirmed_value for item in self.task_answers}
        return tuple(
            f"Confirm the custom task requirement: {question.key.replace('_', ' ')}."
            for question in TASK_PROFILES[TaskType.CUSTOM].questions
            if question.required_before_provider_interview
            and not answers.get(question.key)
        )


def requirements_to_state(draft: RequirementsDraft) -> dict[str, object]:
    """Return the JSON-compatible values needed to checkpoint a draft."""
    return {
        "request": draft.request,
        "purpose": _requirement_value_to_state(draft.purpose),
        "requirements_reviewed": draft.requirements_reviewed,
        "task_types": [item.value for item in draft.task_types],
        "target": None if draft.target is None else draft.target.value,
        "operating_context": _requirement_value_to_state(
            draft.operating_context
        ),
        "task_answers": [
            {
                "key": item.key,
                "value": item.value,
                "confirmed": item.confirmed,
            }
            for item in draft.task_answers
        ],
        "supplemental_answers": [
            {"question": item.question, "answer": item.answer}
            for item in draft.supplemental_answers
        ],
        "constraints": [
            _requirement_value_to_state(item) for item in draft.constraints
        ],
        "desired_outputs": [
            _requirement_value_to_state(item) for item in draft.desired_outputs
        ],
        "setup": {
            "platform": _requirement_value_to_state(draft.setup.platform),
            "runtime": _requirement_value_to_state(draft.setup.runtime),
            "dependencies": [
                _requirement_value_to_state(item)
                for item in draft.setup.dependencies
            ],
            "configuration": [
                _requirement_value_to_state(item)
                for item in draft.setup.configuration
            ],
            "verification": [
                _requirement_value_to_state(item)
                for item in draft.setup.verification
            ],
            "data_source": _requirement_value_to_state(
                draft.setup.data_source
            ),
            "analysis_goal": _requirement_value_to_state(
                draft.setup.analysis_goal
            ),
        },
        "unresolved_choices": list(draft.unresolved_choices),
        "task_type_instructions": draft.task_type_instructions,
        "path_scoped_instructions": draft.path_scoped_instructions,
        "path_patterns": [
            {"pattern": item.pattern, "confirmed": item.confirmed}
            for item in draft.path_patterns
        ],
        "custom_task_support": draft.custom_task_support.value,
    }


def requirements_from_state(
    values: Mapping[str, object],
) -> RequirementsDraft:
    """Rebuild a requirements draft from its checkpoint representation."""
    try:
        requirement_keys = {
            "request",
            "purpose",
            "requirements_reviewed",
            "task_types",
            "target",
            "operating_context",
            "task_answers",
            "supplemental_answers",
            "constraints",
            "desired_outputs",
            "setup",
            "unresolved_choices",
            "task_type_instructions",
            "path_scoped_instructions",
            "path_patterns",
            "custom_task_support",
        }
        legacy_requirement_keys = requirement_keys - {"supplemental_answers"}
        saved_keys = frozenset(values)
        if saved_keys not in {
            frozenset(requirement_keys),
            frozenset(legacy_requirement_keys),
        }:
            raise TypeError("The saved requirements have unsupported fields.")
        setup = _state_mapping(values["setup"])
        _require_state_keys(
            setup,
            {
                "platform",
                "runtime",
                "dependencies",
                "configuration",
                "verification",
                "data_source",
                "analysis_goal",
            },
        )
        target_value = values.get("target")
        return RequirementsDraft(
            request=_state_string(values["request"]),
            purpose=_requirement_value_from_state(values.get("purpose")),
            requirements_reviewed=_state_bool(
                values.get("requirements_reviewed", False)
            ),
            task_types=tuple(
                TaskType(_state_string(item))
                for item in _state_list(values.get("task_types", []))
            ),
            target=(
                None
                if target_value is None
                else AgentTarget(_state_string(target_value))
            ),
            operating_context=_requirement_value_from_state(
                values.get("operating_context")
            ),
            task_answers=tuple(
                _task_answer_from_state(item)
                for item in _state_list(values.get("task_answers", []))
            ),
            supplemental_answers=tuple(
                _supplemental_answer_from_state(item)
                for item in _state_list(values.get("supplemental_answers", []))
            ),
            constraints=tuple(
                _required_requirement_value(item)
                for item in _state_list(values.get("constraints", []))
            ),
            desired_outputs=tuple(
                _required_requirement_value(item)
                for item in _state_list(values.get("desired_outputs", []))
            ),
            setup=SetupRequirements(
                platform=_requirement_value_from_state(setup.get("platform")),
                runtime=_requirement_value_from_state(setup.get("runtime")),
                dependencies=tuple(
                    _required_requirement_value(item)
                    for item in _state_list(setup.get("dependencies", []))
                ),
                configuration=tuple(
                    _required_requirement_value(item)
                    for item in _state_list(setup.get("configuration", []))
                ),
                verification=tuple(
                    _required_requirement_value(item)
                    for item in _state_list(setup.get("verification", []))
                ),
                data_source=_requirement_value_from_state(
                    setup.get("data_source")
                ),
                analysis_goal=_requirement_value_from_state(
                    setup.get("analysis_goal")
                ),
            ),
            unresolved_choices=tuple(
                _state_string(item)
                for item in _state_list(values.get("unresolved_choices", []))
            ),
            task_type_instructions=_state_bool(
                values.get("task_type_instructions", False)
            ),
            path_scoped_instructions=_state_bool(
                values.get("path_scoped_instructions", False)
            ),
            path_patterns=tuple(
                _path_pattern_from_state(item)
                for item in _state_list(values.get("path_patterns", []))
            ),
            custom_task_support=CustomTaskSupport(
                _state_string(
                    values.get(
                        "custom_task_support",
                        CustomTaskSupport.NOT_ASSESSED.value,
                    )
                )
            ),
        )
    except (KeyError, TypeError, ValueError, RequirementsError):
        raise RequirementsError(
            "The saved requirements are invalid. Review and correct them."
        ) from None


def _requirement_value_to_state(
    value: RequirementValue | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {"value": value.value, "confirmed": value.confirmed}


def _requirement_value_from_state(value: object) -> RequirementValue | None:
    if value is None:
        return None
    data = _state_mapping(value)
    _require_state_keys(data, {"value", "confirmed"})
    return RequirementValue(
        value=_optional_state_string(data.get("value")),
        confirmed=_state_bool(data.get("confirmed", False)),
    )


def _required_requirement_value(value: object) -> RequirementValue:
    requirement = _requirement_value_from_state(value)
    if requirement is None:
        raise TypeError("A requirement list cannot contain null values.")
    return requirement


def _task_answer_from_state(value: object) -> TaskAnswer:
    data = _state_mapping(value)
    _require_state_keys(data, {"key", "value", "confirmed"})
    return TaskAnswer(
        key=_state_string(data["key"]),
        value=_optional_state_string(data["value"]),
        confirmed=_state_bool(data["confirmed"]),
    )


def _supplemental_answer_from_state(value: object) -> SupplementalAnswer:
    data = _state_mapping(value)
    _require_state_keys(data, {"question", "answer"})
    return SupplementalAnswer(
        question=_state_string(data["question"]),
        answer=_state_string(data["answer"]),
    )


def _path_pattern_from_state(value: object) -> ConfirmedPathPattern:
    data = _state_mapping(value)
    _require_state_keys(data, {"pattern", "confirmed"})
    return ConfirmedPathPattern(
        pattern=_state_string(data["pattern"]),
        confirmed=_state_bool(data["confirmed"]),
    )


def _state_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError("Expected a string-keyed mapping.")
    return value


def _require_state_keys(
    value: Mapping[str, object],
    expected_keys: set[str],
) -> None:
    if set(value) != expected_keys:
        raise TypeError("The saved requirements have unsupported fields.")


def _state_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("Expected a list.")
    return value


def _state_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected a string.")
    return value


def _optional_state_string(value: object) -> str | None:
    if value is None:
        return None
    return _state_string(value)


def _state_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("Expected a boolean.")
    return value


def _is_confirmed(value: RequirementValue | None) -> bool:
    return value is not None and value.confirmed_value is not None
