from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from harness_gen.interview import (
    _draft_from_interview,
    _initial_interview_data,
    _interview_fields,
    _interview_prompt,
    _is_coachable_field,
    _integer,
    _mapping,
    _normalize_interview_answer,
    _string_list,
)
from harness_gen.errors import HarnessError, SessionPersistenceError
from harness_gen.interview_assistance import InterviewAssistant
from harness_gen.provider_approval import (
    ProviderApproval,
    describe_provider,
    record_provider_approval,
)
from harness_gen.sessions import DatabaseSettings, SessionManager
from harness_gen.web import project_root
from harness_gen.webapp.models import get_provider_profile
from harness_gen.workflow import HarnessWorkflowState


@contextmanager
def session_manager(user: object) -> Iterator[SessionManager]:
    profile = get_provider_profile(user)
    with SessionManager(
        DatabaseSettings.from_environment(),
        owner_id=str(profile.user_id),
        provider_settings=profile.to_provider_settings(),
    ) as manager:
        yield manager


def ensure_interview_prompt(manager: SessionManager) -> HarnessWorkflowState:
    state = manager.state()
    if state.get("workflow_enabled") is True:
        return state
    if state.get("pending_prompt") is not None:
        return state
    interview = _mapping(state.get("interview_data"))
    if not interview:
        interview = _initial_interview_data()
        state = manager.update({"interview_data": interview})
    if interview.get("complete") is True:
        draft = _draft_from_interview(state, interview)
        manager.start_workflow(draft)
        return manager.state()
    answers = _mapping(interview.get("answers"))
    fields = _string_list(interview.get("fields"))
    index = _integer(interview.get("index", 0))
    if index >= len(fields):
        interview["complete"] = True
        manager.update({"interview_data": interview})
        return ensure_interview_prompt(manager)
    field = fields[index]
    pending_field = interview.get("pending_field")
    if pending_field is None:
        interview["pending_field"] = field
        manager.update({"interview_data": interview})
        return manager.request_input(_interview_prompt(field, answers))
    if pending_field != field:
        raise SessionPersistenceError(
            "The saved interview step is inconsistent. Restart the session."
        )
    return manager.request_input(_interview_prompt(field, answers))


def answer_interview(
    manager: SessionManager,
    answer: str,
) -> HarnessWorkflowState:
    state = manager.resume(answer)
    interview = _mapping(state.get("interview_data"))
    answers = _mapping(interview.get("answers"))
    field = str(interview.get("pending_field", ""))
    try:
        normalized = _normalize_interview_answer(field, answer)
    except (HarnessError, ValueError) as error:
        interview["pending_field"] = field
        manager.update(
            {
                "interview_data": interview,
                "last_response": None,
                "error_message": str(error),
            }
        )
        prompt = _interview_prompt(field, answers)
        return manager.request_input(prompt)

    answers[field] = normalized
    interview["answers"] = answers
    interview["pending_field"] = None
    interview["index"] = _integer(interview.get("index", 0)) + 1
    if field == "task_types":
        interview["fields"] = _interview_fields(_string_list(normalized))
    if field == "path_scoped_instructions" and normalized is True:
        interview["fields"] = [
            *_string_list(interview.get("fields")),
            "path_patterns",
        ]
    if field == "path_patterns":
        interview["answers"][field] = _string_list(normalized)
    if interview["index"] >= len(_string_list(interview.get("fields"))):
        interview["complete"] = True
    state = manager.update(
        {
            "interview_data": interview,
            "last_response": None,
            "error_message": "",
        }
    )
    return ensure_interview_prompt(manager)


def set_interview_assistance(
    manager: SessionManager,
    *,
    approved: bool,
) -> tuple[HarnessWorkflowState, str]:
    if manager.provider_settings is None:
        raise HarnessError("Interview assistance is unavailable.")
    disclosure = describe_provider(manager.provider_settings)
    if disclosure.requires_approval and not approved:
        return manager.state(), disclosure.render()
    approval = record_provider_approval(disclosure, approved=True)
    state = manager.state()
    interview = _mapping(state.get("interview_data"))
    interview["ai_assistance_enabled"] = True
    interview["ai_provider_approval"] = (
        None
        if approval is None
        else {
            "provider": approval.provider.value,
            "endpoint_fingerprint": disclosure._approval_scope,
        }
    )
    return manager.update({"interview_data": interview}), ""


