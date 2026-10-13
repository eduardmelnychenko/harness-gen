from __future__ import annotations

import logging
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from harness_gen.logging_config import configure_logging


class LoggingConfigurationTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness_gen")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_creates_owner_only_timestamped_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "logs"
            log_path = configure_logging(
                log_directory=directory,
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
                level="INFO",
            )

            self.assertEqual(log_path.name, "harness-20261013-112841.log")
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
            self.assertEqual(logging.getLogger("harness_gen").level, logging.INFO)

    def test_simultaneous_runs_get_distinct_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            started_at = datetime(2026, 10, 13, 11, 28, 41)

            first = configure_logging(
                log_directory=directory, run_started_at=started_at
            )
            second = configure_logging(
                log_directory=directory, run_started_at=started_at
            )

            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_unsupported_level_uses_debug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            configure_logging(
                log_directory=Path(temporary_directory),
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
                level="unsupported",
            )

            self.assertEqual(logging.getLogger("harness_gen").level, logging.DEBUG)


if __name__ == "__main__":
    unittest.main()
