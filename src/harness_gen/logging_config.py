"""Per-run operational logging configuration."""

from __future__ import annotations

import logging
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import TextIO


_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


class _OwnedStreamHandler(logging.StreamHandler[TextIO]):
    def close(self) -> None:
        stream = self.stream
        try:
            self.flush()
        finally:
            self.stream = None
            if stream is not None:
                stream.close()
            super().close()


def _configured_level(value: str | None) -> int:
    if value is None:
        return logging.INFO
    return _LOG_LEVELS.get(value.strip().upper(), logging.DEBUG)


def _create_unique_log_file(directory: Path, timestamp: str) -> tuple[Path, TextIO]:
    suffix = 0
    while True:
        suffix_text = "" if suffix == 0 else f"-{suffix}"
        path = directory / f"harness-{timestamp}{suffix_text}.log"
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            suffix += 1
            continue
        return path, os.fdopen(descriptor, "a", encoding="utf-8")


def configure_logging(
    *,
    log_directory: Path | None = None,
    run_started_at: datetime | None = None,
    level: str | None = None,
) -> Path:
    """Configure a private, timestamped log file and return its path.

    The web application can use this handler for stage-level records that
    include user or provider content. Callers must never pass provider
    credentials.
    """
    directory = log_directory or Path.cwd() / "logs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_stat = directory.lstat()
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(
        directory_stat.st_mode
    ):
        raise OSError("The log directory must be a real directory.")
    os.chmod(directory, 0o700)
    timestamp = (run_started_at or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path, stream = _create_unique_log_file(directory, timestamp)

    logger = logging.getLogger("harness_gen")
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = _OwnedStreamHandler(stream)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    configured_level = (
        level if level is not None else os.getenv("HARNESS_CREATOR_LOG_LEVEL")
    )
    logger.setLevel(_configured_level(configured_level))
    logger.propagate = False
    return path
