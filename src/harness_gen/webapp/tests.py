from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from harness_gen.artifacts import GeneratedFile, GenerationResult
from harness_gen.config import ProviderSettings
from harness_gen.errors import HarnessError
from harness_gen.generation import plan_generation
from harness_gen.interview_assistance import TopicInterviewTurn
from harness_gen.requirements import RequirementsDraft
from harness_gen.sessions import DatabaseSettings, SessionManager
from harness_gen.targets import ArtifactKind
from harness_gen.validation import confirmed_details, validate_generated_files
from harness_gen.webapp.forms import ProviderProfileForm
from harness_gen.webapp.models import ProviderProfile
from harness_gen.webapp.services import user_workspace
from harness_gen.webapp.views import _resolve_destination


class _FixedGenerator:
    last_requirements: RequirementsDraft | None = None

    def generate(
        self,
        requirements: RequirementsDraft,
        provider_settings: ProviderSettings,
        approval: object,
    ) -> GenerationResult:
        type(self).last_requirements = requirements
        plan = plan_generation(requirements)
        confirmed = "\n".join(confirmed_details(requirements))
        files = []
        for artifact in plan.artifacts:
            if artifact.kind is ArtifactKind.CORE_INSTRUCTIONS:
                content = f"# Core instructions\nRead AGENTS.md.\n{confirmed}"
            elif artifact.kind is ArtifactKind.SHARED_CONTEXT:
                content = (
                    "# Shared project context\nThis file provides shared project "
                    f"context and instructions. It is not target settings.\n{confirmed}"
                )
            else:
                content = artifact.frontmatter + "Apply confirmed requirements."
            files.append(
                GeneratedFile(
                    artifact_id=artifact.artifact_id,
                    path=artifact.path,
                    kind=artifact.kind,
                    purpose=artifact.purpose,
                    content=content,
                )
            )
        output_files = tuple(files)
        return GenerationResult(
            plan,
            output_files,
            validate_generated_files(plan, output_files),
            attempts=1,
        )


