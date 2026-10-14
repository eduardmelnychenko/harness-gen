# Product Requirements

## Purpose

An agent harness is the software, configuration, and documentation around a large language model (LLM) that enables it to perform goal-directed tasks. This product helps a user create a tailored agent harness through a natural-language conversation and produces a set of files that define the harness.

## Intended users and outcome

The intended user is a person who wants to describe an agent's goals and constraints without having to design every harness file by hand. At the end of a successful session, the user has a coherent set of harness files, understands the important choices made, and can identify any remaining setup they must perform.

## Scope

The product gathers requirements and generates harness files. It does not promise that a generated harness will work with every LLM, runtime, tool, or deployment environment. Compatibility depends on the requirements and supported targets made available by the product.

## Functional requirements

### Conversation and requirements

* The product must accept requirements expressed in natural language.
* It must gather enough information to define the requested harness, including its purpose, tasks, operating context, constraints, and desired outputs.
* It must let the user select one or more task types: regular software development, a machine learning (ML) task, or a data science task. It must also offer an `Other/custom` option for work that does not fit these categories. The product must use the selection to guide follow-up questions and capture relevant requirements without overriding the user's specific needs.
* Deterministic core and task-profile question templates must be managed separately from optional model-generated interview questions. Deterministic questions remain mandatory where required and authoritative for all readiness gates.
* The user may opt in to provider-backed interview assistance. Before interview content is sent to a provider, the product must show the provider disclosure and obtain approval when required. The model may suggest up to three additional questions; the product must validate them, label them optional, and let the user choose which to answer. Supplemental answers may add context but must not replace deterministic answers or satisfy readiness gates.
* During the interview, the user may ask the model for guidance on how to answer an active deterministic question. Coaching must not populate, confirm, or change the user's answer. If the user does not opt in, the deterministic interview must work as before without model requests.
* Before a data-science session is ready, it must collect a user-confirmed data source and analysis goal. Unknown details must remain unresolved. It must ask about platform only when platform affects setup or use.
* For `Other/custom`, the product must ask focused questions to understand the task and its requirements. If it cannot support the clarified task, it must explain the limitation and must not present incomplete output as a complete harness.
* The user must be able to choose a supported target agent. The product must distinguish the target agent, which determines generated file formats, from the LLM provider, which receives requirements. Ollama is a provider, not an agent target. Show supported targets as clear choices; if the user enters a provider name or unsupported target, explain the difference and ask them to choose a supported target before sending requirements.
* It must ask focused follow-up questions when required information is missing, ambiguous, or contradictory. It must not silently treat a material assumption as a confirmed requirement.
* When setup depends on the user's environment, it must capture confirmed platform, runtime, dependency, configuration, and verification details in structured requirements. Unknown material setup choices must remain unresolved rather than being guessed.
* It must retain relevant answers during the current harness-creation session and use them consistently in generated files.
* Before generation, it must summarize the captured requirements and surface unresolved assumptions or choices that could materially affect the result.
* It must let the user correct or clarify that summary before files are finalized.
* It must communicate limitations, missing information, and failures in terms the user can understand, and identify what input or action is needed next.
* The user may pause, resume, or restart a session at any time. Pause and resume must preserve and restore the full session state, including confirmed requirements, selected task types and target agent, unresolved questions, and generated files. Restart must begin a new session with the prior requirements and choices carried over as editable starting points, but without carrying over generated files. Restart must not delete files already generated or other user files.
* The product must provide an authenticated Django web interface using Jinja2 templates and Bootstrap 5 or later. Accounts must be created by an administrator. Each account must have one active session and must not access another account's requirements, generated files, or provider configuration.
* Each account must be able to configure its own model provider and model. Provider credentials must be encrypted at rest with a key supplied by the server operator and must not be included in session state.

### LLM provider configuration

* When the product uses an LLM, it must access providers through a shared provider-neutral interface.
* The product must support Ollama, OpenAI, Azure OpenAI, Meta's hosted API, Google Gemini, and Anthropic Claude.
* The default provider must be Ollama at a local endpoint. The user must be able to configure the provider and model.
* The product must report an unavailable or incorrectly configured provider and must not silently switch to another provider.

