"""Line-oriented terminal interface for harness creation."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError, RequirementsError
from harness_gen.logging_config import configure_logging
from harness_gen.requirements import (
    ConfirmedPathPattern,
    CustomTaskSupport,
    RequirementValue,
    RequirementsDraft,
    SetupRequirements,
    TaskAnswer,
    requirements_from_state,
    requirements_to_state,
)
from harness_gen.sessions import DatabaseSettings, SessionManager
from harness_gen.targets import AgentTarget, parse_agent_target
from harness_gen.tasks import TASK_PROFILES, FollowUpQuestion, TaskType
from harness_gen.workflow import GenerationRunner, HarnessWorkflowState

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], object]
_PAUSE_COMMAND = ":pause"
_EXIT_COMMAND = ":quit"


class _EditPaused(Exception):
    pass


def main() -> int:
    return run_cli()


def run_cli(
    *,
    input_fn: InputFunction | None = None,
    output_fn: OutputFunction = print,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
    generator: GenerationRunner | None = None,
) -> int:
    """Run one CLI session without logging user or provider content."""
    read = input if input_fn is None else input_fn
    root = Path.cwd() if cwd is None else cwd
    if environ is None:
        load_dotenv(override=False)
        values = os.environ
    else:
        values = environ
    try:
        log_path = configure_logging(
            log_directory=root / "logs",
            level=values.get("HARNESS_CREATOR_LOG_LEVEL"),
        )
        logger = logging.getLogger("harness_gen.cli")
        logger.info("CLI run started")
        provider_settings = ProviderSettings.from_environment(values)
        database_settings = DatabaseSettings.from_environment(values)
        output_fn(f"Operational log: {log_path}")
        output_fn(f"Session database: {database_settings.path}")
        output_fn(f"Model provider: {provider_settings.provider.value}")
        output_fn(
            "Requirements and generated files are stored in the session database. "
            "Do not enter secrets: text is not scanned or redacted. External "
            "providers receive content only after approval; local Ollama does not "
            "need a separate provider approval. Review external provider terms. "
            "The active session remains until you delete or restart it. "
            "Deleting a session removes its checkpoint records but does not ensure "
            "secure erasure from storage."
        )
        with SessionManager(
            database_settings,
            provider_settings=provider_settings,
            generator=generator,
        ) as manager:
            return _run_session(manager, read, output_fn)
    except (HarnessError, OSError) as error:
        logging.getLogger("harness_gen.cli").error(
            "CLI stopped (%s)", type(error).__name__
        )
        output_fn(f"Cannot continue: {error}")
        return 1


def _run_session(
    manager: SessionManager,
    read: InputFunction,
    write: OutputFunction,
) -> int:
    if manager.active_thread_id is not None:
        return _resume_menu(manager, read, write)
    return _create_session(manager, read, write)


def _resume_menu(
    manager: SessionManager,
    read: InputFunction,
    write: OutputFunction,
) -> int:
    state = manager.state()
    write("An active session is saved.")
    write(f"Current step: {state.get('phase', 'checkpoint')}.")
    choice = _read_choice(
        "Choose: [r]esume, re[s]tart, [d]elete, or [q]uit.",
        {"r", "s", "d", "q"},
        read,
        write,
    )
    if choice is None or choice == "q":
        return 0
    if choice == "d":
        confirmation = _read_choice(
            "Delete the saved session and its checkpoint? [y/N]",
            {"y", "n"},
            read,
            write,
        )
        if confirmation == "y":
            manager.delete()
            write("The saved session was deleted. Project files were not changed.")
        else:
            write("The saved session was kept.")
        return 0
    if choice == "s":
        manager.restart()
        state = manager.state()
        write(
            "Started a fresh thread. Previously generated files were not carried over."
        )
    return _drive_session(manager, state, read, write)


def _create_session(
    manager: SessionManager,
    read: InputFunction,
    write: OutputFunction,
) -> int:
    write("Describe the harness instructions you want to create.")
    request = _read_line("Request: ", read, write)
    if request is None or request == _EXIT_COMMAND:
        return 0
    if not request.strip():
        write("Enter a non-empty request to start.")
        return 1
    interview = _initial_interview_data()
    manager.start({"request": request.strip(), "interview_data": interview})
    logger = logging.getLogger("harness_gen.cli")
    logger.info("Requirements interview started")
    return _drive_session(manager, manager.state(), read, write)


def _drive_session(
    manager: SessionManager,
    state: HarnessWorkflowState,
    read: InputFunction,
    write: OutputFunction,
) -> int:
    while True:
        if state.get("workflow_enabled") is True:
            return _drive_workflow(manager, state, read, write)
        if state.get("phase") == "user_input" and state.get("pending_prompt"):
            state, paused = _answer_interview_prompt(manager, state, read, write)
            if paused:
                return 0
            continue
        if state.get("interview_data") is None:
            interview = _initial_interview_data()
            state = manager.update({"interview_data": interview})
        interview = _mapping(state.get("interview_data"))
        if interview.get("complete") is True:
            try:
                draft = _draft_from_interview(state, interview)
            except (HarnessError, ValueError) as error:
                write(f"Requirements need correction: {error}")
                return 1
            manager.start_workflow(draft)
            logging.getLogger("harness_gen.cli").info(
                "Requirements review started"
            )
            state = manager.state()
            continue

        answers = _mapping(interview.get("answers"))
        fields = _string_list(interview.get("fields"))
        index = _integer(interview.get("index", 0))
        if index >= len(fields):
            interview["complete"] = True
            state = manager.update({"interview_data": interview})
            continue
        field = fields[index]
        pending_field = interview.get("pending_field")
        if pending_field is None:
            interview["pending_field"] = field
            state = manager.update({"interview_data": interview})
            prompt = _interview_prompt(field, answers)
            state = manager.request_input(prompt)
            continue
        if pending_field != field:
            write("The saved interview step is inconsistent. Restart the session.")
            return 1
        if state.get("last_response") is not None:
            try:
                answer = _normalize_interview_answer(field, state["last_response"])
            except (HarnessError, ValueError) as error:
                write(str(error))
                state = manager.update({"last_response": None})
                state = manager.request_input(_interview_prompt(field, answers))
                continue
            answers[field] = answer
            interview["answers"] = answers
            interview["pending_field"] = None
            interview["index"] = index + 1
            if field == "task_types":
                interview["fields"] = _interview_fields(answer)
            if field == "path_scoped_instructions" and answer is True:
                interview["fields"] = [
                    *(_string_list(interview.get("fields"))),
                    "path_patterns",
                ]
            state = manager.update(
                {"interview_data": interview, "last_response": None}
            )
            continue
        state = manager.request_input(_interview_prompt(field, answers))


def _answer_interview_prompt(
    manager: SessionManager,
    state: HarnessWorkflowState,
    read: InputFunction,
    write: OutputFunction,
) -> tuple[HarnessWorkflowState, bool]:
    prompt = _mapping(state.get("pending_prompt"))
    write(str(prompt.get("message", "Enter a response.")))
    choices = prompt.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            write(f"  {choice}")
    answer = _read_line("> ", read, write)
    if answer is None or answer in {_PAUSE_COMMAND, _EXIT_COMMAND}:
        manager.pause()
        write("Session paused. Resume it by running harness-gen again.")
        return state, True
    return manager.resume(answer), False


def _drive_workflow(
    manager: SessionManager,
    state: HarnessWorkflowState,
    read: InputFunction,
    write: OutputFunction,
) -> int:
    previewed = False
    while True:
        outcome = state.get("outcome")
        if outcome:
            _show_outcome(state, write)
            return 0 if outcome == "complete" else 1
        if state.get("phase") == "destination_selection" and not previewed:
            _show_generated_files(state, write)
            previewed = True
        prompt_value = state.get("pending_prompt")
        if not isinstance(prompt_value, Mapping):
            write("The workflow is not waiting for a user decision.")
            write("Resume or restart the session from the menu.")
            return 1
        prompt = _mapping(prompt_value)
        kind = prompt.get("kind")
        response: object
        if kind == "requirements_review":
            response, paused = _requirements_decision(
                manager, state, prompt, read, write
            )
            if paused:
                return 0
        elif kind == "provider_approval":
            write(str(prompt.get("message", "")))
            choice = _read_choice(
                "Send the disclosed data to this provider? [y]es/[n]o/[p]ause",
                {"y", "n", "p"},
                read,
                write,
            )
            if choice is None or choice == "p":
                manager.pause()
                write("Session paused.")
                return 0
            response = {"approved": choice == "y"}
        elif kind == "destination_selection":
            write(str(prompt.get("message", "")))
            destination = _read_line(
                "Existing project directory (or :pause): ", read, write
            )
            if destination is None or destination in {_PAUSE_COMMAND, _EXIT_COMMAND}:
                manager.pause()
                write("Session paused.")
                return 0
            response = {"destination": destination}
        elif kind == "write_approval":
            write(str(prompt.get("message", "")))
            for item in prompt.get("files", []):
                write(f"  {item}")
            choice = _read_choice(
                "Apply this full file set? [y]es/[n]o/[p]ause",
                {"y", "n", "p"},
                read,
                write,
            )
            if choice is None or choice == "p":
                manager.pause()
                write("Session paused.")
                return 0
            response = {"approved": choice == "y"}
        else:
            write("The saved decision is not supported by this CLI.")
            return 1
        state = manager.resume(response)


def _requirements_decision(
    manager: SessionManager,
    state: HarnessWorkflowState,
    prompt: Mapping[str, object],
    read: InputFunction,
    write: OutputFunction,
) -> tuple[object, bool]:
    write("Requirements summary:")
    _show_requirements(prompt, write)
    blockers = [
        item
        for item in prompt.get("blockers", [])
        if item
        not in {
            "Review and confirm the requirements before generation.",
            "Confirm each exact relative path pattern.",
        }
    ]
    for blocker in blockers:
        write(f"Unresolved: {blocker}")
    if blockers:
        choice = _read_choice(
            "Edit requirements or pause? [e]dit/[p]ause",
            {"e", "p"},
            read,
            write,
        )
    else:
        choice = _read_choice(
            "Confirm these requirements, edit them, or pause? "
            "[c]onfirm/[e]dit/[p]ause",
            {"c", "e", "p"},
            read,
            write,
        )
    if choice is None or choice == "p":
        manager.pause()
        write("Session paused.")
        return {}, True
    if choice == "e":
        current = requirements_from_state(_mapping(state.get("requirements")))
        corrected, paused = _edit_requirements(current, read, write)
        if paused:
            manager.pause()
            write("Session paused. The unsubmitted edits were not saved.")
            return {}, True
        return {"requirements": requirements_to_state(corrected)}, False
    return {"approved": True}, False


def _edit_requirements(
    draft: RequirementsDraft,
    read: InputFunction,
    write: OutputFunction,
) -> tuple[RequirementsDraft, bool]:
    while True:
        for number, label in enumerate(
            (
                "purpose",
                "task types",
                "agent target",
                "operating context",
                "constraints",
                "desired outputs",
                "task-specific answers",
                "setup requirements",
                "optional task instructions",
                "path-scoped instructions",
                "unresolved choices",
                "initial request",
                "custom task support",
                "finish editing",
            ),
            1,
        ):
            write(f"{number}. {label}")
        choice = _read_line(
            "Select a field to edit (or :pause): ", read, write
        )
        if choice is None or choice in {_PAUSE_COMMAND, _EXIT_COMMAND}:
            return draft, True
        if choice == "14":
            return draft, False
        if choice not in {str(number) for number in range(1, 14)}:
            write("Choose a number from 1 to 14.")
            continue
        try:
            draft = _edit_requirement_field(
                draft, int(choice), read, write
            )
        except _EditPaused:
            return draft, True
        except (HarnessError, ValueError) as error:
            write(str(error))


def _edit_requirement_field(
    draft: RequirementsDraft,
    field_number: int,
    read: InputFunction,
    write: OutputFunction,
) -> RequirementsDraft:
    if field_number == 1:
        value = _required_line("Purpose: ", read, write)
        return replace(draft, purpose=RequirementValue(value, confirmed=True))
    if field_number == 2:
        value = _required_line("Task types (1-4, comma-separated): ", read, write)
        task_types = _parse_task_types(value)
        active_answers = {
            question.key
            for task_type in task_types
            for question in TASK_PROFILES[task_type].questions
        }
        unresolved = draft.unresolved_choices
        support = draft.custom_task_support
        if TaskType.CUSTOM not in task_types:
            support = CustomTaskSupport.NOT_ASSESSED
            unresolved = tuple(
                item
                for item in unresolved
                if item != "Assess whether the clarified custom task is supported."
            )
        return replace(
            draft,
            task_types=task_types,
            task_answers=tuple(
                answer
                for answer in draft.task_answers
                if answer.key in active_answers
            ),
            custom_task_support=support,
            unresolved_choices=unresolved,
        )
    if field_number == 3:
        value = _required_line(
            "Target (1 Claude Code, 2 GitHub Copilot): ", read, write
        )
        return replace(draft, target=_parse_target(value))
    if field_number == 4:
        value = _edit_read("Operating context (blank to clear): ", read, write)
        return replace(
            draft,
            operating_context=(
                RequirementValue(value, confirmed=True)
                if value
                else None
            ),
        )
    if field_number == 5:
        return replace(
            draft,
            constraints=_confirmed_values(
                _edit_read(
                    "Constraints separated by ';' (blank to clear): ",
                    read,
                    write,
                )
            ),
        )
    if field_number == 6:
        return replace(
            draft,
            desired_outputs=_confirmed_values(
                _edit_read(
                    "Desired outputs separated by ';' (blank to clear): ",
                    read,
                    write,
                )
            ),
        )
    if field_number == 7:
        for answer in draft.task_answers:
            write(f"{answer.key}: {answer.value or '(not set)'}")
        key = _required_line("Answer key to edit: ", read, write)
        value = _edit_read("Updated answer (blank to remove): ", read, write)
        answers = [item for item in draft.task_answers if item.key != key]
        if value:
            answers.append(TaskAnswer(key, value, confirmed=True))
        elif TaskType.CUSTOM in draft.task_types and key.startswith("custom_"):
            answers.append(TaskAnswer(key, confirmed=False))
        return replace(draft, task_answers=tuple(answers))
    if field_number == 8:
        return _edit_setup(draft, read, write)
    if field_number == 9:
        enabled = _yes_no(
            "Create separate task-type instruction files? [y/N] ",
            read,
            write,
        )
        return replace(draft, task_type_instructions=enabled)
    if field_number == 10:
        enabled = _yes_no(
            "Create path-scoped instruction files? [y/N] ", read, write
        )
        patterns: tuple[ConfirmedPathPattern, ...] = ()
        if enabled:
            raw = _required_line(
                "Exact relative glob patterns separated by ';': ", read, write
            )
            patterns = tuple(
                ConfirmedPathPattern(pattern, confirmed=False)
                for pattern in _split_values(raw)
            )
        return replace(
            draft,
            path_scoped_instructions=enabled,
            path_patterns=patterns,
        )
    if field_number == 11:
        raw = _edit_read(
            "Unresolved choices separated by ';' (blank to clear): ",
            read,
            write,
        )
        return replace(draft, unresolved_choices=_split_values(raw))
    if field_number == 12:
        return replace(
            draft,
            request=_required_line("Updated initial request: ", read, write),
        )
    if field_number == 13:
        support = _read_choice(
            "Can the custom task be supported? [y]es/[n]o/[u]nsure",
            {"y", "n", "u"},
            read,
            write,
        )
        if support is None:
            raise _EditPaused
        selected = {
            "y": CustomTaskSupport.SUPPORTED,
            "n": CustomTaskSupport.UNSUPPORTED,
            "u": CustomTaskSupport.NOT_ASSESSED,
        }[support or "u"]
        unresolved = tuple(
            item
            for item in draft.unresolved_choices
            if item != "Assess whether the clarified custom task is supported."
        )
        if selected is CustomTaskSupport.NOT_ASSESSED:
            unresolved = (
                *unresolved,
                "Assess whether the clarified custom task is supported.",
            )
        return replace(
            draft,
            custom_task_support=selected,
            unresolved_choices=unresolved,
        )
    raise ValueError("Choose a supported requirements field.")


def _edit_setup(
    draft: RequirementsDraft,
    read: InputFunction,
    write: OutputFunction,
) -> RequirementsDraft:
    setup = draft.setup
    write(
        "Setup fields: platform, runtime, data_source, analysis_goal, dependencies, "
        "configuration, verification."
    )
    field_name = _required_line("Setup field: ", read, write)
    allowed = {
        "platform",
        "runtime",
        "data_source",
        "analysis_goal",
        "dependencies",
        "configuration",
        "verification",
    }
    if field_name not in allowed:
        raise ValueError("Choose one of the listed setup fields.")
    raw = _edit_read(
        "Updated value (separate list items with ';'; blank to clear): ",
        read,
        write,
    )
    if field_name in {"dependencies", "configuration", "verification"}:
        updated = replace(
            setup,
            **{field_name: _confirmed_values(raw)},
        )
    else:
        updated = replace(
            setup,
            **{
                field_name: (
                    RequirementValue(raw, confirmed=True) if raw else None
                )
            },
        )
    return replace(draft, setup=updated)


def _show_requirements(
    prompt: Mapping[str, object],
    write: OutputFunction,
) -> None:
    write(f"Request: {prompt.get('request', '')}")
    for item in prompt.get("unresolved_choices", []):
        write(f"Open choice: {item}")
    summary = _mapping(prompt.get("summary"))
    for label, key in (
        ("Purpose", "purpose"),
        ("Task types", "task_types"),
        ("Agent target", "target"),
        ("Operating context", "operating_context"),
        ("Task-specific answers", "task_answers"),
        ("Constraints", "constraints"),
        ("Desired outputs", "desired_outputs"),
        ("Setup", "setup"),
        ("Task-type instruction files", "task_type_instructions"),
        ("Path-scoped instruction files", "path_scoped_instructions"),
        ("Confirmed path patterns", "path_patterns"),
        ("Custom task support", "custom_task_support"),
    ):
        write(f"{label}: {_display_value(summary.get(key))}")


def _show_outcome(
    state: HarnessWorkflowState,
    write: OutputFunction,
) -> None:
    outcome = state.get("outcome")
    if outcome == "complete":
        result = _mapping(state.get("write_result"))
        write(f"Files were written to {result.get('destination', '')}:")
        for item in result.get("written", []):
            if isinstance(item, Mapping):
                write(f"  {item.get('action')}: {item.get('path')}")
        return
    write(f"Workflow stopped: {state.get('error_message', 'No details available.')}")
    findings = state.get("validation_findings", [])
    for finding in findings:
        if isinstance(finding, Mapping):
            write(
                f"  {finding.get('path') or 'output'}: "
                f"{finding.get('message', 'Validation issue')}"
            )


def _show_generated_files(
    state: HarnessWorkflowState,
    write: OutputFunction,
) -> None:
    write("Generated files (not yet written):")
    files = state.get("generated_files", [])
    for item in files:
        if not isinstance(item, Mapping):
            continue
        write(f"\n--- {item.get('path', '(unknown path)')} ---")
        write(f"Purpose: {item.get('purpose', '')}")
        write(str(item.get("content", "")))


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
    messages = {
        "purpose": (
            "What is the main purpose of the harness? Give one clear result."
        ),
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
        "desired_outputs": "List desired agent outputs, separated by ';'. (Optional.)",
        "dependencies": "List confirmed dependencies, separated by ';'. (Optional.)",
        "configuration": (
            "List required configuration, using local placeholders only. (Optional.)"
        ),
        "task_type_instructions": "Create separate task-type instruction files? [y/N]",
        "path_scoped_instructions": "Create path-scoped instruction files? [y/N]",
        "custom_support": (
            "Can the clarified custom task be supported by harness instructions? "
            "[y]es/[n]o/[u]nsure"
        ),
        "path_patterns": (
            "Enter exact relative glob patterns, separated by ';'. Do not enter "
            "directories unless that exact pattern is intended."
        ),
    }
    if field in {"task_types", "target"}:
        choices = (
            [
                "1. Software development",
                "2. Machine learning",
                "3. Data science",
                "4. Other/custom",
            ]
            if field == "task_types"
            else ["1. Claude Code", "2. GitHub Copilot"]
        )
    elif field in messages:
        choices = None
    else:
        question = _find_profile_question(field, answers)
        messages[field] = question.prompt
        choices = None
    return {
        "kind": "requirements_interview",
        "field": field,
        "message": messages[field],
        **({"choices": choices} if choices else {}),
    }


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
    state: HarnessWorkflowState,
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
    unresolved: list[str] = []
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


def _read_choice(
    prompt: str,
    allowed: set[str],
    read: InputFunction,
    write: OutputFunction,
) -> str | None:
    while True:
        value = _read_line(prompt, read, write)
        if value is None:
            return None
        value = value.lower()
        if value in {_PAUSE_COMMAND, _EXIT_COMMAND}:
            return "p" if "p" in allowed else None
        if value in allowed:
            return value
        write(f"Choose one of: {', '.join(sorted(allowed))}.")


def _yes_no(
    prompt: str,
    read: InputFunction,
    write: OutputFunction,
) -> bool:
    value = _read_choice(prompt, {"y", "n"}, read, write)
    if value is None:
        raise _EditPaused
    return value == "y"


def _required_line(
    prompt: str,
    read: InputFunction,
    write: OutputFunction,
) -> str:
    while True:
        value = _read_line(prompt, read, write)
        if value is None or value in {_PAUSE_COMMAND, _EXIT_COMMAND}:
            raise _EditPaused
        if value.strip():
            return value.strip()
        write("Enter a non-empty value.")


def _edit_read(
    prompt: str,
    read: InputFunction,
    write: OutputFunction,
) -> str:
    value = _read_line(prompt, read, write)
    if value is None or value in {_PAUSE_COMMAND, _EXIT_COMMAND}:
        raise _EditPaused
    return value


def _read_line(
    prompt: str,
    read: InputFunction,
    write: OutputFunction,
) -> str | None:
    try:
        return read(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        write("Input ended. The current checkpoint was kept.")
        return None


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


def _display_value(value: object) -> str:
    if value is None or value == "" or value == []:
        return "(not set)"
    if isinstance(value, Mapping):
        return ", ".join(
            f"{key}: {_display_value(item)}" for key, item in value.items()
        )
    if isinstance(value, list):
        return "; ".join(_display_value(item) for item in value)
    return str(value)
