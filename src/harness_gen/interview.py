"""Reusable interview and requirements-draft logic."""

from __future__ import annotations

from collections.abc import Mapping

from harness_gen.errors import RequirementsError
from harness_gen.question_templates import (
    CORE_QUESTION_PROMPTS,
    INTERVIEW_CHOICES,
    INTERVIEW_OPTIONS,
    TASK_PROFILES,
    FollowUpQuestion,
)
from harness_gen.requirements import (
    ConfirmedPathPattern,
    CustomTaskSupport,
    InterviewMessage,
    RequirementValue,
    RequirementsDraft,
    SetupRequirements,
    SupplementalAnswer,
    TaskAnswer,
)
from harness_gen.targets import AgentTarget, parse_agent_target
from harness_gen.tasks import TaskType


def _initial_interview_data() -> dict[str, object]:
    return {
        "answers": {},
        "fields": [
            "purpose",
            "task_types",
            "target",
            "operating_context",
            "constraints",
            "desired_outputs",
            "dependencies",
            "configuration",
        ],
        "index": 0,
        "pending_field": None,
        "complete": False,
        "conversation": [],
    }


def _interview_fields(task_types: list[str]) -> list[str]:
    fields = [
        "purpose",
        "task_types",
        "target",
        "operating_context",
        "constraints",
        "desired_outputs",
        "dependencies",
        "configuration",
    ]
    selected = [TaskType(item) for item in task_types]
    profile_fields = [
        question.key
        for task_type in selected
        for question in TASK_PROFILES[task_type].questions
    ]
    fields.extend(dict.fromkeys(profile_fields))
    if TaskType.CUSTOM in selected:
        fields.append("custom_support")
    fields.extend(("task_type_instructions", "path_scoped_instructions"))
    return list(dict.fromkeys(fields))


def _interview_prompt(
    field: str,
    answers: Mapping[str, object],
) -> dict[str, object]:
    message = CORE_QUESTION_PROMPTS.get(field)
    if message is None:
        message = _find_profile_question(field, answers).prompt
    return {
        "kind": "requirements_interview",
        "field": field,
        "message": message,
        **(
            {"choices": list(INTERVIEW_CHOICES[field])}
            if field in INTERVIEW_CHOICES
            else {}
        ),
        **(
            {
                "options": [
                    {"value": value, "label": label}
                    for value, label in INTERVIEW_OPTIONS[field]
                ],
                "multiple": field == "task_types",
            }
            if field in INTERVIEW_OPTIONS
            else {}
        ),
    }


def _is_coachable_field(field: str) -> bool:
    if field in CORE_QUESTION_PROMPTS:
        return field not in {
            "task_types",
            "target",
            "task_type_instructions",
            "path_scoped_instructions",
            "custom_support",
            "path_patterns",
        }
    return any(
        question.key == field
        for profile in TASK_PROFILES.values()
        for question in profile.questions
    )


def _find_profile_question(
    key: str,
    answers: Mapping[str, object],
) -> FollowUpQuestion:
    task_types = [TaskType(item) for item in _string_list(answers.get("task_types"))]
    for task_type in task_types:
        for question in TASK_PROFILES[task_type].questions:
            if question.key == key:
                return question
    raise ValueError("The saved task profile does not contain this question.")


