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
from harness_gen.logging_events import log_stage_event, web_logging_scope
from harness_gen.question_templates import TASK_PROFILES
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
    with web_logging_scope():
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
    interview = _mapping(state.get("interview_data"))
    if not interview:
        interview = _initial_interview_data()
    prompt = _mapping(state.get("pending_prompt"))
    if "conversation" in interview and not isinstance(
        interview.get("conversation"), list
    ):
        raise SessionPersistenceError(
            "The saved interview conversation is invalid. Restart the session."
        )
    if "conversation" not in interview or not interview.get("conversation"):
        if prompt:
            manager.resume(":transcript")
            state = manager.state()
            interview = _mapping(state.get("interview_data")) or interview
        interview["conversation"] = _legacy_interview_conversation(state, interview)
        state = manager.update({"interview_data": interview})
    _validated_conversation(interview.get("conversation", []))
    if prompt:
        return manager.request_input(prompt) if not state.get("pending_prompt") else state
    if (
        interview.get("suggestion_selection_pending") is True
        or interview.get("supplemental_pending_questions")
    ):
        return state
    answers = _mapping(interview.get("answers"))
    if (
        interview.get("ai_assistance_enabled") is True
        and not interview.get("topic_interview_complete")
        and answers.get("task_types")
        and answers.get("target")
    ):
        if interview.get("topic_mode") is not True:
            interview["topic_mode"] = True
            interview["fields"] = [
                "purpose",
                "task_types",
                "target",
                "task_type_instructions",
                "path_scoped_instructions",
            ]
            interview["index"] = 3
            interview["pending_field"] = None
            state = manager.update({"interview_data": interview})
        return _request_topic_interview_turn(manager, state, interview)
    if interview.get("topic_mode") is True and not interview.get(
        "topic_interview_complete"
    ):
        return _request_topic_interview_turn(manager, state, interview)
    if interview.get("complete") is True:
        draft = _draft_from_interview(state, interview)
        manager.start_workflow(draft)
        return manager.state()
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
        prompt = _interview_prompt(field, answers)
        log_stage_event(
            "requirements_interview",
            "question_presented",
            location=field,
            text=prompt["message"],
        )
        _append_message(interview, "assistant", str(prompt["message"]))
        manager.update({"interview_data": interview})
        pending_field = field
    if pending_field != field:
        raise SessionPersistenceError(
            "The saved interview step is inconsistent. Restart the session."
        )
    prompt = _interview_prompt(field, answers)
    if not _has_last_message(interview, "assistant", str(prompt["message"])):
        log_stage_event(
            "requirements_interview",
            "question_presented",
            location=field,
            text=prompt["message"],
        )
        _append_message(interview, "assistant", str(prompt["message"]))
        manager.update({"interview_data": interview})
    return manager.request_input(prompt)


def answer_interview(
    manager: SessionManager,
    answer: str,
) -> HarnessWorkflowState:
    state = manager.resume(answer)
    interview = _mapping(state.get("interview_data"))
    if not interview.get("conversation"):
        interview["conversation"] = _legacy_interview_conversation(state, interview)
    answers = _mapping(interview.get("answers"))
    field = str(interview.get("pending_field", ""))
    log_stage_event(
        "requirements_interview",
        "user_answer",
        location=field,
        text=answer,
    )
    if interview.get("topic_mode") is True and field == "topic":
        if answer.strip():
            _append_message(interview, "user", answer.strip())
        interview["pending_field"] = None
        state = manager.update(
            {
                "interview_data": interview,
                "last_response": None,
                "error_message": "",
            }
        )
        return _request_topic_interview_turn(manager, state, interview)
    try:
        normalized = _normalize_interview_answer(field, answer)
    except (HarnessError, ValueError) as error:
        log_stage_event(
            "requirements_interview",
            "answer_rejected",
            code="invalid_interview_answer",
            location=field,
        )
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
    if answer.strip():
        _append_message(interview, "user", answer.strip())
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