def interview_approval(
    interview: Mapping[str, object],
    settings: object,
) -> ProviderApproval | None:
    from harness_gen.config import ProviderSettings

    if not isinstance(settings, ProviderSettings):
        raise HarnessError("The configured provider is unavailable.")
    if interview.get("ai_assistance_enabled") is not True:
        raise HarnessError("Enable interview assistance before requesting model help.")
    disclosure = describe_provider(settings)
    if not disclosure.requires_approval:
        return None
    saved = _mapping(interview.get("ai_provider_approval"))
    if (
        saved.get("provider") != disclosure.provider.value
        or saved.get("endpoint_fingerprint") != disclosure._approval_scope
    ):
        raise HarnessError(
            "Provider settings changed. Review and approve them before continuing."
        )
    return record_provider_approval(disclosure, approved=True)


def request_suggestions(manager: SessionManager) -> HarnessWorkflowState:
    state = manager.state()
    interview = _mapping(state.get("interview_data"))
    attempts = _integer(interview.get("suggestion_attempts", 0))
    if attempts >= 3:
        raise HarnessError("No more model suggestion attempts are available.")
    interview["suggestion_attempts"] = attempts + 1
    manager.update({"interview_data": interview, "last_response": None})
    approval = interview_approval(interview, manager.provider_settings)
    questions = InterviewAssistant().suggest_questions(
        manager.provider_settings,
        approval,
        str(state.get("request", "")),
        _mapping(interview.get("answers")),
        _deterministic_question_texts(),
    )
    interview["suggestions_generated"] = True
    interview["suggested_questions"] = list(questions)
    interview["suggestion_selection_pending"] = bool(questions)
    return manager.update({"interview_data": interview, "last_response": None})


def answer_supplemental(
    manager: SessionManager,
    selection: list[str],
    answer: str,
) -> HarnessWorkflowState:
    state = manager.state()
    interview = _mapping(state.get("interview_data"))
    if interview.get("suggestion_selection_pending") is True:
        questions = _string_list(interview.get("suggested_questions"))
        valid_choices = {str(i) for i in range(len(questions))}
        if (
            len(set(selection)) != len(selection)
            or any(item not in valid_choices for item in selection)
        ):
            raise HarnessError("Choose only the displayed optional questions.")
        interview["suggestion_selection_pending"] = False
        interview["supplemental_pending_questions"] = [
            questions[int(item)] for item in selection
        ]
        interview["supplemental_index"] = 0
        interview["supplemental_answers"] = interview.get(
            "supplemental_answers", []
        )
        manager.update({"interview_data": interview, "last_response": None})
        return manager.state()
    else:
        questions = _string_list(interview.get("supplemental_pending_questions"))
        index = _integer(interview.get("supplemental_index", 0))
        saved = interview.get("supplemental_answers")
        if not isinstance(saved, list):
            saved = []
        if index < len(questions) and answer.strip():
            saved.append({"question": questions[index], "answer": answer.strip()})
        interview["supplemental_answers"] = saved
        index += 1
        interview["supplemental_index"] = index
        if index < len(questions):
            manager.update({"interview_data": interview, "last_response": None})
            return manager.state()
        interview["supplemental_pending_questions"] = []
        interview["supplemental_index"] = 0
    state = manager.update({"interview_data": interview, "last_response": None})
    return ensure_interview_prompt(manager)


def coach_interview(manager: SessionManager, user_question: str) -> str:
    state = manager.state()
    prompt = _mapping(state.get("pending_prompt"))
    if (
        prompt.get("kind") != "requirements_interview"
        or not _is_coachable_field(str(prompt.get("field", "")))
        or not user_question.strip()
    ):
        raise HarnessError("Enter a coaching question for the current interview step.")
    interview = _mapping(state.get("interview_data"))
    approval = interview_approval(interview, manager.provider_settings)
    return InterviewAssistant().coach(
        manager.provider_settings,
        approval,
        str(state.get("request", "")),
        str(prompt.get("message", "")),
        user_question.strip(),
    )


def user_workspace(user: object) -> Path:
    user_id = getattr(user, "pk", None)
    if user_id is None:
        raise HarnessError("An authenticated account is required.")
    root = Path(
        os.environ.get("HARNESS_WORKSPACE_ROOT", str(project_root() / "workspaces"))
    ).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise HarnessError("The configured workspace root is not a safe directory.")
    os.chmod(root, 0o700)
    account_root = root / str(user_id)
    try:
        account_root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    account_stat = account_root.lstat()
    if stat.S_ISLNK(account_stat.st_mode) or not stat.S_ISDIR(account_stat.st_mode):
        raise HarnessError("The account workspace is not a safe directory.")
    os.chmod(account_root, 0o700)
    return account_root.resolve()


def _deterministic_question_texts() -> tuple[str, ...]:
    from harness_gen.question_templates import CORE_QUESTION_PROMPTS, TASK_PROFILES

    return tuple(
        [
            *CORE_QUESTION_PROMPTS.values(),
            *(
                question.prompt
                for profile in TASK_PROFILES.values()
                for question in profile.questions
            ),
        ]
    )