class WebApplicationTests(TestCase):
    def setUp(self) -> None:
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            username="web-user",
            password="test-password-not-for-production",
        )

    def test_home_requires_authentication(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_state_changing_requests_require_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            "/accounts/login/",
            {"username": "web-user", "password": "incorrect"},
        )

        self.assertEqual(response.status_code, 403)

    def test_authenticated_home_uses_jinja_and_is_account_scoped(self) -> None:
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "checkpoint.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{db}",
                    "HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces"),
                },
            ):
                response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create harness instructions")
        self.assertContains(response, "Start requirements interview")
        self.assertContains(response, "sticky-top")

    def test_starting_session_opens_deterministic_interview_in_browser(self) -> None:
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "checkpoint.sqlite3"
            workspaces = Path(directory) / "workspaces"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{db}",
                    "HARNESS_WORKSPACE_ROOT": str(workspaces),
                },
            ):
                response = self.client.post(
                    "/",
                    {"request": "Create clear instructions for the team."},
                )
                self.assertRedirects(response, "/session/")
                response = self.client.get("/session/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Interview chat")
        self.assertContains(response, "shared with the operator")
        self.assertContains(response, "Create clear instructions for the team.")
        self.assertContains(response, "What is the main purpose of the harness?")
        self.assertContains(response, "Your answer")

    def test_fixed_interview_answers_use_clickable_choices(self) -> None:
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{database}",
                    "HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces"),
                },
            ):
                response = self.client.post(
                    "/",
                    {"request": "Create instructions for a data analysis team."},
                )
                self.assertRedirects(response, "/session/")

                response = self.client.post(
                    "/session/",
                    {
                        "action": "answer-interview",
                        "answer": "Create clear instructions for this work.",
                    },
                )
                self.assertContains(response, 'type="checkbox"')
                self.assertContains(response, 'value="1"')
                self.assertContains(response, "Other/custom")
                self.assertNotContains(response, 'id="id_answer"')

                response = self.client.post(
                    "/session/",
                    {
                        "action": "answer-interview",
                        "answer": ["1", "3", "4"],
                    },
                )
                self.assertContains(response, "Choose the agent target.")
                self.assertContains(response, 'type="radio"')
                self.assertContains(response, "GitHub Copilot")
                self.assertNotContains(response, 'id="id_answer"')

                response = self.client.post(
                    "/session/",
                    {"action": "answer-interview", "answer": "Ollama"},
                )
                self.assertContains(
                    response,
                    "That name is an LLM provider, not an agent target.",
                )
                self.assertContains(response, "Choose the agent target.")

                response = self.client.post(
                    "/session/",
                    {"action": "answer-interview", "answer": "2"},
                )
                self.assertContains(response, "Your answer")

                for answer in (
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ):
                    response = self.client.post(
                        "/session/",
                        {"action": "answer-interview", "answer": answer},
                    )

                self.assertContains(response, "Can the clarified custom task")
                self.assertContains(response, "Unsure")
                self.assertNotContains(response, 'id="id_answer"')

                response = self.client.post(
                    "/session/",
                    {"action": "answer-interview", "answer": "unsure"},
                )
                self.assertContains(response, "Create separate task-type instruction files?")
                self.assertContains(response, "Yes")
                self.assertContains(response, "No")
                self.assertNotContains(response, 'id="id_answer"')

                response = self.client.post(
                    "/session/",
                    {"action": "answer-interview", "answer": "no"},
                )
                self.assertContains(response, "Create path-scoped instruction files?")
                self.assertContains(response, "Yes")
                self.assertContains(response, "No")
                self.assertNotContains(response, 'id="id_answer"')

    def test_topic_interview_cannot_stop_before_data_science_readiness(self) -> None:
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{database}",
                    "HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces"),
                },
            ):
                self.client.post(
                    "/",
                    {"request": "Analyze the customer dataset."},
                )
                response = self.client.post(
                    "/session/",
                    {"action": "enable-assistance"},
                )
                self.assertContains(
                    response,
                    "Select one or more task types.",
                )
                self.assertNotContains(
                    response,
                    "What is the main purpose of the harness?",
                )
                with patch(
                    "harness_gen.webapp.services.InterviewAssistant.interview_topic",
                    return_value=TopicInterviewTurn(
                        sufficient=True,
                        next_question=None,
                        extracted_requirements=[],
                        unresolved_choices=[],
                    ),
                ):
                    for answer in ("3", "2"):
                        response = self.client.post(
                            "/session/",
                            {"action": "answer-interview", "answer": answer},
                        )

        self.assertContains(response, "Which data source will the analysis use?")
        self.assertNotContains(response, "Review requirements")

    def test_legacy_interview_checkpoint_renders_reconstructed_chat_history(self) -> None:
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{database}",
                    "HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces"),
                },
            ):
                with SessionManager(
                    DatabaseSettings.from_environment(),
                    owner_id=str(self.user.pk),
                ) as manager:
                    manager.start(
                        {
                            "request": "Create instructions for a service.",
                            "interview_data": {
                                "answers": {"purpose": "Preserve API behavior."},
                                "fields": ["purpose", "task_types"],
                                "index": 1,
                                "pending_field": "task_types",
                                "complete": False,
                            },
                        }
                    )

                response = self.client.get("/session/")

        self.assertContains(response, "Interview chat")
        self.assertContains(response, "Create instructions for a service.")
        self.assertContains(response, "What is the main purpose of the harness?")
        self.assertContains(response, "Preserve API behavior.")

    def test_completed_interview_opens_review_and_edit_pages(self) -> None:
        self.client.force_login(self.user)
        _FixedGenerator.last_requirements = None
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "checkpoint.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "HARNESS_DB_URL": f"sqlite:///{db}",
                    "HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces"),
                },
            ):
                with patch(
                    "harness_gen.sessions.HarnessGenerator",
                    _FixedGenerator,
                ):
                    workspace = user_workspace(self.user)
                    project = workspace / "project"
                    project.mkdir()
                    self.client.post(
                        "/",
                        {"request": "Create accurate instructions for an API team."},
                    )
                    with patch(
                        "harness_gen.webapp.services.InterviewAssistant.answer_question",
                        return_value="Use the confirmed requirements as the source of truth.",
                    ) as answer_question:
                        response = self.client.post(
                            "/session/",
                            {
                                "action": "chat-interview",
                                "question": "How should I handle open choices?",
                            },
                        )
                        self.assertContains(
                            response,
                            "Enable interview assistance before requesting model help.",
                        )
                        answer_question.assert_not_called()

                        response = self.client.post(
                            "/session/",
                            {"action": "enable-assistance"},
                        )
                        self.assertContains(response, "Ask the assistant a question")
                        response = self.client.post(
                            "/session/",
                            {
                                "action": "chat-interview",
                                "question": "How should I handle open choices?",
                            },
                        )
                        self.assertContains(
                            response,
                            "How should I handle open choices?",
                        )
                        self.assertContains(
                            response,
                            "Use the confirmed requirements as the source of truth.",
                        )
                        answer_question.assert_called_once()
                        response = self.client.post(
                            "/session/",
                            {"action": "pause"},
                        )
                        self.assertContains(
                            response,
                            "Use the confirmed requirements as the source of truth.",
                        )

                    with patch(
                        "harness_gen.webapp.services.InterviewAssistant.interview_topic",
                        side_effect=(
                            TopicInterviewTurn(
                                sufficient=False,
                                next_question=(
                                    "Which important workflow must the harness support?"
                                ),
                                extracted_requirements=[
                                    {
                                        "key": "workflows",
                                        "value": "Maintain API compatibility.",
                                    },
                                    {"key": "runtime", "value": "Python"},
                                    {"key": "verification", "value": "Unit tests"},
                                ],
                                unresolved_choices=[],
                            ),
                            TopicInterviewTurn(
                                sufficient=True,
                                next_question=None,
                                extracted_requirements=[],
                                unresolved_choices=[],
                            ),
                        ),
                    ) as interview_topic:
                        for answer in ("1", "2"):
                            response = self.client.post(
                                "/session/",
                                {"action": "answer-interview", "answer": answer},
                            )
                            self.assertEqual(response.status_code, 200)
                        response = self.client.post(
                            "/session/",
                            {"action": "pause"},
                        )
                        self.assertContains(
                            response,
                            "Which important workflow must the harness support?",
                        )
                        response = self.client.get("/session/")
                        self.assertContains(
                            response,
                            "Which important workflow must the harness support?",
                        )
                        for answer in ("Maintain API compatibility.", "n", "n"):
                            response = self.client.post(
                                "/session/",
                                {"action": "answer-interview", "answer": answer},
                            )
                            self.assertEqual(response.status_code, 200)
                        interview_topic.assert_called()

                    response = self.client.get("/session/")
                    self.assertContains(response, "Review requirements")
                    self.assertContains(response, "Confirm requirements")

                    response = self.client.post(
                        "/session/",
                        {"action": "requirements-edit"},
                    )
                    self.assertContains(response, "Edit reviewed requirements")
                    response = self.client.post(
                        "/session/",
                        {
                            "action": "requirements-save",
                            "request": "Create accurate instructions for an API team.",
                            "purpose": "Preserve the confirmed public API.",
                            "task_types": ["software_development"],
                            "target": "github_copilot",
                            "operating_context": "",
                            "task_answers": "workflows: Maintain API compatibility.",
                            "supplemental_answers": "",
                            "constraints": "",
                            "desired_outputs": "",
                            "platform": "",
                            "runtime": "Python",
                            "data_source": "",
                            "analysis_goal": "",
                            "dependencies": "",
                            "configuration": "",
                            "verification": "Unit tests",
                            "path_patterns": "",
                            "unresolved_choices": "",
                            "custom_task_support": "not_assessed",
                        },
                    )
                    self.assertContains(response, "Review requirements")
                    self.assertContains(
                        response,
                        "Preserve the confirmed public API.",
                    )
                    response = self.client.post(
                        "/session/",
                        {"action": "requirements-confirm"},
                    )
                    self.assertContains(response, "Review generated files")
                    response = self.client.post(
                        "/session/",
                        {"action": "destination", "destination": "project"},
                    )
                    self.assertContains(response, "Approve project changes")
                    self.assertContains(response, "Create: AGENTS.md")
                    response = self.client.post(
                        "/session/",
                        {"action": "write-approval", "approved": "yes"},
                    )
                    self.assertContains(response, "Files were written")
                    self.assertTrue((project / "AGENTS.md").is_file())
                    self.assertTrue(
                        (project / ".github" / "copilot-instructions.md").is_file()
                    )

        self.assertIsNotNone(_FixedGenerator.last_requirements)
        transcript = _FixedGenerator.last_requirements.interview_conversation
        self.assertTrue(
            any(
                item.content == "How should I handle open choices?"
                for item in transcript
            )
        )
        self.assertTrue(
            any(
                item.content
                == "Use the confirmed requirements as the source of truth."
                for item in transcript
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_provider_api_key_is_encrypted_at_rest(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        secret = "api-key-that-must-not-be-stored-in-plain-text"
        profile = ProviderProfile.objects.create(
            user=self.user,
            provider="openai",
            model_name="test-model",
        )
        with patch.dict(os.environ, {"HARNESS_CREDENTIAL_ENCRYPTION_KEY": key}):
            profile.encrypt_api_key(secret)
            profile.save()
            profile.refresh_from_db()
            settings = profile.to_provider_settings()
            decrypted = profile.decrypt_api_key()

        self.assertNotIn(secret, profile.encrypted_api_key)
        self.assertEqual(decrypted, secret)
        self.assertNotIn(secret, repr(settings))

    def test_provider_settings_form_saves_account_specific_configuration(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        secret = "per-account-provider-secret"
        profile = ProviderProfile.objects.create(user=self.user)
        form = ProviderProfileForm(
            data={
                "provider": "openai",
                "model_name": "account-model",
                "endpoint": "",
                "api_version": "",
                "api_key": secret,
                "clear_api_key": "",
            },
            profile=profile,
        )

        with patch.dict(os.environ, {"HARNESS_CREDENTIAL_ENCRYPTION_KEY": key}):
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            profile.refresh_from_db()
            provider_settings = profile.to_provider_settings()

        self.assertEqual(provider_settings.model, "account-model")
        self.assertNotIn(secret, profile.encrypted_api_key)
        self.assertNotIn(secret, repr(provider_settings))

    def test_one_account_cannot_see_another_account_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = DatabaseSettings(
                url="sqlite:///unused",
                path=Path(directory) / "checkpoint.sqlite3",
            )
            with SessionManager(settings, owner_id=str(self.user.pk)) as own:
                own.start({"request": "Private account request."})
            other = self.user_model.objects.create_user(
                username="other-user",
                password="another-test-password",
            )
            with SessionManager(settings, owner_id=str(other.pk)) as foreign:
                self.assertIsNone(foreign.active_thread_id)

    def test_project_destinations_are_confined_to_account_workspace(self) -> None:
        other = self.user_model.objects.create_user(
            username="other-user",
            password="another-test-password",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"HARNESS_WORKSPACE_ROOT": str(Path(directory) / "workspaces")},
            ):
                own_root = user_workspace(self.user)
                other_root = user_workspace(other)
                project = own_root / "project"
                project.mkdir()

                self.assertNotEqual(own_root, other_root)
                self.assertEqual(stat.S_IMODE(own_root.stat().st_mode), 0o700)
                self.assertEqual(_resolve_destination(self.user, "project"), project)
                with self.assertRaises(HarnessError):
                    _resolve_destination(self.user, "../other")
