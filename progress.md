# Project History

## 2026-10-13

- Initialized `feature_list.json` from the documented product capabilities. All features are `not-started`; no implementation evidence exists yet.
- Updated `docs/ARCHITECTURE.md` to match `docs/PRODUCT.md`: the database URL defaults to `sqlite:///data/harness.db` when `HARNESS_DB_URL` is unset or unsupported.
- Validation: parsed `feature_list.json`; confirmed 11 unique feature records with the requested fields and `not-started` status; staged and unstaged `git diff --check` passed; repository search found no stale database-default reference.
- Not run: application tests or build; the repository has no source code, tests, or declared dependencies yet.

### Provider configuration, logging, and adapters

- Added the provider configuration and adapter layer in `src/harness_gen/`: local Ollama defaults, explicit settings and credentials for six providers, validated endpoints, LangChain chat-model adapters, and safe configuration/provider error types.
- Added per-run timestamped logging with collision-safe file creation, owner-only file permissions, and the documented log-level fallback. Updated `pyproject.toml`, `uv.lock`, and `docs/ARCHITECTURE.md` with the implementation dependencies and runtime configuration.
- Validation: 11 unit tests passed; all six provider model classes constructed without network requests; Python compilation, diff checks, and `uv build` passed.
- Not run: live provider requests; provider credentials and network availability are not part of this offline validation. Provider approval and CLI log wiring remain for later work units.

### Requirements, target capabilities, and provider approval

- Added focused profiles for software development, machine learning, data science, and custom tasks. Added structured requirement values and gates for confirmed data-science inputs, unresolved material choices, custom-task support, and exact user-confirmed relative glob patterns.
- Added Claude Code and GitHub Copilot capability catalogs, target/provider distinction, target-native paths and frontmatter, and documented product-surface limits. Added provider disclosures, approval receipts bound to provider and endpoint, and an authorized model factory that blocks missing task/target choices and unapproved external providers.
- Updated `docs/ARCHITECTURE.md` with the supported profiles, paths, frontmatter, limitations, approval behavior, and public target documentation references. Updated `feature_list.json` to record the still-unwired CLI flows as incomplete.
- Validation: 35 unit tests passed; Python compilation, line-length, whitespace, feature-inventory, and package build checks passed.
- Not run: live provider requests or interactive CLI flow; the CLI and graph integration are later work units.

### Generation planning and offline validation

- Added a deterministic generation plan for target-supported core, shared, task-type, and path-scoped files. The provider returns content keyed to planned artifact IDs; the application controls paths and adds scoped frontmatter from the exact confirmed glob.
- Added structured provider generation from reviewed, confirmed requirements only, with up to three total attempts for invalid structure or offline validation findings. Provider failures are surfaced with safe messages and affected planned paths.
- Added validation for the complete planned file set, safe and unique paths, target path/type, frontmatter, required confirmed details in core and shared context, the `AGENTS.md` reference, and its shared-context/settings distinction. Only a passing result is marked writable.
- Updated `docs/ARCHITECTURE.md` with generation, retry, and validation behavior. Updated `feature_list.json`; the CLI and approved writer are still pending.
- Validation: 51 unit tests passed; Python compilation, line-length, feature-inventory, whitespace, and package build checks passed.
- Not run: live provider generation or CLI presentation; no configured provider was used for these offline checks.

### Artifact review and approved writes

- Added `src/harness_gen/artifact_manager.py` to present the complete create-or-replace set, bind approval to the destination, generated content and filesystem snapshots, reject stale reviews and unsafe paths, and apply approved files with per-file atomic operations. New directories and files use restrictive permissions; existing file permissions are retained. Partial writes identify applied files and created directories. No project files are deleted.
- Added artifact-manager tests, including changed reviewed-content rejection. Updated `docs/ARCHITECTURE.md` and `feature_list.json`; the writer service is implemented and tested, but the terminal approval flow is not integrated, so the end-to-end feature remains incomplete.
- Validation: all 61 unit tests passed; Python compilation, artifact-manager line-length, `git diff --check`, and `uv build` passed.
- Not run: interactive CLI/write workflow or live provider flow; those integrations are not implemented.

### Graph checkpoint and session lifecycle

