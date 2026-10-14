# Architecture

## Status and scope

Harness Generator is an authenticated Django web application. Django serves
Jinja2 templates styled with Bootstrap 5.3. The browser UI drives the existing
LangGraph workflow and domain services for deterministic requirements,
provider-backed assistance, generation, offline validation, artifact review, and
approved writes. The interactive CLI is no longer an application entry point.

The product creates harness instruction files for Claude Code and GitHub
Copilot. It does not create application code, scaffolds, native target settings,
tool configuration, setup guides, or command-running hooks. Offline validation
does not prove semantic correctness, target discovery, or behavior on every
agent product surface.

## Technology decisions

| Area | Decision |
| --- | --- |
| Runtime | Python 3.14 or later; dependencies and lockfile managed by `uv`. |
| Web framework | Django 5.2, Django authentication, CSRF-protected POST actions, and administrator-created accounts. |
| Templates and styling | Django Jinja2 backend and Bootstrap 5.3 styles. Django's standard template backend remains enabled for the admin. |
| Session workflow | LangGraph with the SQLite checkpointer. One active thread is indexed per Django account. |
| Django persistence | A separate SQLite database stores Django accounts and provider profiles. `HARNESS_DJANGO_DB` selects its path. |
| Workflow persistence | `HARNESS_DB_URL` selects the checkpointer database, defaulting to `sqlite:///data/harness.db`. The web app does not import or adopt legacy unowned CLI sessions. |
| Provider configuration | Each account has a provider profile. Credentials are encrypted with Fernet using `HARNESS_CREDENTIAL_ENCRYPTION_KEY`; decrypted credentials are runtime-only. |
| Project destinations | Writes are restricted to `HARNESS_WORKSPACE_ROOT/<account-id>`. The selected project directory must already exist. |
| LLM integration | A provider-neutral interface supports Ollama, OpenAI, Azure OpenAI, Meta's hosted API, Google Gemini, and Anthropic Claude. The user configures the provider separately from the target agent. |

## Components

| Component | Responsibility |
| --- | --- |
| Django project | `harness_gen.web` configures settings, URL routing, WSGI/ASGI, Jinja2, authentication, CSRF, static handling, and the Django database. `manage.py` and `harness-gen-web` run Django management commands. |
| Web application | `harness_gen.webapp` provides login/logout, administrator-managed account access, provider settings, session pages, forms, and Jinja2 templates. Mutations use POST requests protected by Django CSRF middleware. |
| Provider profile | A one-to-one profile stores the provider, model, endpoint, and encrypted credential. Runtime settings are built from the profile without adding credentials to graph state. |
| Interview workflow | The UI uses the deterministic question templates and answer/readiness logic. Optional suggestions and coaching require user opt-in; remote providers require disclosure and approval. Supplemental answers remain separate from deterministic answers. |
| LangGraph workflow | Typed workflow state and interrupt nodes manage requirements review, provider disclosure, generation, validation, destination review, write approval, and apply. |
| Session manager | `SessionManager` loads the active thread for the authenticated account, checkpoints answers, resumes decisions, and supports restart and deletion. `active_sessions` is keyed by owner ID. A web request cannot select a thread ID directly. |
| Provider adapters | Direct provider adapters implement one shared model contract. Calls require a configured provider; the app does not silently switch providers. |
| Generation planner and LLM | The planner fixes the output file set and paths. The provider supplies content only for those planned artifacts. |
| Output validator | Checks exact planned paths, unique safe paths, required confirmed details, shared-context references, and target-native frontmatter. Passing validation is required before review and write. |
| Artifact manager | Snapshots the destination and the full create/replace set, binds approval to those snapshots, rejects unsafe paths and symbolic links, and applies files atomically one at a time. Files are never deleted. |

## Browser workflow

1. An administrator creates user accounts in Django admin. Users sign in and configure their own model provider and credentials.
2. A user starts a session with a natural-language request. Deterministic task-profile questions are checkpointed as browser decisions. The target agent is a separate choice from the model provider.
3. The user may opt in to interview assistance. The app shows the provider disclosure and obtains approval before external-provider requests. Suggestions are limited and optional; coaching does not change the answer.
4. The app presents the structured requirements summary. Users can edit confirmed values and open choices. Material blockers prevent generation.
5. Before generation, the user reviews and approves the provider disclosure when required. Generation uses reviewed requirements and the configured provider.
6. Generated files are validated and previewed. The user selects an existing project directory inside their own workspace, reviews every file to create or replace, and approves the entire set.
7. The graph checkpoints the active prompt and full workflow state. Users can resume, restart from editable requirements without generated files, or delete their own saved session. Deletion does not guarantee secure erasure.

The browser session UI maps each graph interrupt to a page action. It must not bypass provider approval, material-requirement gates, validation failures, stale artifact checks, or full-set write approval. The graph remains the source of truth for workflow progression.

## Security and privacy

- Django authentication protects application pages. Accounts are administrator-managed; there is no public registration flow.
- Session thread lookup is keyed by the authenticated user's database ID. Each account has one active thread. Legacy unowned CLI threads are not adopted by web accounts.
- Django's database file is prepared with restrictive file permissions. Provider credentials use authenticated Fernet encryption at rest; losing or changing the encryption key makes saved credentials unreadable.
- Credentials are excluded from LangGraph state, templates, generated files, and operational error text. Logs must not include user requirements, provider prompts/responses, or credentials.
- Django application logging uses `HARNESS_CREATOR_LOG_LEVEL` (`INFO` by default, `DEBUG` for unsupported values) and records operational events only.
- External provider use requires a disclosure describing the data categories sent. The app does not claim to verify provider retention.
- User text is not scanned or redacted for secrets. It can be checkpointed, sent after approval, or included in generated instructions.
- CSRF middleware protects state-changing requests. Session cookies use `HttpOnly` and both session and CSRF cookies use `SameSite=Lax`; cookies require HTTPS outside local development.
- Each user can select destinations only inside `HARNESS_WORKSPACE_ROOT/<account-id>`. The artifact manager additionally checks path safety and symbolic links. The workspace root must be access-controlled by the operator.
- Bootstrap CSS is loaded from jsDelivr. No requirements or generated file content is sent to that asset host.

## Deployment and configuration

Use `uv sync --locked`, configure `DJANGO_SECRET_KEY` and
`HARNESS_CREDENTIAL_ENCRYPTION_KEY`, run `python manage.py migrate`, create an
administrator with `python manage.py createsuperuser`, and start Django with
`python manage.py runserver` for local development. Do not enable `HARNESS_DEBUG`
in a hosted deployment. Configure `HARNESS_ALLOWED_HOSTS`,
`HARNESS_CSRF_TRUSTED_ORIGINS`, and HTTPS at the serving proxy as required.

The Django account/profile database and LangGraph checkpoint database are
separate. Back up both databases and the credential encryption key. The default
workspace root is `workspaces/`; users must place projects within their own
account subdirectory before selecting them. Production deployments must provide
appropriate filesystem isolation and backups for the workspace root.

## Extension boundaries and limits

- Add task questions in `question_templates.py`; deterministic questions remain
  authoritative for readiness.
- Add supported provider adapters through the shared provider interface.
- Add target capabilities to the typed catalog. Do not claim support solely
  because the application can render text.
- Add artifact validation for the supported file format and preserve the
  explicit review/write approval boundary.
- Offline checks do not prove semantic correctness, target discovery, or actual
  behavior in every target surface. Live provider behavior and retention are not
  verified by the test suite.
