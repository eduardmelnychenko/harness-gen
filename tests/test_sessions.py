from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from pathlib import Path

from harness_gen.config import ProviderSettings
from harness_gen.errors import SessionPersistenceError
from harness_gen.requirements import RequirementValue, RequirementsDraft
from harness_gen.sessions import (
    DEFAULT_DATABASE_URL,
    DatabaseSettings,
    SessionManager,
)
from harness_gen.targets import AgentTarget
from harness_gen.tasks import TaskType


class SessionPersistenceTests(unittest.TestCase):
    def test_database_url_defaults_and_unsupported_urls_fall_back(self) -> None:
        for environ in (
            {},
            {"HARNESS_DB_URL": "postgresql://localhost/sessions"},
            {"HARNESS_DB_URL": "sqlite:///data/sessions.db?mode=ro"},
            {"HARNESS_DB_URL": "sqlite:///:memory:"},
        ):
            with self.subTest(environ=environ):
                settings = DatabaseSettings.from_environment(environ)
                self.assertEqual(settings.url, DEFAULT_DATABASE_URL)
                self.assertEqual(
                    settings.path, Path.cwd() / "data" / "harness.db"
                )

    def test_database_url_supports_relative_and_absolute_sqlite_paths(self) -> None:
        relative = DatabaseSettings.from_environment(
            {"HARNESS_DB_URL": "sqlite:///custom/session.db"}
        )
        self.assertEqual(relative.path, Path.cwd() / "custom" / "session.db")

        with tempfile.TemporaryDirectory() as temporary_directory:
            absolute_path = Path(temporary_directory) / "sessions.sqlite"
            absolute = DatabaseSettings.from_environment(
                {"HARNESS_DB_URL": f"sqlite:///{absolute_path}"}
            )
            self.assertEqual(absolute.path, absolute_path)

    def test_database_and_new_parent_directory_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "private" / "session.db"
            with SessionManager(_settings(database_path)):
                self.assertEqual(
                    stat.S_IMODE(database_path.stat().st_mode), 0o600
                )
                self.assertEqual(
                    stat.S_IMODE(database_path.parent.stat().st_mode), 0o700
                )

    def test_existing_database_permissions_are_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "session.db"
            database_path.touch(mode=0o644)
            database_path.chmod(0o644)

            with SessionManager(_settings(database_path)):
                self.assertEqual(
                    stat.S_IMODE(database_path.stat().st_mode), 0o600
                )

    def test_rejects_symlinked_database_and_accepts_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_database = root / "real.db"
            real_database.touch()
            link_database = root / "linked.db"
            link_database.symlink_to(real_database)
            with self.assertRaises(SessionPersistenceError):
                SessionManager(_settings(link_database))

            real_directory = root / "real-directory"
            real_directory.mkdir()
            linked_directory = root / "linked-directory"
            linked_directory.symlink_to(real_directory, target_is_directory=True)
            database_path = linked_directory / "session.db"
            with SessionManager(_settings(database_path)):
                self.assertEqual(
                    stat.S_IMODE(database_path.stat().st_mode), 0o600
                )

    def test_start_resume_and_reopen_restore_checkpointed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "session.db"
            state = {
                "request": "Create harness instructions.",
                "requirements": {"purpose": "Maintain the API."},
                "task_types": ["software_development"],
                "target_agent": "github_copilot",
                "generated_files": [
                    {"path": "AGENTS.md", "content": "Generated context."}
                ],
            }
            with SessionManager(_settings(database_path)) as manager:
                reference = manager.start(state)
                self.assertEqual(manager.active_thread_id, reference.thread_id)

                paused = manager.request_input(
                    {
                        "kind": "requirements_review",
                        "summary": "Review the confirmed requirements.",
                    }
                )
                self.assertEqual(
                    paused["pending_prompt"]["kind"], "requirements_review"
                )
                self.assertIsNone(paused["last_response"])

            with SessionManager(_settings(database_path)) as resumed:
                self.assertEqual(resumed.active_thread_id, reference.thread_id)
                self.assertEqual(
                    resumed.pause()["generated_files"], state["generated_files"]
                )
                result = resumed.resume({"approved": True})
                self.assertIsNone(result["pending_prompt"])
                self.assertEqual(result["last_response"], {"approved": True})

    def test_checkpoint_read_failure_is_reported_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _settings(Path(temporary_directory) / "session.db")
            ) as manager:
                reference = manager.start({"request": "Keep this checkpoint."})

                with patch.object(
                    manager._graph,
                    "get_state",
                    side_effect=sqlite3.OperationalError("simulated read failure"),
                ):
                    with self.assertRaisesRegex(
                        SessionPersistenceError, "checkpoint could not be read"
                    ):
                        manager.state()

                self.assertEqual(manager.active_thread_id, reference.thread_id)
                self.assertEqual(manager.state()["request"], "Keep this checkpoint.")

    def test_checkpoint_write_failure_keeps_prior_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _settings(Path(temporary_directory) / "session.db")
            ) as manager:
                reference = manager.start({"request": "Keep this checkpoint."})

                with patch.object(
                    manager._graph,
                    "invoke",
                    side_effect=sqlite3.OperationalError("simulated write failure"),
                ):
                    with self.assertRaisesRegex(
                        SessionPersistenceError, "checkpoint could not be updated"
                    ):
                        manager.update({"request": "Do not replace saved state."})

                self.assertEqual(manager.active_thread_id, reference.thread_id)
                self.assertEqual(manager.state()["request"], "Keep this checkpoint.")

    def test_existing_custom_session_rows_are_not_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "session.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "CREATE TABLE sessions (id TEXT PRIMARY KEY, request TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO sessions (id, request) VALUES (?, ?)",
                    ("old-session", "Legacy request."),
                )
                connection.commit()

            with SessionManager(_settings(database_path)) as manager:
                self.assertIsNone(manager.active_thread_id)
                self.assertEqual(
                    manager._connection.execute(
                        "SELECT request FROM sessions WHERE id = ?",
                        ("old-session",),
                    ).fetchone(),
                    ("Legacy request.",),
                )
                self.assertEqual(
                    manager._connection.execute(
                        "SELECT COUNT(*) FROM active_session"
                    ).fetchone(),
                    (0,),
                )

    def test_provider_credentials_are_not_saved_in_graph_checkpoints(self) -> None:
        secret_marker = "credential-marker-not-for-session-state"
        provider = ProviderSettings.from_environment(
            {
                "HARNESS_LLM_PROVIDER": "openai",
                "HARNESS_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": secret_marker,
            }
        )
        requirements = RequirementsDraft(
            request="Create API instructions.",
            purpose=RequirementValue("Maintain the API.", confirmed=True),
            task_types=(TaskType.SOFTWARE_DEVELOPMENT,),
            target=AgentTarget.GITHUB_COPILOT,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "session.db"
            with SessionManager(
                _settings(database_path),
                provider_settings=provider,
            ) as manager:
                reference = manager.start_workflow(requirements)
                manager.resume({"approved": True})
                state = manager.state()
                checkpoint_rows = manager._connection.execute(
                    "SELECT * FROM checkpoints WHERE thread_id = ?",
                    (reference.thread_id,),
                ).fetchall()

                self.assertEqual(state["phase"], "provider_approval_wait")
                self.assertNotIn(secret_marker, repr(state))
                self.assertNotIn(secret_marker, repr(checkpoint_rows))
                self.assertNotIn("api_key", state.get("provider_pending", {}))

    def test_only_one_active_session_can_be_started(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _settings(Path(temporary_directory) / "session.db")
            ) as manager:
                manager.start({"request": "First request."})
                with self.assertRaises(SessionPersistenceError):
                    manager.start({"request": "Second request."})

    def test_account_sessions_are_isolated_and_lifecycles_do_not_cross(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = _settings(Path(temporary_directory) / "shared.db")
            with SessionManager(settings, owner_id="account-a") as first:
                first_ref = first.start({"request": "Account A request."})
            with SessionManager(settings, owner_id="account-b") as second:
                second_ref = second.start({"request": "Account B request."})
                self.assertNotEqual(first_ref.thread_id, second_ref.thread_id)
                self.assertEqual(second.state()["request"], "Account B request.")

            with SessionManager(settings, owner_id="account-a") as first:
                self.assertEqual(first.state()["request"], "Account A request.")
                first.restart()
                self.assertNotEqual(first.active_thread_id, first_ref.thread_id)
                first.delete()

            with SessionManager(settings, owner_id="account-b") as second:
                self.assertEqual(second.active_thread_id, second_ref.thread_id)
                self.assertEqual(second.state()["request"], "Account B request.")

    def test_account_session_does_not_adopt_legacy_unowned_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = _settings(Path(temporary_directory) / "shared.db")
            with SessionManager(settings) as legacy:
                legacy.start({"request": "Unowned legacy checkpoint."})

            with SessionManager(settings, owner_id="account-a") as account:
                self.assertIsNone(account.active_thread_id)
                account.start({"request": "Account-specific checkpoint."})
                self.assertEqual(
                    account.state()["request"],
                    "Account-specific checkpoint.",
                )

    def test_restart_keeps_requirements_but_drops_generated_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_file = Path(temporary_directory) / "project" / "AGENTS.md"
            project_file.parent.mkdir()
            project_file.write_text("Project-owned instructions.")
            database_path = Path(temporary_directory) / "session.db"
            with SessionManager(_settings(database_path)) as manager:
                original = manager.start(
                    {
                        "request": "Create instructions.",
                        "requirements": {"purpose": "Maintain the API."},
                        "task_types": ["software_development"],
                        "target_agent": "github_copilot",
                        "task_type_instructions": True,
                        "path_patterns": ["src/**/*.py"],
                        "interview_data": {
                            "answers": {"purpose": "Maintain the API."},
                            "ai_assistance_enabled": True,
                            "ai_provider_approval": {
                                "provider": "openai",
                                "endpoint_fingerprint": "approved-endpoint",
                            },
                            "supplemental_answers": [
                                {
                                    "question": "Which API versions matter?",
                                    "answer": "Version 1.",
                                }
                            ],
                        },
                        "requirements_reviewed": True,
                        "provider_approval": {
                            "provider": "ollama",
                            "endpoint_fingerprint": "local-endpoint",
                            "approved": True,
                        },
                        "generated_files": [
                            {"path": "AGENTS.md", "content": "Old output."}
                        ],
                        "validation_status": "pass",
                        "artifact_review": {"approved": True},
                        "write_approved": True,
                        "write_result": {"written": ["AGENTS.md"]},
                        "outcome": "complete",
                    }
                )

                restarted = manager.restart()
                state = manager.state()

                self.assertNotEqual(restarted.thread_id, original.thread_id)
                self.assertEqual(state["request"], "Create instructions.")
                self.assertEqual(
                    state["requirements"], {"purpose": "Maintain the API."}
                )
                self.assertEqual(state["task_types"], ["software_development"])
                self.assertEqual(state["path_patterns"], ["src/**/*.py"])
                self.assertFalse(state["requirements_reviewed"])
                self.assertEqual(state["phase"], "checkpoint")
                self.assertNotIn(
                    "ai_assistance_enabled",
                    state["interview_data"],
                )
                self.assertNotIn("ai_provider_approval", state["interview_data"])
                self.assertEqual(
                    state["interview_data"]["supplemental_answers"][0]["answer"],
                    "Version 1.",
                )
                for cleared_key in (
                    "provider_approval",
                    "generated_files",
                    "validation_status",
                    "artifact_review",
                    "write_approved",
                    "write_result",
                    "outcome",
                ):
                    self.assertNotIn(cleared_key, state)
                self.assertEqual(
                    project_file.read_text(), "Project-owned instructions."
                )
                checkpoint_count = manager._connection.execute(
                    "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                    (original.thread_id,),
                ).fetchone()[0]
                self.assertEqual(checkpoint_count, 0)

    def test_delete_removes_active_checkpoint_and_allows_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_file = Path(temporary_directory) / "AGENTS.md"
            project_file.write_text("User-owned instructions.")
            with SessionManager(
                _settings(Path(temporary_directory) / "session.db")
            ) as manager:
                reference = manager.start(
                    {
                        "request": "Delete this session.",
                        "pending_prompt": {
                            "kind": "requirements_review",
                            "message": "Review the requirements.",
                        },
                    }
                )
                self.assertTrue(manager.delete())
                self.assertIsNone(manager.active_thread_id)
                for table in ("checkpoints", "writes"):
                    with self.subTest(table=table):
                        self.assertEqual(
                            manager._connection.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?",
                                (reference.thread_id,),
                            ).fetchone()[0],
                            0,
                        )
                self.assertFalse(manager.delete())
                manager.start({"request": "Start a new session."})
                self.assertEqual(
                    project_file.read_text(), "User-owned instructions."
                )

    def test_state_validation_rejects_unknown_or_non_json_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with SessionManager(
                _settings(Path(temporary_directory) / "session.db")
            ) as manager:
                with self.assertRaises(SessionPersistenceError):
                    manager.start(
                        {"request": "Do not persist a secret field.", "api_key": "x"}
                    )
                with self.assertRaises(SessionPersistenceError):
                    manager.start(
                        {
                            "request": "Do not persist provider credentials.",
                            "provider_approval": {
                                "provider": "openai",
                                "endpoint_fingerprint": "fingerprint",
                                "approved": True,
                                "api_key": "secret",
                            },
                        }
                    )
                with self.assertRaises(SessionPersistenceError):
                    manager.start(
                        {
                            "request": "Do not persist a non-JSON object.",
                            "task_types": [object()],
                        }
                    )
                with self.assertRaises(SessionPersistenceError):
                    manager.start({"request": " "})
                self.assertIsNone(manager.active_thread_id)


def _settings(path: Path) -> DatabaseSettings:
    return DatabaseSettings(url=f"sqlite:///{path}", path=path)


if __name__ == "__main__":
    unittest.main()
