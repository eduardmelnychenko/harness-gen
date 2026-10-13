# Startup Rules
Before writing any code, complete these steps in order:

1. Read this file completely. It defines the boundaries and working rules for this project.
2. Read docs/ARCHITECTURE.md, if it contains content, to understand the app structure. When progressing with a task, follow the architecture and design patterns in that document. If it is missing or empty, do not invent its contents until you have specific instructions. Record the gap and continue only when it does not prevent the task.
3. Read docs/PRODUCT.md to understand the product requirements.
4. Read feature_list.json, if it contains valid data, to understand the current feature status. If it's empty, infer the features from the product and architecture documents, and initialize it with status `not-started`. If it's invalid, preserve it unchanged and report the issue.
5. Read progress.md before work when prior decisions, blockers, or completed work may affect the task. Treat it as historical context; current source files and task instructions remain authoritative.
6. Inspect the repository for the relevant code, tests, and documented build or validation commands. Run `bash init.sh` only if that file exists.
7. If feature_list.json is missing or empty (zero bytes, whitespace only, or a valid feature-list structure with no feature records), initialize it with features directly supported by non-empty docs/PRODUCT.md and docs/ARCHITECTURE.md, and set each status to `not-started`. Preserve the established file structure when one exists. If the documentation is insufficient to identify features or the required file structure, do not create or modify the file; report the gap and ask for guidance instead of guessing. If feature_list.json is invalid JSON or does not match its established structure, preserve it unchanged and report the issue.
8. If any other required document besides progress.md is missing, empty, or invalid, do not invent its contents until you have specific instructions. Record the gap and continue only when it does not prevent the task. If a build or test fails, determine whether the failure is caused by the current change; fix task-related failures and report unrelated baseline failures without expanding the task.

## Boundaries

* Keep changes within the product requirements and the scope of the task. Do not add features or dependencies without a clear need.
* Preserve existing project behavior and conventions. Do not overwrite or discard unrelated user changes.
* Do not expose credentials, secrets, or private user data in source files, logs, examples, or generated output.
* Do not run destructive commands or make external changes unless the user explicitly requests them.
* Ask for clarification when requirements conflict or when a decision would materially change behavior. Otherwise, follow established project patterns and state any necessary assumptions.

## Step-by-Step Execution

* Treat one feature, one document, or one clearly scoped logical step as the unit of work for each run.
* If a request includes multiple units, implement only one unit in the current run. Leave the other units for later instructions; do not continue to them automatically.
* Complete and verify the current unit against its applicable done conditions before considering it complete.
* Before the final response, append a dated entry for the completed unit to progress.md, following the Project History rules below.
* At the end of the run, summarize the completed work, note any relevant validation or remaining blocker, and suggest the next recommended step. Then continue.

## Project History

* Use progress.md as the chronological history of project work. Preserve existing entries and append a new entry after each completed work unit.
* Include the date, work completed and affected files, tests or other validation with accurate outcomes, tests not run and why, and useful decisions, blockers, or follow-up work.
* Record facts only. Do not claim a check passed unless it was run, and do not include credentials, secrets, or private user data.
* If progress.md is missing or empty, create a concise heading before adding the first entry. Do not replace or rewrite existing history; append a correction if an earlier entry needs correction.

## Conventions

* Follow the existing architecture, naming, formatting, and dependency patterns. When no pattern exists, use the standard conventions for the language and tools already in use.
* Reuse existing helpers and validation before adding new ones. Keep changes focused and add or update tests for changed behavior where tests are available.
* Keep documentation consistent with the implemented behavior and product requirements.
* Use ASD-STE100 for user-facing conversation and generated files where required by docs/PRODUCT.md. Do not silently claim compliance when it cannot be verified.

## Definition of Done
A feature is done when all applicable conditions are met:

* The implementation meets the agreed requirements and does not introduce known regressions.
* Relevant tests and available build or validation commands pass. If a check cannot run, record why.
* The app launches and operates without errors in the relevant normal-use flow, when the app is runnable.
* Generated or changed files meet their format and language requirements, where applicable.
* The feature is recorded in feature_list.json with status `pass` and evidence that supports the result.
* Documentation is updated when the change affects documented behavior.

## Working with the Feature List
The feature_list.json file is the source of truth for project feature status. Keep its existing structure and use these status values:

* `not-started`: work has not started.
* `pass`: all applicable done conditions are met; include verifiable evidence.
* `fail`: the feature does not meet its requirements; include the reason and remaining work.
* `blocked`: work cannot proceed; include the blocker and what is needed to continue.
* Do not mark a feature `pass` based only on implementation or an unverified assumption.
* Never delete existing features. If the list is missing or empty, initialize it as described in the startup steps. If it is invalid, preserve it unchanged and report the issue; do not replace malformed data with an assumed feature list.
