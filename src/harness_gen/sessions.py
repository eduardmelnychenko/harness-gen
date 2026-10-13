"""Owner-only SQLite persistence and lifecycle for one active graph thread."""

from __future__ import annotations

import math
import os
import sqlite3
import stat
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, cast
from urllib.parse import unquote

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot

from harness_gen.config import ProviderSettings
from harness_gen.errors import RequirementsError, SessionPersistenceError
from harness_gen.generation import HarnessGenerator
from harness_gen.requirements import (
    RequirementsDraft,
    requirements_from_state,
    requirements_to_state,
)
from harness_gen.workflow import (
    GenerationRunner,
    HarnessWorkflowState,
    build_workflow_graph,
)

DEFAULT_DATABASE_URL = "sqlite:///data/harness.db"
_DATABASE_PREFIX = "sqlite:///"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_STATE_FIELDS = frozenset(HarnessWorkflowState.__annotations__)
_RESTART_FIELDS = (
    "request",
    "interview_data",
    "requirements",
    "task_types",
    "target_agent",
    "task_type_instructions",
    "path_scoped_instructions",
    "path_patterns",
    "unresolved_choices",
    "workflow_enabled",
)


@dataclass(frozen=True, slots=True)
class SessionReference:
    thread_id: str


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    url: str = field(repr=False)
    path: Path = field(repr=False)

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> DatabaseSettings:
        if environ is None:
            load_dotenv(override=False)
            values = os.environ
        else:
            values = environ

        configured_url = values.get("HARNESS_DB_URL", "")
        path, effective_url = _database_location(configured_url)
        return cls(url=effective_url, path=path)


