from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from harness_gen.logging_config import configure_logging
from harness_gen.logging_events import log_stage_event, web_logging_scope
from harness_gen.workflow import _failure


class StageLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness_gen")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_records_content_location_and_stable_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = configure_logging(
                log_directory=Path(temporary_directory) / "logs",
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
            )

            with web_logging_scope():
                log_stage_event(
                    "output_validation",
                    "finding",
                    code="missing_confirmed_detail",
                    location="AGENTS.md",
                    text="Maintain the public API.",
                )

            content = log_path.read_text(encoding="utf-8")
            self.assertIn('"stage": "output_validation"', content)
            self.assertIn('"code": "missing_confirmed_detail"', content)
            self.assertIn('"location": "AGENTS.md"', content)
            self.assertIn("Maintain the public API.", content)

    def test_content_events_are_disabled_outside_web_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = configure_logging(
                log_directory=Path(temporary_directory) / "logs",
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
            )

            log_stage_event("generation", "model_output", output="private text")

            self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_workflow_failure_records_its_code_without_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = configure_logging(
                log_directory=Path(temporary_directory) / "logs",
                run_started_at=datetime(2026, 10, 13, 11, 28, 41),
            )

            with web_logging_scope():
                _failure("validation_failed", "Private error details.")

            content = log_path.read_text(encoding="utf-8")
            self.assertIn('"stage": "workflow"', content)
            self.assertIn('"code": "validation_failed"', content)
            self.assertNotIn("Private error details.", content)


if __name__ == "__main__":
    unittest.main()
