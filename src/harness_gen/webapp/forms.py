from __future__ import annotations

from dataclasses import replace

from django import forms

from harness_gen.config import Provider, ProviderSettings
from harness_gen.errors import HarnessError
from harness_gen.path_rules import validate_relative_glob
from harness_gen.question_templates import TASK_PROFILES
from harness_gen.requirements import (
    ConfirmedPathPattern,
    CustomTaskSupport,
    RequirementValue,
    RequirementsDraft,
    SetupRequirements,
    SupplementalAnswer,
    TaskAnswer,
    requirements_from_state,
)
from harness_gen.targets import AgentTarget, parse_agent_target
from harness_gen.tasks import TaskType
from harness_gen.webapp.models import PROVIDER_CHOICES, ProviderProfile

TASK_TYPE_LABELS = {
    TaskType.SOFTWARE_DEVELOPMENT: "Software development",
    TaskType.MACHINE_LEARNING: "Machine learning",
    TaskType.DATA_SCIENCE: "Data science",
    TaskType.CUSTOM: "Other/custom",
}
TASK_TYPE_CHOICES = [(item.value, TASK_TYPE_LABELS[item]) for item in TaskType]
TARGET_CHOICES = [
    (AgentTarget.CLAUDE_CODE.value, "Claude Code"),
    (AgentTarget.GITHUB_COPILOT.value, "GitHub Copilot"),
]
CUSTOM_SUPPORT_CHOICES = [
    (CustomTaskSupport.NOT_ASSESSED.value, "Not assessed"),
    (CustomTaskSupport.SUPPORTED.value, "Supported"),
    (CustomTaskSupport.UNSUPPORTED.value, "Unsupported"),
]


class _StyledForm(forms.Form):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        _style_fields(self)


class LoginForm(_StyledForm):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class StartSessionForm(_StyledForm):
    request = forms.CharField(
        label="Describe the harness instructions you want to create",
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=20_000,
    )


class InterviewAnswerForm(_StyledForm):
    answer = forms.CharField(
        label="Your answer",
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=20_000,
        required=False,
    )


class ProviderProfileForm(_StyledForm):
    provider = forms.ChoiceField(choices=PROVIDER_CHOICES)
    model_name = forms.CharField(max_length=200, required=False)
    endpoint = forms.URLField(
        label="Custom endpoint (where supported)",
        max_length=500,
        required=False,
        help_text="Used by Ollama, OpenAI, Azure OpenAI, and Meta's hosted API.",
    )
    api_version = forms.CharField(max_length=100, required=False)
    api_key = forms.CharField(
        label="API key (leave blank to keep the saved key)",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )
    clear_api_key = forms.BooleanField(required=False, label="Remove saved API key")

    def __init__(self, *args: object, profile: ProviderProfile, **kwargs: object) -> None:
        self.profile = profile
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.update(
                {
                    "provider": profile.provider,
                    "model_name": profile.model_name,
                    "endpoint": profile.endpoint,
                    "api_version": profile.api_version,
                }
            )

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if self.errors:
            return cleaned
        secret = cleaned.get("api_key") or ""
        if cleaned.get("clear_api_key"):
            secret = ""
        elif not secret:
            try:
                secret = self.profile.decrypt_api_key() or ""
            except HarnessError as error:
                self.add_error(None, str(error))
                return cleaned
        values = {
            "HARNESS_LLM_PROVIDER": str(cleaned["provider"]),
            "HARNESS_LLM_MODEL": str(cleaned.get("model_name", "")),
        }
        provider = str(cleaned["provider"])
        if (
            provider
            in {Provider.GOOGLE_GEMINI.value, Provider.ANTHROPIC.value}
            and cleaned.get("endpoint")
        ):
            self.add_error(
                "endpoint",
                "This provider uses its configured default endpoint.",
            )
            return cleaned
        if provider == Provider.OLLAMA.value:
            values["HARNESS_OLLAMA_BASE_URL"] = str(cleaned.get("endpoint") or "http://localhost:11434")
        elif provider == Provider.OPENAI.value:
            values["OPENAI_API_KEY"] = secret
            values["HARNESS_OPENAI_BASE_URL"] = str(cleaned.get("endpoint") or "https://api.openai.com/v1")
        elif provider == Provider.AZURE_OPENAI.value:
            values["AZURE_OPENAI_API_KEY"] = secret
            values["AZURE_OPENAI_ENDPOINT"] = str(cleaned.get("endpoint", ""))
            values["AZURE_OPENAI_API_VERSION"] = str(cleaned.get("api_version") or "2024-10-21")
        elif provider == Provider.META.value:
            values["LLAMA_API_KEY"] = secret
            values["HARNESS_META_BASE_URL"] = str(cleaned.get("endpoint") or "https://api.llama.com/compat/v1")
        elif provider == Provider.GOOGLE_GEMINI.value:
            values["GOOGLE_API_KEY"] = secret
        elif provider == Provider.ANTHROPIC.value:
            values["ANTHROPIC_API_KEY"] = secret
        try:
            self.provider_settings = ProviderSettings.from_environment(values)
        except HarnessError as error:
            self.add_error(None, str(error))
        return cleaned

    def save(self) -> ProviderProfile:
        self.profile.provider = str(self.cleaned_data["provider"])
        self.profile.model_name = str(self.cleaned_data.get("model_name") or "")
        self.profile.endpoint = str(self.cleaned_data.get("endpoint") or "")
        self.profile.api_version = str(self.cleaned_data.get("api_version") or "")
        if self.cleaned_data.get("clear_api_key"):
            self.profile.encrypted_api_key = ""
        elif self.cleaned_data.get("api_key"):
            self.profile.encrypt_api_key(str(self.cleaned_data["api_key"]))
        self.profile.save()
        return self.profile


