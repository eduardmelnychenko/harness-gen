"""Django application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from django.core.management import execute_from_command_line


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harness_gen.web.settings")
    execute_from_command_line(sys.argv)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]
