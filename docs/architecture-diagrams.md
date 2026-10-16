# C4 architecture diagrams

This set describes the implemented Harness Generator for contributors and users
who need to understand its boundaries and data flows. The diagrams use C4
system-context, container, component, and code levels.
Boxes identify C4 element types and technologies; arrows show the direction and
purpose of each interaction.

## System context

`diagrams/c4-context.drawio` shows the authenticated browser user, the Django
Harness Generator, configured model providers, and the account-scoped project
workspace on the application host. Non-local provider requests require
disclosure and approval; project-file writes require full-set approval.

## Containers

`diagrams/c4-containers.drawio` shows the Django web application, Django account
database, SQLite checkpoint database, operator-only application log files, and
interactions with the configured model provider and account-scoped project
workspace. Application logs may contain interview and model content and are
shared across accounts.

## Components

`diagrams/c4-components.drawio` expands the web container to show its browser
views, deterministic requirements interview, optional model-assisted
topic-based interview, session/workflow, provider, generation, validation, and
artifact-writing responsibilities. It answers how confirmed requirements move
through generation and approval before files are applied. The graph checkpoint
preserves state; per-account provider credentials are encrypted in the Django
database.

## Code

`diagrams/c4-workflow-code.drawio` expands the workflow component into its typed
state, graph factory/router, phase-driven nodes, interrupt points, and direct
service collaborators. It answers how `workflow.py` routes a reviewed request
through provider approval, generation, validation, destination review, and
approved writes, and how `sessions.py` resumes the checkpointed graph.

All diagrams are editable draw.io XML source. They describe the implementation
documented in [Architecture](ARCHITECTURE.md); generated harness files are
written to the user-selected project and are not part of this application.
