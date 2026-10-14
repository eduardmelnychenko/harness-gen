from __future__ import annotations

from typing import Any

from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment


def environment(**options: Any) -> Environment:
    env = Environment(**options)
    env.globals.update(url=reverse, static=static)
    return env
