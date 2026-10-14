from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[3]
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _prepare_auth_database(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.lstat()
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise ImproperlyConfigured("The application database directory is not safe.")
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
            raise ImproperlyConfigured("The application database path is not safe.")
    os.chmod(path, 0o600)


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before starting the web app.")

DEBUG = os.environ.get("HARNESS_DEBUG", "").strip().lower() in {"1", "true", "yes"}
_log_level = os.environ.get("HARNESS_CREATOR_LOG_LEVEL", "INFO").strip().upper()
if _log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
    _log_level = "DEBUG"
ALLOWED_HOSTS = [
    item.strip()
    for item in os.environ.get(
        "HARNESS_ALLOWED_HOSTS", "localhost,127.0.0.1"
    ).split(",")
    if item.strip()
]
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.environ.get("HARNESS_CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "harness_gen.webapp.apps.WebAppConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "harness_gen.web.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "environment": "harness_gen.web.jinja2.environment",
        },
    },
]

WSGI_APPLICATION = "harness_gen.web.wsgi.application"
ASGI_APPLICATION = "harness_gen.web.asgi.application"
_django_database = Path(
    os.environ.get("HARNESS_DJANGO_DB", str(BASE_DIR / "data" / "web.sqlite3"))
).expanduser()
if str(_django_database) != ":memory:":
    _prepare_auth_database(_django_database)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_django_database),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "harness_gen": {
            "handlers": ["console"],
            "level": getattr(logging, _log_level),
            "propagate": False,
        },
    },
}
