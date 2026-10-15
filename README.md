# Harness Generator

Harness Generator is a Django web application that turns a description of an AI
agent's goals and constraints into reviewable instruction files. It gathers
requirements, generates and validates the files, and asks before writing them to
a project. It creates harness instructions, not application code, project
scaffolding, runtime settings, or deployment configuration.

The browser interface uses Django authentication, Jinja2 templates, and
Bootstrap 5.3. Accounts are created by an administrator. Each account has one
resumable session, provider configuration, encrypted provider credentials, and
an isolated workspace.

## What you can create

- Software development, machine-learning, data-science, and custom-task harness
  instructions.
- Instructions for Claude Code or GitHub Copilot, with optional task-specific
  and path-scoped files.
- Shared `AGENTS.md` context alongside target-specific instruction files.

The target agent determines file formats. The model provider is configured
separately for each account. Optional interview assistance is disabled until
you opt in; the application discloses provider use and asks for approval before
it sends interview or generation content to an external provider.

## Requirements and setup

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)

Install the locked dependencies:

```sh
uv sync --locked
```

Set these values in the process environment or a local `.env` file. The project
ignores `.env`; do not commit it.

| Setting | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django session and CSRF signing key. Keep it stable and secret. |
| `HARNESS_CREDENTIAL_ENCRYPTION_KEY` | Fernet key for per-account provider credentials. Keep a secure backup; the application cannot decrypt saved keys if this value is lost or changed. |
| `HARNESS_DEBUG` | Set to `true` only for local development over HTTP. Leave unset in deployment. |
| `HARNESS_CREATOR_LOG_LEVEL` | Django application log level. Defaults to `INFO`; unsupported values use `DEBUG`. |
| `HARNESS_DJANGO_DB` | Django authentication and provider-profile database. Defaults to `data/web.sqlite3`. |
| `HARNESS_DB_URL` | LangGraph session database. Defaults to `sqlite:///data/harness.db`. |
| `HARNESS_WORKSPACE_ROOT` | Root for user-owned project workspaces. Defaults to `workspaces/`; each account is restricted to its own subdirectory. |
| `HARNESS_ALLOWED_HOSTS` | Comma-separated Django host names. Defaults to `localhost,127.0.0.1`. |
| `HARNESS_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins when the app is served behind a proxy. |

Generate keys with:

```sh
uv run python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Create a local `.env` from the example and replace the required placeholders:

```sh
cp .env.example .env
```

Initialize the databases, create the administrator account, and run the
development server:

```sh
mkdir -p data
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/`. Use the Django admin at `/admin/` to create
accounts and set their passwords. Users configure their own model provider and
credential after signing in. The `harness-gen-web` command can run Django
management commands, for example `uv run harness-gen-web check`.

For hosted use, configure HTTPS, a stable Django secret key, secure database
backups, trusted hosts/origins, and an access-controlled workspace root.
`HARNESS_DEBUG` must be unset. SQLite is the default persistence option.

## Provider and data handling

The application supports Ollama, OpenAI, Azure OpenAI, Meta's hosted API, Google
Gemini, and Anthropic Claude. Provider credentials are encrypted in the Django
database using `HARNESS_CREDENTIAL_ENCRYPTION_KEY`. They are not included in
LangGraph checkpoints, generated files, or operational errors.

Requirements, interview answers, and generated files are stored in the
checkpoint database. The app does not scan or redact arbitrary user text.
External providers receive session content only after the user approves the
disclosure. The app does not verify provider retention practices; review the
provider's terms. Deleting a session removes checkpoint records but does not
guarantee secure erasure from storage media.

Generated files are written on the application host. The app creates a
workspace under `HARNESS_WORKSPACE_ROOT/<account-id>` and only allows project
destinations directly inside that account's workspace. Place existing project
directories there before selecting a destination. The complete create/replace
set is reviewed and approved before any write. Existing files are never deleted.

## Important limits

- Generated output contains agent instructions and shared context. It is not an
  implementation of the described task.
- Offline validation cannot prove semantic correctness, automatic target file
  discovery, or behavior on every agent product surface.
- The application does not verify provider retention practices or guarantee
  secure erasure of deleted session data.

See [Product Requirements](docs/PRODUCT.md), [Architecture](docs/ARCHITECTURE.md),
and the [C4 architecture diagrams](docs/architecture-diagrams.md).
