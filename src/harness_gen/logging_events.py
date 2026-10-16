"""Stage-level web logging for content-bearing workflow events."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_LOGGER = logging.getLogger("harness_gen")
_WEB_LOGGING_ACTIVE: ContextVar[bool] = ContextVar(
    "harness_gen_web_logging_active",
    default=False,
)


@contextmanager
def web_logging_scope() -> Iterator[None]:
    token = _WEB_LOGGING_ACTIVE.set(True)
    try:
        yield
    finally:
        _WEB_LOGGING_ACTIVE.reset(token)


def log_stage_event(
    stage: str,
    event: str,
    **fields: object,
) -> None:
    if not _WEB_LOGGING_ACTIVE.get():
        return
    record = {"stage": stage, "event": event, **fields}
    _LOGGER.info(
        "workflow_stage %s",
        json.dumps(record, ensure_ascii=True, sort_keys=True, default=str),
    )