class SessionManager:
    """Manage graph checkpoints and the single active session pointer."""

    def __init__(
        self,
        settings: DatabaseSettings | None = None,
        *,
        provider_settings: ProviderSettings | None = None,
        generator: GenerationRunner | None = None,
    ) -> None:
        self.settings = settings or DatabaseSettings.from_environment()
        self.provider_settings = provider_settings
        self.generator = generator or HarnessGenerator()
        self._lock = threading.RLock()
        self._closed = False
        self._connection: sqlite3.Connection | None = None
        try:
            _prepare_database_path(self.settings.path)
            self._connection = sqlite3.connect(
                self.settings.path,
                check_same_thread=False,
            )
            os.chmod(self.settings.path, 0o600)
            self._checkpointer = SqliteSaver(self._connection)
            self._checkpointer.setup()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS active_session (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._connection.commit()
            self._graph = build_workflow_graph(
                self._checkpointer,
                provider_settings,
                self.generator,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            raise SessionPersistenceError(
                "The session database could not be initialized safely."
            ) from error

    @property
    def database_path(self) -> Path:
        return self.settings.path

    @property
    def active_thread_id(self) -> str | None:
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT thread_id FROM active_session WHERE singleton = 1"
                ).fetchone()
            except sqlite3.Error as error:
                raise SessionPersistenceError(
                    "The active session index could not be read. "
                    "Check the session database and try again."
                ) from error
            return None if row is None else str(row[0])

    def start(self, initial_state: HarnessWorkflowState) -> SessionReference:
        state = _validated_state(initial_state, require_request=True)
        state["phase"] = "checkpoint"
        state["workflow_enabled"] = False
        return self._start_state(state)

    def start_workflow(
        self, requirements: RequirementsDraft
    ) -> SessionReference:
        editable_requirements = replace(
            requirements,
            requirements_reviewed=False,
        )
        initial_state: HarnessWorkflowState = {
            "request": editable_requirements.request,
            "requirements": requirements_to_state(editable_requirements),
            "interview_data": {},
            "requirements_reviewed": False,
            "requirements_review_fingerprint": None,
            "provider_pending": None,
            "provider_approval": None,
            "phase": "requirements_review",
            "workflow_enabled": True,
            "planned_artifacts": [],
            "generated_files": [],
            "generation_attempts": 0,
            "validation_status": None,
            "validation_findings": [],
            "destination": "",
            "artifact_review": None,
            "review_summary": [],
            "write_approved": False,
            "write_approval_fingerprint": None,
            "write_result": None,
            "pending_prompt": None,
            "last_response": None,
            "outcome": "",
            "error_message": "",
        }
        state = _validated_state(initial_state, require_request=True)
        with self._lock:
            self._ensure_open()
            thread_id = self.active_thread_id
            if thread_id is None:
                return self._start_state(state)
            config = _thread_config(thread_id)
            snapshot = self._get_snapshot(config)
            if (
                snapshot.values.get("workflow_enabled") is True
                or _has_pending_interrupt(snapshot)
            ):
                raise SessionPersistenceError(
                    "The active session must finish its current decision before "
                    "the harness workflow can start."
                )
            self._invoke_graph(state, config)
            return SessionReference(thread_id)

    def _start_state(self, state: HarnessWorkflowState) -> SessionReference:
        with self._lock:
            self._ensure_open()
            if self.active_thread_id is not None:
                raise SessionPersistenceError(
                    "An active session already exists. Resume or restart it first."
                )
            thread_id = str(uuid.uuid4())
            config = _thread_config(thread_id)
            self._invoke_graph(state, config)
            try:
                self._connection.execute(
                    """
                    INSERT INTO active_session (singleton, thread_id)
                    VALUES (1, ?)
                    """,
                    (thread_id,),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._checkpointer.delete_thread(thread_id)
                raise SessionPersistenceError(
                    "The new session could not be made active."
                ) from error
            return SessionReference(thread_id)

    def state(self) -> HarnessWorkflowState:
        with self._lock:
            config = self._active_config()
            snapshot = self._get_snapshot(config)
            if not snapshot.values:
                raise SessionPersistenceError(
                    "The active session has no recoverable checkpoint."
                )
            state = _validated_state(snapshot.values)
            for task in snapshot.tasks:
                for pending in task.interrupts:
                    prompt = pending.value
                    if isinstance(prompt, dict) and all(
                        isinstance(key, str) for key in prompt
                    ):
                        state["pending_prompt"] = prompt
                        return state
            return state

    def pause(self) -> HarnessWorkflowState:
        """Return the checkpointed state without changing its pending interrupt."""
        return self.state()

    def request_input(
        self, prompt: dict[str, object]
    ) -> HarnessWorkflowState:
        _validate_json(prompt)
        with self._lock:
            config = self._active_config()
            snapshot = self._get_snapshot(config)
            if snapshot.values.get("workflow_enabled") is True:
                raise SessionPersistenceError(
                    "Use the current workflow decision instead of adding "
                    "a separate prompt."
                )
            if _has_pending_interrupt(snapshot):
                raise SessionPersistenceError(
                    "Answer the pending decision before requesting another one."
                )
            self._invoke_graph(
                {
                    "pending_prompt": prompt,
                    "last_response": None,
                    "phase": "user_input",
                },
                config,
            )
            return self.state()

    def resume(self, response: object) -> HarnessWorkflowState:
        _validate_json(response)
        with self._lock:
            config = self._active_config()
            snapshot = self._get_snapshot(config)
            if not _has_pending_interrupt(snapshot):
                raise SessionPersistenceError(
                    "The active session is not waiting for a user decision."
                )
            _validate_resume_response(snapshot.values, response)
            self._invoke_graph(Command(resume=response), config)
            return self.state()

    def update(self, patch: HarnessWorkflowState) -> HarnessWorkflowState:
        state_patch = _validated_state(patch)
        with self._lock:
            config = self._active_config()
            snapshot = self._get_snapshot(config)
            if snapshot.values.get("workflow_enabled") is True:
                raise SessionPersistenceError(
                    "Use revise_requirements to change a workflow session."
                )
            if _has_pending_interrupt(snapshot):
                raise SessionPersistenceError(
                    "Resume the pending decision before updating the session."
                )
            state_patch["phase"] = "checkpoint"
            state_patch["workflow_enabled"] = False
            self._invoke_graph(state_patch, config)
            return self.state()

    def revise_requirements(
        self, requirements: RequirementsDraft
    ) -> HarnessWorkflowState:
        with self._lock:
            config = self._active_config()
            snapshot = self._get_snapshot(config)
            if snapshot.values.get("workflow_enabled") is not True:
                raise SessionPersistenceError(
                    "The active session is not a harness workflow."
                )
            if _has_pending_interrupt(snapshot):
                raise SessionPersistenceError(
                    "Resume the pending decision before changing requirements."
                )
            editable = replace(requirements, requirements_reviewed=False)
            update: HarnessWorkflowState = {
                "request": editable.request,
                "requirements": requirements_to_state(editable),
                "requirements_reviewed": False,
                "requirements_review_fingerprint": None,
                "provider_pending": None,
                "provider_approval": None,
                "planned_artifacts": [],
                "generated_files": [],
                "generation_attempts": 0,
                "validation_status": None,
                "validation_findings": [],
                "destination": "",
                "artifact_review": None,
                "review_summary": [],
                "write_approved": False,
                "write_approval_fingerprint": None,
                "write_result": None,
                "outcome": "",
                "error_message": "",
                "phase": "requirements_review",
            }
            self._invoke_graph(_validated_state(update), config)
            return self.state()

    def restart(self) -> SessionReference:
        with self._lock:
            old_thread_id = self.active_thread_id
            if old_thread_id is None:
                raise SessionPersistenceError("There is no active session to restart.")
            snapshot = self._get_snapshot(_thread_config(old_thread_id))
            if not snapshot.values:
                raise SessionPersistenceError(
                    "The active session has no requirements to restart from."
                )
            previous_state = _validated_state(snapshot.values)
            seed = {
                key: previous_state[key]
                for key in _RESTART_FIELDS
                if key in previous_state
            }
            workflow_enabled = seed.get("workflow_enabled") is True
            if workflow_enabled and isinstance(seed.get("requirements"), Mapping):
                try:
                    editable = replace(
                        requirements_from_state(seed["requirements"]),
                        requirements_reviewed=False,
                    )
                except (TypeError, ValueError, RequirementsError):
                    raise SessionPersistenceError(
                        "The saved requirements cannot be restarted safely."
                    ) from None
                seed["requirements"] = requirements_to_state(editable)
                seed["request"] = editable.request
                seed["phase"] = "requirements_review"
            else:
                seed["workflow_enabled"] = False
                seed["phase"] = "checkpoint"
            seed["requirements_reviewed"] = False
            seed = _validated_state(seed, require_request=True)

            new_thread_id = str(uuid.uuid4())
            self._invoke_graph(seed, _thread_config(new_thread_id))
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE active_session
                    SET thread_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE singleton = 1 AND thread_id = ?
                    """,
                    (new_thread_id, old_thread_id),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError(
                        "The active session changed during restart."
                    )
                self._connection.commit()
            except sqlite3.Error as error:
                self._checkpointer.delete_thread(new_thread_id)
                raise SessionPersistenceError(
                    "The restarted session could not be made active."
                ) from error
            try:
                self._checkpointer.delete_thread(old_thread_id)
            except sqlite3.Error as error:
                raise SessionPersistenceError(
                    "The new session is active, but the previous checkpoint "
                    "could not be removed."
                ) from error
            return SessionReference(new_thread_id)

    def delete(self) -> bool:
        with self._lock:
            thread_id = self.active_thread_id
            if thread_id is None:
                return False
            try:
                self._checkpointer.delete_thread(thread_id)
                self._connection.execute(
                    "DELETE FROM active_session WHERE singleton = 1 AND thread_id = ?",
                    (thread_id,),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                raise SessionPersistenceError(
                    "The active session could not be deleted completely."
                ) from error
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> SessionManager:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _active_config(self) -> dict[str, dict[str, str]]:
        thread_id = self.active_thread_id
        if thread_id is None:
            raise SessionPersistenceError("There is no active session.")
        return _thread_config(thread_id)

    def _get_snapshot(
        self,
        config: dict[str, dict[str, str]],
    ) -> StateSnapshot:
        try:
            return self._graph.get_state(config)
        except sqlite3.Error as error:
            raise SessionPersistenceError(
                "The active session checkpoint could not be read. "
                "Check the session database and try again."
            ) from error

    def _invoke_graph(
        self,
        state: HarnessWorkflowState | Command,
        config: dict[str, dict[str, str]],
    ) -> object:
        try:
            return self._graph.invoke(state, config)
        except sqlite3.Error as error:
            raise SessionPersistenceError(
                "The session checkpoint could not be updated. Check the database. "
                "If this occurred during file application, inspect the project "
                "before you resume."
            ) from error

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionPersistenceError("The session manager is closed.")


def _database_location(configured_url: str) -> tuple[Path, str]:
    selected_url = configured_url.strip()
    path_text = ""
    if (
        selected_url.startswith(_DATABASE_PREFIX)
        and "?" not in selected_url
        and "#" not in selected_url
        and "\x00" not in selected_url
    ):
        path_text = unquote(selected_url[len(_DATABASE_PREFIX) :])
    if not path_text or path_text == ":memory:":
        selected_url = DEFAULT_DATABASE_URL
        path_text = DEFAULT_DATABASE_URL[len(_DATABASE_PREFIX) :]

    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute(), selected_url


def _prepare_database_path(path: Path) -> None:
    for directory in reversed(path.parents):
        if directory == Path(directory.anchor):
            continue
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        directory_stat = directory.stat()
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError("The database parent is not a safe directory.")

    try:
        database_stat = path.lstat()
    except FileNotFoundError:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        os.close(descriptor)
    else:
        if stat.S_ISLNK(database_stat.st_mode) or not stat.S_ISREG(
            database_stat.st_mode
        ):
            raise OSError("The database path is not a regular file.")
    os.chmod(path, 0o600)


def _validated_state(
    state: Mapping[str, object],
    *,
    require_request: bool = False,
) -> HarnessWorkflowState:
    unknown_fields = set(state) - _STATE_FIELDS
    if unknown_fields:
        raise SessionPersistenceError(
            "Session state contains unsupported fields and was not saved."
        )
    if require_request and (
        not isinstance(state.get("request"), str)
        or not state["request"].strip()
    ):
        raise SessionPersistenceError("A new session requires a non-empty request.")
    provider_approval = state.get("provider_approval")
    if provider_approval is not None:
        _validate_provider_approval(provider_approval)
    provider_pending = state.get("provider_pending")
    if provider_pending is not None:
        _validate_provider_pending(provider_pending)
    _validate_json(dict(state))
    return cast(HarnessWorkflowState, dict(state))


def _validate_provider_approval(value: object) -> None:
    expected_fields = {"provider", "endpoint_fingerprint", "approved"}
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or not isinstance(value.get("provider"), str)
        or not isinstance(value.get("endpoint_fingerprint"), str)
        or not isinstance(value.get("approved"), bool)
    ):
        raise SessionPersistenceError(
            "Provider approval state must not contain provider credentials."
        )


def _validate_provider_pending(value: object) -> None:
    expected_fields = {
        "provider",
        "endpoint_fingerprint",
        "disclosure",
        "endpoint_is_local",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or not isinstance(value.get("provider"), str)
        or not isinstance(value.get("endpoint_fingerprint"), str)
        or not isinstance(value.get("disclosure"), str)
        or not isinstance(value.get("endpoint_is_local"), bool)
    ):
        raise SessionPersistenceError(
            "Provider disclosure state is invalid."
        )


def _validate_resume_response(
    state: Mapping[str, object],
    response: object,
) -> None:
    phase = state.get("phase")
    if phase == "user_input":
        _validate_json(response)
        return
    if phase == "requirements_review":
        if not isinstance(response, Mapping) or set(response) - {
            "approved",
            "requirements",
        }:
            raise SessionPersistenceError(
                "Return a requirements decision or a corrected requirements draft."
            )
        if "approved" in response and not isinstance(
            response["approved"], bool
        ):
            raise SessionPersistenceError(
                "The requirements decision must be true or false."
            )
        if "requirements" in response and not isinstance(
            response["requirements"], Mapping
        ):
            raise SessionPersistenceError(
                "Corrected requirements must use the structured requirements format."
            )
        if "requirements" in response:
            try:
                requirements_from_state(response["requirements"])
            except RequirementsError:
                raise SessionPersistenceError(
                    "Corrected requirements contain unsupported fields."
                ) from None
    elif phase in {"provider_approval_wait", "write_approval"}:
        if (
            not isinstance(response, Mapping)
            or set(response) != {"approved"}
            or not isinstance(response.get("approved"), bool)
        ):
            raise SessionPersistenceError(
                "Confirm approval with a true or false decision."
            )
    elif phase == "destination_selection":
        if (
            not isinstance(response, Mapping)
            or set(response) != {"destination"}
            or not isinstance(response.get("destination"), str)
            or not response["destination"].strip()
        ):
            raise SessionPersistenceError(
                "Select a non-empty project directory."
            )
    else:
        raise SessionPersistenceError(
            "The active workflow is not waiting for this decision."
        )
    _validate_json(response)


def _validate_json(value: object) -> None:
    _validate_json_value(value, set())


def _validate_json_value(value: object, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
    elif isinstance(value, (list, dict)):
        identity = id(value)
        if identity in ancestors:
            raise SessionPersistenceError(
                "Session state must not contain circular references."
            )
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, ancestors)
                return
            if not all(isinstance(key, str) for key in value):
                raise SessionPersistenceError(
                    "Session state object keys must be text."
                )
            for item in value.values():
                _validate_json_value(item, ancestors)
            return
        finally:
            ancestors.remove(identity)
    raise SessionPersistenceError(
        "Session state must contain JSON-compatible values only."
    ) from None


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _has_pending_interrupt(snapshot: StateSnapshot) -> bool:
    return any(task.interrupts for task in snapshot.tasks)