### Harness generation

* The product must use the configured LLM to convert reviewed, confirmed requirements into a complete, coherent set of files that define the harness.
* It must not generate final files while material choices remain unresolved. It must explain which choices need clarification and let the user resume requirements gathering.
* Generated files and guidance must reflect the selected task type or combination of types, as well as the confirmed requirements.
* Generated files must follow the selected target agent's supported conventions and formats. The product must not present a harness for an unsupported target as compatible.
* The product must distinguish artifact types supported by the selected target from unsupported types. It must not claim support based only on being able to render text.
* The product must generate a root `AGENTS.md` containing shared context derived only from confirmed requirements. It must identify this file as instructions/context, not target settings. Target-specific instruction files must retain the confirmed requirements for product surfaces that do not load `AGENTS.md`. User corrections must update earlier details, and unresolved items must not be treated as confirmed.
* Generated outputs are the selected target's core instructions, shared `AGENTS.md` context, and any selected task-type or path-scoped instruction files.
* The product must not present generated instructions as an implementation of the user's task. It does not generate task-specific application code, project scaffolds, native target settings, tool configuration, hooks, setup guides, or editable setup records.
* The user must be able to select optional task-type-specific instruction files and path-scoped instruction files. If the user does not select separate task-type files, the core instruction file must still reflect the selected task types.
* Path-scoped instructions must use exact relative glob patterns supplied and confirmed by the user. The product must not infer or broaden a path pattern. If path rules are selected but no patterns are confirmed, it must block final generation and ask the user to supply and confirm patterns.
* Scoped instruction files must use formats supported by the selected target. The product must identify target-surface limits rather than claim universal support.
* Files that describe the same behavior must not contain known contradictions. If a requirement cannot be represented or generated, the product must identify it rather than silently omit it.
* The product must identify the purpose of generated files and any required user setup, such as credentials, dependencies, or environment-specific values. It must not include real credentials in generated output.
* The product must provide the user with access to the generated files and a way to distinguish generated content from information or files supplied by the user.
* The product must not silently overwrite existing user content. Before replacing or deleting user content, it must explain the effect and get the user's approval.
* When generation fails or validation does not pass, the product must identify the affected outputs and must not present them as ready to use.
* Before writing generated files, the product must show the selected project directory and identify each file to create or replace. It must obtain approval for the full set of changes and block writes after failed or incomplete validation.
* The web application must limit each account's project destinations to its own configured workspace. It must not accept arbitrary server paths from browser input.


### Language and terminology

* User-facing conversation and human-readable generated text must use ASD-STE100 controlled English where applicable.
* Programming languages, configuration formats, identifiers, and other machine-readable syntax must remain valid for their target format. ASD-STE100 requirements apply to accompanying human-readable text, not to syntax that cannot conform to the standard.
* The product must use technical terms consistently and explain terms that the intended user may not know.

## Non-functional requirements

### Security and privacy

* The product must protect user requirements, conversation content, and generated files from unauthorized access.
* The product must authenticate users and enforce account ownership on every session and provider-profile operation.
* Configured provider credentials must not be included in saved sessions, generated files, or application error messages. The product must not ask users to enter real secret values in requirements or generated content.
* Per-account provider credentials must be encrypted at rest. The operator must supply and back up the encryption key; loss of the key can make saved provider credentials unreadable.
* Setup requirements must refer to credentials through local placeholders or environment-variable names, not credential values.
* The product does not scan or redact arbitrary user text for secrets. It must warn users not to enter secret values and explain that user-provided text can be saved, sent to an approved provider, or included in generated files.
* The product must explain relevant data handling, including whether session content is stored, where it is sent, and when it is removed. It must not claim privacy or retention guarantees that the implementation does not provide.
* Any external service or model use must be made clear to the user before data is sent to it.

### Reliability and correctness

* The product must preserve confirmed requirements throughout a session and must not report success when required generation or validation steps have failed.
* The product must report actionable errors when it cannot continue, generate a requested file, or validate an output.
* Generated files must conform to their declared formats. Where automated validation is available, the product must use it and report its result.
* The web application must log operational events without raw requirements, conversation content, provider prompts or responses, or credentials.
* `HARNESS_CREATOR_LOG_LEVEL` must configure the application log level. Use `INFO` when it is unset and `DEBUG` when its value is unsupported.
* `HARNESS_DB_URL` must configure the database connection URL. Use `sqlite:///data/harness.db` when it is unset or unsupported.