- Added `langgraph` and `langgraph-checkpoint-sqlite` dependencies, typed workflow state, and a resumable graph input interrupt in `src/harness_gen/workflow.py`.
- Added `src/harness_gen/sessions.py` with the documented SQLite URL default/fallback, owner-only database and newly created directory permissions, a single active-thread pointer, checkpoint restore/pause/resume, restart from editable requirement fields without generated or approval state, and thread/checkpoint deletion. Session state accepts only declared JSON-compatible fields; provider approval state excludes credentials.
- Added session lifecycle tests and updated `docs/ARCHITECTURE.md` and `feature_list.json`. Checkpoint lifecycle primitives are implemented, but feature-specific graph routing and CLI recovery/integration remain incomplete.
- Validation: all 71 unit tests passed; Python compilation, line-length, feature-inventory JSON parsing, `git diff --check`, and `uv build` passed.
- Not run: full CLI journeys, provider-backed graph nodes, or live external provider calls; graph-to-domain and terminal integration are later work.

### Graph workflow and domain routing

- Expanded `src/harness_gen/workflow.py` into a typed graph that reviews and accepts corrections to requirements, discloses and approves external providers, invokes generation, revalidates output, inspects a destination, presents the complete write set, and applies only after approval. Endpoint changes invalidate pending provider approval; failed validation and declined approval stop before a write.
- Integrated the graph with `SessionManager`, `HarnessGenerator`, the offline validator, and the artifact manager. Added strict requirements and artifact-review checkpoint serialization so generated drafts and snapshots survive process restart without storing provider credentials.
- Added graph integration tests for requirements correction and readiness gates, provider decline/configuration changes, validation failures, write decline, full approved writes after checkpoint reopen, and restart without generated artifacts. Updated `docs/ARCHITECTURE.md` and `feature_list.json`; the CLI and provider-backed requirements interview remain incomplete.
- Validation: all 83 unit tests passed; Python compilation, line-length, feature-inventory JSON parsing, `git diff --check`, and `uv build` passed.
- Not run: interactive terminal journeys or live provider calls. CLI rendering and requirements interviewing are the next units.

### Line-oriented terminal workflow

- Added `harness-gen` and `python -m harness_gen` entry points in `src/harness_gen/cli.py` and `src/harness_gen/__main__.py`. The CLI loads environment and `.env` settings, creates a per-run log, reports database and retention limits, and presents the model provider separately from the target-agent choices.
- Added a checkpointed task-profile interview for purpose, task types, target, context, constraints, desired outputs, setup details, task-specific questions, and optional scoped outputs. `SessionManager.start_workflow()` now promotes the same draft thread into the generation graph; `SessionManager.state()` exposes the pending graph interrupt to the terminal driver. Requirements review supports structured corrections and confirms the displayed path patterns. The CLI previews generated contents and renders provider, destination, and full-set write decisions.
- Required the custom-task goal, inputs/outputs, success measure, and constraints before provider use or generation. Data-science source and analysis goal remain gated. The requirements questions are deterministic and local; no provider request is used to formulate follow-up questions.
- Added scripted CLI tests for generation and approved writes with a fake generator, target/provider distinction, requirements correction, exact scoped output, remote-provider decline, data-science blocking, log content exclusion, and pause/resume across process reopen. Updated `docs/ARCHITECTURE.md` and `feature_list.json`.
- Validation: all 88 unit tests passed; Python compilation, changed-file line-length checks, `feature_list.json` parsing, `git diff --check`, `uv lock --check`, and `uv build` passed.
- Not run: live model-provider requests.

### Integrated verification and safety documentation

- Hardened `SessionManager` checkpoint read/write error reporting so SQLite failures surface as actionable persistence errors. The CLI now distinguishes external-provider approval from local Ollama use in its data-handling notice.
- Added regression tests for checkpoint read/write failure recovery, provider-credential exclusion from checkpoint state, preservation of legacy session rows without importing them, and user-file preservation across restart/deletion. Extended disclosure assertions to cover all requirement data categories.
- Updated `docs/ARCHITECTURE.md` with verification commands, tested safety boundaries, and the limits of offline and live-provider verification. Updated `feature_list.json` with evidence for the integrated checks.
- Validation: all 92 unit tests passed; Python compilation, changed-file line-length checks, `feature_list.json` parsing, `git diff --check`, `uv lock --check`, and `uv build` passed.
- Not run: live model-provider requests or provider-retention verification.

### User README

- Added `README.md` with the product's user value, supported use cases and targets, local installation and run steps, provider configuration, session controls, deployment model, and data-handling and validation limits.
- Validation: documentation-only change; no application tests run.

### Hybrid interview assistance

