# C4 architecture diagrams

This set describes the implemented Harness Generator for contributors and users
who need to understand its boundaries and data flows. The diagrams use C4 system context, container, component, and code levels.
Boxes identify C4 element types and technologies; arrows show the direction and
purpose of each interaction.

## System context

`diagrams/c4-context.drawio` shows the harness author, the local Harness
Generator system, configured model providers, and the user's project workspace.
It answers what the system does and what external resources it interacts with.
Provider transmission and project-file writes are approval-gated.

## Containers

`diagrams/c4-containers.drawio` shows the locally run Python CLI, its SQLite
checkpoint database, operational log files, and its interactions with the
configured model provider and selected project workspace. It answers where the
main application behavior and persistent data reside. There is no web service
or background worker.

## Components

`diagrams/c4-components.drawio` expands the CLI container to show its terminal,
requirements, session/workflow, provider, generation, validation, and artifact
writing responsibilities. It answers how a reviewed request moves through
generation and approval before files are applied. The graph checkpoint
preserves state; credentials remain in runtime configuration.

## Code

`diagrams/c4-workflow-code.drawio` expands the workflow component into its typed
state, graph factory/router, phase-driven nodes, interrupt points, and direct
service collaborators. It answers how `workflow.py` routes a reviewed request
through provider approval, generation, validation, destination review, and
approved writes, and how `sessions.py` resumes the checkpointed graph.

All diagrams are editable draw.io XML source. They describe the implementation
documented in [Architecture](ARCHITECTURE.md); generated harness files are
written to the user-selected project and are not part of this application.
