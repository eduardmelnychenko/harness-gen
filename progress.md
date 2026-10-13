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