- Moved deterministic core prompts and task-profile question data into `src/harness_gen/question_templates.py`, separate from optional model-assistance behavior.
- Added explicit `:ai` opt-in with provider disclosure and external-provider approval, `:suggest` for up to three validated optional questions selected by the user, and `? <question>` coaching for open-ended deterministic prompts. Supplemental answers are checkpointed and included in review and generation context, but remain separate from readiness gates. Coaching does not alter user answers, and restart clears model-consent state.
- Updated product, architecture, and user documentation and added feature inventory evidence. Added CLI, suggestion-validation, readiness-gate, generation-context, legacy-checkpoint, and restart-consent tests.
- Validation: all 101 unit tests passed; Python compilation, changed-file line-length checks, `feature_list.json` parsing, `git diff --check`, `uv lock --check`, and `uv build` passed.
- Not run: live model-provider requests.

## 2026-10-14

### C4 architecture diagrams

- Added editable draw.io diagrams for system context, containers, and CLI components in `docs/diagrams/`. They show the local CLI, checkpoint database, operational logs, configured LLM provider, selected project workspace, and approval-gated flows.
- Added the `c4-architecture-diagrams` feature record; the diagrams are indexed by `docs/architecture-diagrams.md`.
- Validation: Python XML parsing confirmed all three draw.io files are valid XML with unique cell IDs and valid cell references; all three documented diagram paths exist.
- Not run: visual rendering or interaction/layout inspection because a draw.io renderer was not available.

## 2026-10-15

### Checkpointed web interview chat

- Replaced the web interview's single-question presentation with an ordered chat transcript containing the initial request, deterministic questions and answers, selected optional questions and answers, and user/LLM exchanges. Legacy active interviews reconstruct their visible deterministic history from saved answers.
- Added opt-in contextual LLM chat while preserving provider disclosure and external-provider approval. The transcript is persisted in the existing checkpoint and passed separately to generation as contextual, non-authoritative input; reviewed requirements remain authoritative.
- Updated the session UI, provider data disclosure, product/architecture/README documentation, and feature inventory.
- Validation: all 105 unit tests and all 10 Django web tests passed; Python compilation, `feature_list.json` JSON parsing, Django system checks, and `git diff --check` passed.
- Not run: live provider requests; validation used offline tests and a mocked interview response.

### Topic-based requirements interview

- Added structured, validated topic-interview turns. After explicit task type and target choices, an opted-in model selects the next topic/question, extracts only supported user-stated requirements, and decides when the conversation is sufficient. Existing material-readiness checks can override a premature completion assessment and require a focused follow-up.
- Kept provider disclosure and approval, the ordered checkpointed transcript, the no-opt-in deterministic fallback, and explicit output-selection choices. Updated the session UI, product/architecture/README documentation, and feature evidence.
- Added tests for structured turn validation, topic extraction, the data-science readiness safeguard, and the full web interview path.
- Validation: 107 unit tests and 11 Django web tests passed; Python compilation, Django system checks, and `git diff --check` passed.
- Not run: live provider requests.

### Clickable interview choices and sticky navigation

- Replaced typed responses for fixed-answer web interview prompts with labelled Bootstrap checkbox/radio controls. Task type remains multi-select; target, yes/no, and custom-task support are single-select. Existing answer validation and readiness gates remain in place. Made the shared navbar sticky across the web app.
- Updated the product, architecture, README, and feature evidence. Added web coverage for choice rendering, multi-select answers, target validation, yes/no/custom-support options, and sticky navigation.
- Validation: all 12 web application tests passed with `uv run python manage.py test harness_gen.webapp`; `uv run harness-gen-web check` passed. The first pytest invocation used the system Python and could not import project dependencies; `pytest` is not installed in the project environment.
- Not run: the full unit suite or live provider requests.

## 2026-10-16

### Stage-level web logging

- Added a per-process operator-only web log under `logs/`, with mode `0700` on the log directory and `0600` on log files. Content-bearing records are enabled only within authenticated web-session handling; the legacy CLI does not emit raw stage content.
- Logged interview requests, answers, questions, model prompts/responses, generated model output, validation statuses/finding codes/artifact locations, and stage error codes. Configured provider credential fields are not included in log records.
- Updated authenticated-page notices, README, product and architecture requirements, and `feature_list.json` to explain shared-log access, retention, and the risk that user-entered secrets can appear because arbitrary text is not scanned or redacted.
- Validation: all 112 unit tests and 12 Django web tests passed; Python compilation, feature inventory JSON parsing, Django system checks, permission tests, and `git diff --check` passed.
- Not run: live provider requests.