def _request_topic_interview_turn(
    manager: SessionManager,
    state: HarnessWorkflowState,
    interview: dict[str, object],
) -> HarnessWorkflowState:
    answers = _mapping(interview.get("answers"))
    draft = _draft_from_interview(state, interview)
    mandatory_gaps = list(draft.provider_interview_blockers)
    transcript = _validated_conversation(interview.get("conversation", []))
    response = InterviewAssistant().interview_topic(
        manager.provider_settings,
        interview_approval(interview, manager.provider_settings),
        str(state.get("request", "")),
        _string_list(answers.get("task_types")),
        str(answers.get("target", "")),
        answers,
        transcript,
        mandatory_gaps,
    )
    for requirement in response.extracted_requirements:
        answers[requirement.key] = requirement.value
    interview["answers"] = answers
    interview["unresolved_choices"] = response.unresolved_choices

    draft = _draft_from_interview(state, interview)
    mandatory_gaps = list(draft.provider_interview_blockers)
    next_question = response.next_question
    remaining_gaps = [*mandatory_gaps, *response.unresolved_choices]
    if response.sufficient and remaining_gaps:
        next_question = _question_for_interview_gap(remaining_gaps[0])
    if response.sufficient and not remaining_gaps:
        interview["topic_interview_complete"] = True
        interview["pending_field"] = None
        interview["index"] = 3
        interview["fields"] = [
            "purpose",
            "task_types",
            "target",
            "task_type_instructions",
            "path_scoped_instructions",
        ]
        state = manager.update(
            {"interview_data": interview, "last_response": None}
        )
        return ensure_interview_prompt(manager)
    if next_question is None:
        raise HarnessError(
            "The model did not ask a follow-up question for an incomplete "
            "requirements interview. Your answers were not changed."
        )
    interview["pending_field"] = "topic"
    log_stage_event(
        "requirements_interview",
        "question_presented",
        location="topic",
        text=next_question,
    )
    _append_message(interview, "assistant", next_question)
    prompt = {
        "kind": "requirements_interview",
        "field": "topic",
        "message": next_question,
    }
    manager.update(
        {"interview_data": interview, "last_response": None}
    )
    return manager.request_input(prompt)