def _normalize_interview_answer(field: str, value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("Enter a text response.")
    answer = value.strip()
    if field == "purpose" and not answer:
        raise ValueError("Purpose is required. Enter a clear harness goal.")
    if field == "task_types":
        return [item.value for item in _parse_task_types(answer)]
    if field == "target":
        return _parse_target(answer).value
    if field in {"task_type_instructions", "path_scoped_instructions"}:
        if answer.lower() in {"y", "yes"}:
            return True
        if answer.lower() in {"", "n", "no"}:
            return False
        raise ValueError("Enter yes or no.")
    if field == "custom_support":
        if answer.lower() in {"y", "yes"}:
            return "yes"
        if answer.lower() in {"n", "no"}:
            return "no"
        if answer.lower() in {"u", "unsure"}:
            return "unsure"
        raise ValueError("Enter yes, no, or unsure.")
    if field == "path_patterns":
        patterns = _split_values(answer)
        if not patterns:
            raise ValueError("Enter at least one exact relative glob pattern.")
        return [ConfirmedPathPattern(item).pattern for item in patterns]
    return answer


def _draft_from_interview(
    state: Mapping[str, object],
    interview: Mapping[str, object],
) -> RequirementsDraft:
    answers = _mapping(interview.get("answers"))
    task_types = tuple(TaskType(item) for item in _string_list(answers["task_types"]))
    target = parse_agent_target(str(answers["target"]))
    by_profile: dict[str, str] = {}
    for task_type in task_types:
        for question in TASK_PROFILES[task_type].questions:
            if question.key in answers:
                by_profile[question.key] = str(answers[question.key])

    data_source = by_profile.pop("data_source", "")
    analysis_goal = by_profile.pop("analysis_goal", "")
    platform = by_profile.pop("platform", "")
    runtime = by_profile.pop("runtime", "")
    verification = by_profile.pop("verification", "")
    profile_answers = [
        TaskAnswer(key, value, confirmed=True)
        for key, value in by_profile.items()
        if value
    ]
    supplemental_answers = _supplemental_answers_from_interview(
        interview.get("supplemental_answers", [])
    )
    interview_conversation = _interview_conversation_from_state(
        interview.get("conversation", [])
    )
    unresolved = _string_list(interview.get("unresolved_choices"))
    data_source_value = _optional_requirement(data_source)
    analysis_goal_value = _optional_requirement(analysis_goal)
    custom_support = CustomTaskSupport.NOT_ASSESSED
    if TaskType.CUSTOM in task_types:
        for required_key in (
            "custom_goal",
            "custom_inputs",
            "custom_success",
            "custom_constraints",
        ):
            if not by_profile.get(required_key):
                profile_answers.append(TaskAnswer(required_key, confirmed=False))
        support_answer = str(answers.get("custom_support", "")).lower()
        if support_answer in {"yes", "y"}:
            custom_support = CustomTaskSupport.SUPPORTED
        elif support_answer in {"no", "n"}:
            custom_support = CustomTaskSupport.UNSUPPORTED
        else:
            unresolved.append("Assess whether the clarified custom task is supported.")
    path_enabled = answers.get("path_scoped_instructions") is True
    path_patterns = tuple(
        ConfirmedPathPattern(str(item), confirmed=False)
        for item in _string_list(answers.get("path_patterns"))
    )
    return RequirementsDraft(
        request=str(state["request"]),
        purpose=RequirementValue(str(answers["purpose"]), confirmed=True),
        task_types=task_types,
        target=target,
        operating_context=_optional_requirement(
            str(answers.get("operating_context", ""))
        ),
        task_answers=tuple(profile_answers),
        supplemental_answers=supplemental_answers,
        interview_conversation=interview_conversation,
        constraints=_confirmed_values(str(answers.get("constraints", ""))),
        desired_outputs=_confirmed_values(str(answers.get("desired_outputs", ""))),
        setup=SetupRequirements(
            platform=_optional_requirement(platform),
            runtime=_optional_requirement(runtime),
            dependencies=_confirmed_values(str(answers.get("dependencies", ""))),
            configuration=_confirmed_values(str(answers.get("configuration", ""))),
            verification=(
                (RequirementValue(verification, confirmed=True),)
                if verification
                else ()
            ),
            data_source=data_source_value,
            analysis_goal=analysis_goal_value,
        ),
        unresolved_choices=tuple(unresolved),
        task_type_instructions=answers.get("task_type_instructions") is True,
        path_scoped_instructions=path_enabled,
        path_patterns=path_patterns,
        custom_task_support=custom_support,
    )


def _supplemental_answers_from_interview(
    value: object,
) -> tuple[SupplementalAnswer, ...]:
    if not isinstance(value, list):
        raise RequirementsError("The saved optional interview answers are invalid.")
    answers: list[SupplementalAnswer] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("question"), str)
            or not isinstance(item.get("answer"), str)
        ):
            raise RequirementsError(
                "The saved optional interview answers are invalid."
            )
        answers.append(
            SupplementalAnswer(
                question=item["question"],
                answer=item["answer"],
            )
        )
    return tuple(answers)


def _interview_conversation_from_state(
    value: object,
) -> tuple[InterviewMessage, ...]:
    if not isinstance(value, list):
        raise RequirementsError("The saved interview conversation is invalid.")
    messages: list[InterviewMessage] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("role"), str)
            or not isinstance(item.get("content"), str)
        ):
            raise RequirementsError("The saved interview conversation is invalid.")
        messages.append(
            InterviewMessage(role=item["role"], content=item["content"])
        )
    return tuple(messages)


def _parse_task_types(value: str) -> tuple[TaskType, ...]:
    aliases = {
        "1": TaskType.SOFTWARE_DEVELOPMENT,
        "software": TaskType.SOFTWARE_DEVELOPMENT,
        "software development": TaskType.SOFTWARE_DEVELOPMENT,
        "2": TaskType.MACHINE_LEARNING,
        "machine learning": TaskType.MACHINE_LEARNING,
        "ml": TaskType.MACHINE_LEARNING,
        "3": TaskType.DATA_SCIENCE,
        "data science": TaskType.DATA_SCIENCE,
        "4": TaskType.CUSTOM,
        "custom": TaskType.CUSTOM,
        "other": TaskType.CUSTOM,
        "other/custom": TaskType.CUSTOM,
    }
    tokens = [item.strip().lower() for item in value.split(",") if item.strip()]
    try:
        result = tuple(dict.fromkeys(aliases[token] for token in tokens))
    except KeyError:
        raise ValueError(
            "Choose task types 1-4 or enter software development, machine learning, "
            "data science, or custom."
        ) from None
    if not result:
        raise ValueError("Choose at least one task type.")
    return result


def _parse_target(value: str) -> AgentTarget:
    if value.strip() == "1":
        return AgentTarget.CLAUDE_CODE
    if value.strip() == "2":
        return AgentTarget.GITHUB_COPILOT
    return parse_agent_target(value)


def _optional_requirement(value: str) -> RequirementValue | None:
    return RequirementValue(value, confirmed=True) if value.strip() else None


def _confirmed_values(value: str) -> tuple[RequirementValue, ...]:
    return tuple(
        RequirementValue(item, confirmed=True)
        for item in _split_values(value)
    )


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        return {}
    return dict(value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    return value


def _integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
