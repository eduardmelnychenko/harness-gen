from __future__ import annotations

import logging
import stat
from collections.abc import Mapping
from pathlib import Path
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.template import engines
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from harness_gen.errors import HarnessError, RequirementsError
from harness_gen.requirements import requirements_from_state
from harness_gen.sessions import SessionManager
from harness_gen.webapp.forms import (
    InterviewAnswerForm,
    ProviderProfileForm,
    RequirementsEditForm,
    StartSessionForm,
)
from harness_gen.webapp.models import get_provider_profile
from harness_gen.webapp.services import (
    answer_interview,
    answer_supplemental,
    coach_interview,
    ensure_interview_prompt,
    request_suggestions,
    session_manager,
    set_interview_assistance,
    user_workspace,
)
from harness_gen.workflow import HarnessWorkflowState

_LOGGER = logging.getLogger("harness_gen.webapp")


def _render(
    request: HttpRequest,
    template_name: str,
    context: Mapping[str, object] | None = None,
    *,
    status: int = 200,
) -> HttpResponse:
    template = engines["jinja2"].get_template(template_name)
    render_context = dict(context or {})
    render_context["messages"] = list(get_messages(request))
    return HttpResponse(template.render(render_context, request), status=status)


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")
    form = AuthenticationForm(request, data=request.POST or None)
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control"
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.GET.get("next", "")
        if url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("home")
    return _render(request, "webapp/login.html", {"form": form})


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    logout(request)
    return redirect("login")


@login_required
@require_http_methods(["GET", "POST"])
def home(request: HttpRequest) -> HttpResponse:
    form = StartSessionForm(request.POST or None)
    try:
        with session_manager(request.user) as manager:
            state = manager.state() if manager.active_thread_id else None
            if request.method == "POST":
                if state is not None:
                    messages.error(
                        request,
                        "An active session already exists. Resume or restart it first.",
                    )
                elif form.is_valid():
                    request_text = str(form.cleaned_data["request"]).strip()
                    if not request_text:
                        form.add_error("request", "Enter a non-empty harness request.")
                    else:
                        from harness_gen.interview import _initial_interview_data

                        manager.start(
                            {
                                "request": request_text,
                                "interview_data": _initial_interview_data(),
                            }
                        )
                        _LOGGER.info("Browser session started")
                        ensure_interview_prompt(manager)
                        return redirect("session")
            return _render(
                request,
                "webapp/home.html",
                {"form": form, "state": state, "provider": manager.provider_settings},
            )
    except HarnessError as error:
        _LOGGER.error("Browser home request failed (%s)", type(error).__name__)
        messages.error(request, str(error))
        return redirect("provider-settings")


