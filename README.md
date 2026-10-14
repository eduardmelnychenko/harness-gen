# Harness Generator

Harness Generator turns a description of an AI agent's goals and constraints into
reviewable instruction files for a supported coding agent. You describe what the
agent should do; the tool gathers the missing details, creates the files, checks
their format and content, and asks before it writes them to a project.

It is useful when you want a consistent set of agent instructions without
designing every file by hand. It creates harness instructions, not application
code, project scaffolding, runtime settings, or deployment configuration.

## What you can create

- **Software development instructions** for an agent's goals, working context,
  constraints, and expected outputs.
- **Machine-learning instructions** tailored to the task and its confirmed
  setup requirements.
- **Data-science instructions** that require a confirmed data source and
  analysis goal before generation can continue.
- **Custom-task instructions** after the tool has clarified the task and
  confirmed that it can support it.
- **Optional task-specific or path-scoped instructions** when you want different
  guidance for selected task types or exact file patterns.

Choose a supported target agent separately from the model provider. The target
sets the instruction-file format:

| Target | Core instructions | Shared context |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` | `AGENTS.md` |

Optional task-specific and path-scoped files use each target's supported
locations. Some Copilot surfaces do not load path-scoped instructions. The tool
reports this limit when relevant.

## Requirements

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- A configured model provider. The default is a local [Ollama](https://ollama.com/)
  service with the `llama3.2` model. Install and start Ollama, then make that
  model available with `ollama pull llama3.2`, or configure another provider.

The tool also supports OpenAI, Azure OpenAI, Meta's hosted API, Google Gemini,
and Anthropic Claude. External providers require their own credentials and
approval before requirements are sent.

## Install and run

From the project directory, install the locked dependencies and start the
interactive CLI:

```sh
uv sync --locked
uv run harness-gen
```

You can also start it as a Python module:

```sh
uv run python -m harness_gen
```

The first run checks the provider configuration, then asks for a natural-language
description of the harness. Follow the prompts to select task types and a target,
answer focused questions, and review the requirements summary. Resolve any
material open questions before generation.

The requirements interview uses deterministic questions and readiness checks.
Optional interview assistance is off until you enter `:ai` at a question. The
CLI shows the provider disclosure and asks for approval before it sends interview
content to an external provider. After opt-in, enter `:suggest` to request up to
three optional questions and choose which ones to answer. Enter `? your question`
to ask for coaching on the active deterministic question. Suggested questions
and answers do not replace required questions or readiness checks, and coaching
does not fill in your answer.

The workflow then requests provider approval when needed, generates and
validates the planned instructions, and shows the results. To write passing
files, select an existing project directory, review the full list of files to
create or replace, and approve the write. The tool does not delete project
files.

## Configure the model provider

Set configuration in the process environment or in a local `.env` file in the
working directory. The project ignores `.env`; do not commit it. Provider
credentials stay in runtime configuration and are not added to session state
or generated instructions.

| Provider | `HARNESS_LLM_PROVIDER` | Other required settings |
| --- | --- | --- |
| Ollama (default) | `ollama` | Optional `HARNESS_LLM_MODEL` (defaults to `llama3.2`); optional `HARNESS_OLLAMA_BASE_URL` (defaults to `http://localhost:11434`) |
| OpenAI | `openai` | `HARNESS_LLM_MODEL`, `OPENAI_API_KEY`; optional `HARNESS_OPENAI_BASE_URL` |
| Azure OpenAI | `azure_openai` | `HARNESS_LLM_MODEL` (deployment name), `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`; optional `AZURE_OPENAI_API_VERSION` |
| Meta hosted API | `meta` | `HARNESS_LLM_MODEL`, `LLAMA_API_KEY`; optional `HARNESS_META_BASE_URL` |
| Google Gemini | `google_gemini` | `HARNESS_LLM_MODEL`, `GOOGLE_API_KEY` |
| Anthropic Claude | `anthropic` | `HARNESS_LLM_MODEL`, `ANTHROPIC_API_KEY` |

For example, a local `.env` file for OpenAI has this shape; replace the model
placeholder locally and provide your own key:

```dotenv
HARNESS_LLM_PROVIDER=openai
HARNESS_LLM_MODEL=your-model-name
OPENAI_API_KEY=your-api-key
```

Do not enter keys or other secrets in a harness request. The tool does not scan
or redact request text. It saves requirements and generated files in its
session database. It sends content to an external provider only after you
approve the provider disclosure. Review that provider's data-handling terms.

## Session and run behavior

The CLI creates or resumes one active session. At startup, it shows the session
database path, the configured provider, and the operational log path. The
default database is `data/harness.db` under the current working directory. A
separate timestamped log file is created under `logs/` for each run; logs record
operational events, not request text or provider content.

At prompts that accept commands:

- Enter `:pause` to keep the current session and exit. `:quit` also exits and
  preserves the active checkpoint.
- Run the CLI again and choose **resume** to continue the saved session.
- Choose **restart** to begin a new thread with the previous requirements as
  editable starting points. Generated files are not carried over, and existing
  project files are not changed.
- Choose **delete** to remove the saved session checkpoint. This does not
  delete project files or guarantee secure erasure from storage.

Change the database location with `HARNESS_DB_URL`, for example:

```dotenv
HARNESS_DB_URL=sqlite:///data/harness.db
```

Change the operational log level with `HARNESS_CREATOR_LOG_LEVEL`. The default
is `INFO`; unsupported values use `DEBUG`.

## Deployment

Harness Generator currently runs as a local, interactive CLI. It does not
provide a web service or a background worker. Install it with `uv` on the
machine where you want to run it, configure a reachable model provider, and
launch it from the working directory where you want its session database and
logs. Back up the session database if you need to preserve a session.

## Important limits

- Generated files contain agent instructions and shared context. They are not
  an implementation of the task described in the request.
- Generation and offline checks do not prove semantic correctness or guarantee
  behavior on every agent product surface.
- External-provider calls are not made until you approve the disclosure. The
  tool does not verify provider retention practices.
- The active session and generated files are stored in the SQLite checkpoint
  database. Deleting a session removes checkpoint records, but does not
  guarantee secure erasure from storage.

See [Product Requirements](docs/PRODUCT.md) for the intended behavior and
[Architecture](docs/ARCHITECTURE.md) for implementation details and validation
limits. See the [C4 architecture diagrams](docs/architecture-diagrams.md) for
the system context, container, and component views.