### Maintainability and extensibility

* The product must separate requirement gathering, harness generation, and output validation sufficiently to allow each capability to evolve without requiring unrelated changes.
* Supported harness targets, file types, and capabilities must be extendable without changing the meaning of existing requirements or silently changing previously supported behavior.
* Architecture and supported capabilities must be documented sufficiently for contributors to understand how to extend them.

### Usability and accessibility

* The conversation must present one clear question or decision at a time when user input is needed.
* The user must be able to understand progress, review important decisions, and know how to resume or correct the work when supported by the application.
* Errors and limitations must include a clear next action when one is available.
* User-facing flows must be usable with the accessibility capabilities supported by the product's interface and target environment.

## Acceptance criteria

A harness-creation session meets the product requirements when:

* The user can describe a harness request in natural language.
* The user can select one or more of regular software development, an ML task, or a data science task, and the product uses those selections to guide requirements and generated outputs.
* The user can select `Other/custom`; the product clarifies the task requirements or explains why it cannot support the task before presenting generated output as complete.
* Deterministic question templates remain the source of required answers and readiness gates. When the user opts in, the product discloses and obtains any required provider approval before sending interview content, validates and labels up to three LLM suggestions as optional, and lets the user select which to answer.
* Optional answers are stored separately and do not satisfy or replace deterministic readiness gates. The user can request coaching on an active deterministic question; coaching is shown as advice and does not change the answer. Without opt-in, no LLM request is made during the interview.
* The user can select Claude Code or another supported agent as the target from a clear list. If the target is unsupported or unclear, the product explains the limitation and asks the user to choose again.
* The product makes clear that Ollama is an LLM provider, while Claude Code and GitHub Copilot are agent targets that determine generated file formats.
* The product asks for material missing or unclear information instead of silently guessing.
* Material setup requirements are captured as confirmed details, or listed as unresolved; the user can review them before generation.
* The user can review and correct the captured requirements before final generation.
* The user can choose separate task-type and path-scoped instruction files or keep the core file only; path patterns are visible in the review.
* Pausing and resuming restores the full session state, including requirements, task types, target agent, unresolved questions, and generated files. Restart creates a fresh session with prior requirements and choices as editable starting points, no carried-over generated files, and no deletion of existing files.
* The configured LLM generates complete harness instruction files from reviewed requirements. The files preserve confirmed details consistently and follow the selected target agent's supported conventions; invalid or unsupported output is reported and cannot be written.
* The generated file set includes a shared `AGENTS.md` with confirmed context only. Target-specific instructions point to it and retain the confirmed requirements for surfaces that do not load it.
* Material requirements must be resolved before final generation. The product reports open choices and resumes the requirements conversation instead of generating incomplete files.
* Task-specific instructions reflect the selected task types. Path-scoped instructions use only relative patterns that the user supplied and confirmed during review.
* The user can access the generated harness files and understand their purpose and required setup. Before a project write, the user sees the selected directory and each file to create or replace; any validation status other than pass blocks writing.
* User requirements, conversation content, and generated files are protected from access by unauthorized users.
* Each signed-in account can access only its own session and provider configuration. Generated project writes remain within that account's workspace.
* The product uses a Django web UI with Jinja2 and Bootstrap 5 or later; user accounts are created by an administrator.
* Configured provider credentials are not saved with session content or exposed in application errors. The product warns users not to enter secret values. It does not detect or redact secrets that users include in requirements, so such text can be stored, sent after provider approval, or included in generated files.
* Before user data is sent to an external service or model, the product explains which service receives it and the applicable data storage, transmission, and removal practices.
* The product does not replace or delete existing user content without explaining the effect and obtaining the user's approval.
* Generated files conform to their declared formats. When automated validation is available, the product runs it and reports the result.
* Invalid, incomplete, or failed generation is reported as such, with actionable information where possible.
* User-facing conversation and applicable human-readable output follow the language requirements above.