def _question_for_interview_gap(gap: str) -> str:
    if gap == "Confirm the data source for the analysis.":
        return "Which data source will the analysis use?"
    if gap == "Confirm the goal of the analysis.":
        return "What question must the analysis answer?"
    if gap == "Assess whether the clarified custom task is supported.":
        return "Can the clarified custom task be supported by harness instructions?"
    for profile in TASK_PROFILES.values():
        for question in profile.questions:
            if question.key.replace("_", " ") not in gap:
                continue
            return question.prompt
    return f"How should we resolve this material choice: {gap}?"


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
    pending_prompt = _mapping(state.get("pending_prompt"))
    answers = _mapping(interview.get("answers"))
    pending_field = pending_prompt.get("field") or interview.get("pending_field")
    if (
        pending_field == "purpose"
        and not answers.get("purpose")
    ):
        request = str(state.get("request", "")).strip()
        if not request:
            raise SessionPersistenceError(
                "The saved harness request is missing. Restart the session."
            )
        if pending_prompt:
            state = manager.resume(":topic-interview")
            interview = _mapping(state.get("interview_data"))
            answers = _mapping(interview.get("answers"))
        answers["purpose"] = request
        interview["answers"] = answers
        conversation = _validated_conversation(
            interview.get("conversation", [])
        )
        purpose_prompt = str(_interview_prompt("purpose", answers)["message"])
        if (
            conversation
            and conversation[-1]["role"] == "assistant"
            and conversation[-1]["content"] == purpose_prompt
        ):
            conversation.pop()
        interview["conversation"] = conversation
        interview["index"] = max(_integer(interview.get("index", 0)), 1)
        interview["pending_field"] = None
    interview["ai_assistance_enabled"] = True
    interview["ai_provider_approval"] = (
        None
        if approval is None
        else {
            "provider": approval.provider.value,
            "endpoint_fingerprint": disclosure._approval_scope,
        }
    )
    return manager.update(
        {"interview_data": interview, "last_response": None}
    ), ""


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
    if not interview.get("conversation"):
        interview["conversation"] = _legacy_interview_conversation(state, interview)
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
    log_stage_event(
        "requirements_interview",
        "suggestions_selected",
        text=questions,
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
    if not interview.get("conversation"):
        interview["conversation"] = _legacy_interview_conversation(state, interview)
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
        if interview["supplemental_pending_questions"]:
            log_stage_event(
                "requirements_interview",
                "question_presented",
                location="supplemental",
                text=interview["supplemental_pending_questions"][0],
            )
            _append_message(
                interview,
                "assistant",
                str(interview["supplemental_pending_questions"][0]),
            )
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
            log_stage_event(
                "requirements_interview",
                "supplemental_answer",
                location=questions[index],
                text=answer,
            )
            saved.append({"question": questions[index], "answer": answer.strip()})
            _append_message(interview, "user", answer.strip())
        interview["supplemental_answers"] = saved
        index += 1
        interview["supplemental_index"] = index
        if index < len(questions):
            _append_message(interview, "assistant", questions[index])
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


def chat_interview(
    manager: SessionManager,
    user_question: str,
) -> HarnessWorkflowState:
    question = user_question.strip()
    if not question:
        raise HarnessError("Enter a question about the harness interview.")
    state = manager.state()
    if state.get("workflow_enabled") is True or state.get("outcome"):
        raise HarnessError("Interview chat is not available at this workflow step.")
    interview = _mapping(state.get("interview_data"))
    if not interview.get("conversation"):
        interview["conversation"] = _legacy_interview_conversation(state, interview)
    prompt = _mapping(state.get("pending_prompt"))
    if not prompt and not (
        interview.get("suggestion_selection_pending") is True
        or interview.get("supplemental_pending_questions")
    ):
        raise HarnessError("Interview chat is not available at this workflow step.")
    transcript = _validated_conversation(interview.get("conversation", []))
    active_question = str(prompt.get("message", ""))
    if not active_question:
        pending = _string_list(interview.get("supplemental_pending_questions"))
        index = _integer(interview.get("supplemental_index", 0))
        active_question = (
            pending[index]
            if index < len(pending)
            else "Choose optional interview questions."
        )
    approval = interview_approval(interview, manager.provider_settings)
    log_stage_event(
        "interview_chat",
        "user_question",
        location="active_interview_question",
        text=question,
    )
    advice = InterviewAssistant().answer_question(
        manager.provider_settings,
        approval,
        str(state.get("request", "")),
        active_question,
        transcript,
        question,
    )
    log_stage_event("interview_chat", "assistant_response", text=advice)
    if prompt:
        manager.resume(":chat")
        state = manager.state()
        interview = _mapping(state.get("interview_data"))
        interview["conversation"] = transcript
    _append_message(interview, "user", question)
    _append_message(interview, "assistant", advice)
    state = manager.update(
        {"interview_data": interview, "last_response": None}
    )
    if prompt:
        return manager.request_input(prompt)
    return state


def _legacy_interview_conversation(
    state: Mapping[str, object],
    interview: Mapping[str, object],
) -> list[dict[str, str]]:
    conversation: list[dict[str, str]] = []
    request = state.get("request")
    if isinstance(request, str) and request.strip():
        conversation.append({"role": "user", "content": request})
    answers = _mapping(interview.get("answers"))
    known_answers: dict[str, object] = {}
    pending_field = interview.get("pending_field")
    for field in _string_list(interview.get("fields")):
        if field not in answers and field != pending_field:
            continue
        prompt = _interview_prompt(field, known_answers)
        conversation.append(
            {"role": "assistant", "content": str(prompt["message"])}
        )
        if field in answers:
            answer = answers[field]
            formatted_answer = _format_interview_answer(answer)
            if formatted_answer.strip():
                conversation.append({"role": "user", "content": formatted_answer})
            known_answers[field] = answer
        else:
            break
    optional_answers = interview.get("supplemental_answers", [])
    if isinstance(optional_answers, list):
        for item in optional_answers:
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("question"), str)
                and isinstance(item.get("answer"), str)
            ):
                conversation.extend(
                    (
                        {"role": "assistant", "content": item["question"]},
                        {"role": "user", "content": item["answer"]},
                    )
                )
    pending_questions = _string_list(
        interview.get("supplemental_pending_questions")
    )
    pending_index = _integer(interview.get("supplemental_index", 0))
    if pending_index < len(pending_questions):
        conversation.append(
            {"role": "assistant", "content": pending_questions[pending_index]}
        )
    return conversation


def _validated_conversation(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise SessionPersistenceError(
            "The saved interview conversation is invalid. Restart the session."
        )
    conversation: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("role"), str)
            or item.get("role") not in {"assistant", "user"}
            or not isinstance(item.get("content"), str)
            or not item["content"].strip()
        ):
            raise SessionPersistenceError(
                "The saved interview conversation is invalid. Restart the session."
            )
        conversation.append(
            {"role": str(item["role"]), "content": item["content"]}
        )
    return conversation


def _append_message(
    interview: dict[str, object],
    role: str,
    content: str,
) -> None:
    conversation = _validated_conversation(interview.get("conversation", []))
    conversation.append({"role": role, "content": content})
    interview["conversation"] = conversation


def _has_last_message(
    interview: Mapping[str, object],
    role: str,
    content: str,
) -> bool:
    conversation = _validated_conversation(interview.get("conversation", []))
    return bool(
        conversation
        and conversation[-1]["role"] == role
        and conversation[-1]["content"] == content
    )


def _format_interview_answer(answer: object) -> str:
    if isinstance(answer, list) and all(isinstance(item, str) for item in answer):
        return "; ".join(answer)
    if isinstance(answer, bool):
        return "Yes" if answer else "No"
    return str(answer)


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