class RequirementsEditForm(_StyledForm):
    request = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=20_000)
    purpose = forms.CharField(max_length=10_000)
    task_types = forms.MultipleChoiceField(choices=TASK_TYPE_CHOICES)
    target = forms.ChoiceField(choices=TARGET_CHOICES)
    operating_context = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    task_answers = forms.CharField(
        required=False,
        label="Task-specific answers (one key: answer per line)",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    supplemental_answers = forms.CharField(
        required=False,
        label="Optional interview answers (one question: answer per line)",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    constraints = forms.CharField(required=False, help_text="Separate items with semicolons.")
    desired_outputs = forms.CharField(required=False, help_text="Separate items with semicolons.")
    platform = forms.CharField(required=False)
    runtime = forms.CharField(required=False)
    data_source = forms.CharField(required=False)
    analysis_goal = forms.CharField(required=False)
    dependencies = forms.CharField(required=False)
    configuration = forms.CharField(required=False)
    verification = forms.CharField(required=False)
    task_type_instructions = forms.BooleanField(required=False)
    path_scoped_instructions = forms.BooleanField(required=False)
    path_patterns = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    unresolved_choices = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    custom_task_support = forms.ChoiceField(choices=CUSTOM_SUPPORT_CHOICES)

    def __init__(self, *args: object, state: dict[str, object], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        draft = requirements_from_state(state)
        if not self.is_bound:
            self.initial.update(_requirements_initial(draft))

    def to_state(self, original: RequirementsDraft) -> dict[str, object]:
        data = self.cleaned_data
        task_types = tuple(TaskType(item) for item in data["task_types"])
        profile_keys = {
            question.key
            for task_type in task_types
            for question in TASK_PROFILES[task_type].questions
        }
        answers = _parse_pairs(str(data.get("task_answers", "")), "task-specific answer")
        task_answers = tuple(
            TaskAnswer(key, value, confirmed=True)
            for key, value in answers
            if key in profile_keys
        )
        supplemental = tuple(
            SupplementalAnswer(question, answer)
            for question, answer in _parse_pairs(
                str(data.get("supplemental_answers", "")), "optional answer"
            )
        )
        path_patterns = tuple(
            ConfirmedPathPattern(validate_relative_glob(item), confirmed=True)
            for item in _exact_lines(str(data.get("path_patterns", "")))
        )
        return {
            "request": str(data["request"]).strip(),
            "requirements": _draft_state(
                replace(
                    original,
                    request=str(data["request"]).strip(),
                    purpose=RequirementValue(str(data["purpose"]).strip(), confirmed=True),
                    task_types=task_types,
                    target=parse_agent_target(str(data["target"])),
                    operating_context=_optional_value(str(data.get("operating_context", ""))),
                    task_answers=task_answers,
                    supplemental_answers=supplemental,
                    constraints=_confirmed_list(str(data.get("constraints", ""))),
                    desired_outputs=_confirmed_list(str(data.get("desired_outputs", ""))),
                    setup=SetupRequirements(
                        platform=_optional_value(str(data.get("platform", ""))),
                        runtime=_optional_value(str(data.get("runtime", ""))),
                        data_source=_optional_value(str(data.get("data_source", ""))),
                        analysis_goal=_optional_value(str(data.get("analysis_goal", ""))),
                        dependencies=_confirmed_list(str(data.get("dependencies", ""))),
                        configuration=_confirmed_list(str(data.get("configuration", ""))),
                        verification=_confirmed_list(str(data.get("verification", ""))),
                    ),
                    task_type_instructions=bool(data.get("task_type_instructions")),
                    path_scoped_instructions=bool(data.get("path_scoped_instructions")),
                    path_patterns=path_patterns,
                    unresolved_choices=tuple(
                        _lines(str(data.get("unresolved_choices", "")))
                    ),
                    custom_task_support=CustomTaskSupport(str(data["custom_task_support"])),
                    requirements_reviewed=False,
                )
            ),
        }


def _requirements_initial(draft: RequirementsDraft) -> dict[str, object]:
    return {
        "request": draft.request,
        "purpose": draft.purpose.value if draft.purpose else "",
        "task_types": [item.value for item in draft.task_types],
        "target": draft.target.value if draft.target else AgentTarget.CLAUDE_CODE.value,
        "operating_context": draft.operating_context.value if draft.operating_context else "",
        "task_answers": "\n".join(
            f"{item.key}: {item.value or ''}" for item in draft.task_answers
        ),
        "supplemental_answers": "\n".join(
            f"{item.question}: {item.answer}" for item in draft.supplemental_answers
        ),
        "constraints": "; ".join(item.value or "" for item in draft.constraints),
        "desired_outputs": "; ".join(item.value or "" for item in draft.desired_outputs),
        "platform": draft.setup.platform.value if draft.setup.platform else "",
        "runtime": draft.setup.runtime.value if draft.setup.runtime else "",
        "data_source": draft.setup.data_source.value if draft.setup.data_source else "",
        "analysis_goal": draft.setup.analysis_goal.value if draft.setup.analysis_goal else "",
        "dependencies": "; ".join(item.value or "" for item in draft.setup.dependencies),
        "configuration": "; ".join(item.value or "" for item in draft.setup.configuration),
        "verification": "; ".join(item.value or "" for item in draft.setup.verification),
        "task_type_instructions": draft.task_type_instructions,
        "path_scoped_instructions": draft.path_scoped_instructions,
        "path_patterns": "\n".join(item.pattern for item in draft.path_patterns),
        "unresolved_choices": "\n".join(draft.unresolved_choices),
        "custom_task_support": draft.custom_task_support.value,
    }


def _draft_state(draft: RequirementsDraft) -> dict[str, object]:
    from harness_gen.requirements import requirements_to_state

    return requirements_to_state(draft)


def _optional_value(value: str) -> RequirementValue | None:
    value = value.strip()
    return RequirementValue(value, confirmed=True) if value else None


def _confirmed_list(value: str) -> tuple[RequirementValue, ...]:
    return tuple(
        RequirementValue(item.strip(), confirmed=True)
        for item in value.split(";")
        if item.strip()
    )


def _lines(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]


def _exact_lines(value: str) -> list[str]:
    return [item for item in value.splitlines() if item]


def _parse_pairs(value: str, label: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in _lines(value):
        key, separator, answer = line.partition(":")
        if not separator or not key.strip() or not answer.strip():
            raise forms.ValidationError(
                f"Enter each {label} as key: value, one per line."
            )
        key = key.strip()
        if key in seen:
            raise forms.ValidationError(f"Each {label} key must be unique.")
        seen.add(key)
        pairs.append((key, answer.strip()))
    return pairs


def _style_fields(form: forms.Form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            css_class = "form-check-input"
        elif isinstance(field.widget, forms.Select):
            css_class = "form-select"
        else:
            css_class = "form-control"
        existing = field.widget.attrs.get("class", "")
        field.widget.attrs["class"] = f"{existing} {css_class}".strip()