@login_required
@require_http_methods(["GET", "POST"])
def provider_settings(request: HttpRequest) -> HttpResponse:
    profile = get_provider_profile(request.user)
    form = ProviderProfileForm(
        request.POST or None,
        profile=profile,
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except HarnessError as error:
            form.add_error(None, str(error))
        else:
            _LOGGER.info("Account provider profile updated")
            messages.success(
                request,
                "Provider settings were saved. The API key is encrypted at rest and is not shown again.",
            )
            return redirect("home")
    return _render(request, "webapp/provider_settings.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def session_view(request: HttpRequest) -> HttpResponse:
    try:
        with session_manager(request.user) as manager:
            if manager.active_thread_id is None:
                return redirect("home")
            state = manager.state()
            extra: dict[str, object] = {}
            if request.method == "POST":
                action = request.POST.get("action", "")
                try:
                    state, extra = _handle_session_action(
                        request,
                        manager,
                        state,
                        action,
                    )
                except (
                    HarnessError,
                    RequirementsError,
                    DjangoValidationError,
                    ValueError,
                ) as error:
                    _LOGGER.warning(
                        "Browser session action failed (%s)", type(error).__name__
                    )
                    messages.error(request, str(error))
                    state = manager.state()
                if extra.get("disclosure"):
                    return _render(
                        request,
                        "webapp/provider_approval.html",
                        {
                            "disclosure": extra["disclosure"],
                            "approval_action": "approve-interview-provider",
                        },
                    )
                if extra.get("edit_form") is not None:
                    return _render(
                        request,
                        "webapp/requirements_edit.html",
                        {
                            "form": extra["edit_form"],
                            "state": state,
                        },
                    )
                if extra.get("redirect"):
                    return redirect(str(extra["redirect"]))
            interview = state.get("interview_data")
            interview_state = interview if isinstance(interview, Mapping) else {}
            if (
                state.get("workflow_enabled") is not True
                and state.get("pending_prompt") is None
                and interview_state.get("suggestion_selection_pending") is not True
                and not interview_state.get("supplemental_pending_questions")
            ):
                state = ensure_interview_prompt(manager)
            return _render(
                request,
                "webapp/session.html",
                {
                    **_session_context(state),
                    **extra,
                    "answer_form": InterviewAnswerForm(),
                    "workspace_choices": _workspace_choices(request.user),
                },
            )
    except HarnessError as error:
        _LOGGER.error("Browser session request failed (%s)", type(error).__name__)
        messages.error(request, str(error))
        return redirect("provider-settings")


def _handle_session_action(
    request: HttpRequest,
    manager: SessionManager,
    state: HarnessWorkflowState,
    action: str,
) -> tuple[HarnessWorkflowState, dict[str, object]]:
    if action == "answer-interview":
        form = InterviewAnswerForm(request.POST)
        if not form.is_valid():
            return state, {"answer_form": form}
        answer_interview(manager, str(form.cleaned_data["answer"]))
        return manager.state(), {}
    if action == "enable-assistance":
        manager.resume(":ai")
        state, disclosure = set_interview_assistance(manager, approved=False)
        if disclosure:
            return state, {"disclosure": disclosure}
        return ensure_interview_prompt(manager), {}
    if action == "approve-interview-provider":
        state, disclosure = set_interview_assistance(manager, approved=True)
        if disclosure:
            raise HarnessError("Provider approval could not be recorded.")
        messages.success(request, "Interview assistance is enabled for this session.")
        return ensure_interview_prompt(manager), {}
    if action == "suggest-questions":
        manager.resume(":suggest")
        return request_suggestions(manager), {}
    if action == "select-suggestions":
        selected = request.POST.getlist("question_ids")
        return answer_supplemental(manager, selected, ""), {}
    if action == "answer-supplemental":
        return answer_supplemental(
            manager,
            [],
            request.POST.get("answer", ""),
        ), {}
    if action == "coach":
        advice = coach_interview(manager, request.POST.get("question", ""))
        return state, {"coaching": advice}
    if action == "requirements-edit":
        return state, {
            "edit_form": RequirementsEditForm(state=state["requirements"])
        }
    if action == "requirements-save":
        form = RequirementsEditForm(request.POST, state=state["requirements"])
        if not form.is_valid():
            return state, {"edit_form": form}
        original = requirements_from_state(state["requirements"])
        try:
            correction = form.to_state(original)
        except (RequirementsError, ValueError) as error:
            form.add_error(None, str(error))
            return state, {"edit_form": form}
        return manager.resume({"requirements": correction["requirements"]}), {}
    if action == "requirements-confirm":
        return manager.resume({"approved": True}), {}
    if action == "provider-approval":
        return manager.resume({"approved": request.POST.get("approved") == "yes"}), {}
    if action == "destination":
        destination = _resolve_destination(
            request.user,
            request.POST.get("destination", ""),
        )
        return manager.resume({"destination": str(destination)}), {}
    if action == "write-approval":
        return manager.resume({"approved": request.POST.get("approved") == "yes"}), {}
    if action == "restart":
        manager.restart()
        _LOGGER.info("Browser session restarted")
        messages.success(request, "A fresh session was started from the saved requirements.")
        return manager.state(), {}
    if action == "delete":
        manager.delete()
        _LOGGER.info("Browser session deleted")
        messages.success(request, "The saved session was deleted. Project files were not changed.")
        return state, {"redirect": "home"}
    if action == "pause":
        messages.info(request, "The session is saved. You can resume it later.")
        return manager.pause(), {}
    raise HarnessError("This session action is not supported.")


def _session_context(state: HarnessWorkflowState) -> dict[str, object]:
    prompt = state.get("pending_prompt")
    if not isinstance(prompt, Mapping):
        prompt = {}
    interview = state.get("interview_data")
    if not isinstance(interview, Mapping):
        interview = {}
    blockers = prompt.get("blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    display_blockers = [
        item
        for item in blockers
        if item
        not in {
            "Review and confirm the requirements before generation.",
            "Confirm each exact relative path pattern.",
        }
    ]
    return {
        "state": state,
        "prompt": prompt,
        "phase": state.get("phase", ""),
        "interview": interview,
        "error_message": state.get("error_message", ""),
        "generated_files": state.get("generated_files", []),
        "review_summary": state.get("review_summary", []),
        "validation_findings": state.get("validation_findings", []),
        "display_blockers": display_blockers,
    }


def _workspace_choices(user: object) -> list[tuple[str, str]]:
    root = user_workspace(user)
    choices = [(".", "Account workspace")]
    for path in sorted(root.iterdir()):
        if path.is_dir() and not path.is_symlink():
            choices.append((path.name, path.name))
    return choices


def _resolve_destination(user: object, selection: str) -> Path:
    root = user_workspace(user)
    selected_path = Path(selection)
    if (
        not selection
        or selected_path.is_absolute()
        or "\x00" in selection
        or ".." in selected_path.parts
        or len(selected_path.parts) > 1
    ):
        raise HarnessError("Choose an existing project directory in your workspace.")
    candidate = root.joinpath(*selected_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise HarnessError("The selected project directory does not exist.") from None
    if not resolved.is_relative_to(root):
        raise HarnessError("The selected project directory is outside your workspace.")
    current = candidate
    while current != root:
        try:
            mode = current.lstat().st_mode
        except OSError:
            raise HarnessError("The selected project directory is not accessible.") from None
        if stat.S_ISLNK(mode):
            raise HarnessError("Symbolic-link project directories are not allowed.")
        current = current.parent
    if not resolved.is_dir():
        raise HarnessError("Choose an existing project directory.")
    return resolved
