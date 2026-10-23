# MAPMS
**Multi-Agent Project Management System**

MAPMS is a multi-agent project management system designed to transform project meeting transcripts into validated, auditable, and versioned project updates, while keeping project decisions under authorized human control.

## Current Development Status

**Phase 1: Foundation & Testing Infrastructure**

The initial development foundation is in place, including:

- PostgreSQL development environment using Docker Compose.
- Automated tests for database concurrency and transaction rollback behavior.
- Import boundary enforcement using Import Linter.
- CI checks for tests, architecture boundaries, and migration history.

The foundation tests and CI checks currently pass. The full multi-agent workflow is still under development.

## Initial Development Mode

**Replay Mode** with deterministic fixtures.

The initial implementation focuses on reproducible execution and verifiable state transitions before introducing more advanced execution modes.

## Development Requirements

- Python 3.11
- Git
- Docker Desktop
- PostgreSQL (provided through Docker Compose)

## Planned First Vertical Slice

The first end-to-end workflow is designed to process a meeting transcript through the following stages:

1. **Transcript Ingestion** — Receive a project meeting transcript.
2. **Statement Validation** — Extract and validate statements from the transcript.
3. **Proposal Generation** — Convert relevant statements into proposed project changes.
4. **Conflict Detection** — Identify conflicts with the currently accepted project state.
5. **PM Decision** — Request an authorized Project Manager decision where required.
6. **State Update** — Apply approved changes and create a new accepted version.
7. **Verification** — Verify the resulting state and preserve an audit trail.

This workflow is a planned development target, not a claim that the complete vertical slice is already implemented.

## Safety & Architecture Principles

- **Proposals are not accepted facts.** Proposed changes must not automatically modify the accepted project state.
- **Authorized decisions are required.** Material state changes must follow the defined authorization rules.
- **Auditable changes.** State transitions must preserve the information needed to trace and verify changes.
- **Controlled state management.** Accepted-state updates must go through the designated State Manager.
- **Concurrency and rollback safety.** Database operations must be tested for concurrent updates and transaction failures.
- **Architecture boundaries.** The Supervisor must not directly depend on project internals.
- **Test before integration.** Changes must pass the required tests and architecture checks before integration.

## Development Workflow

1. Clone the repository.
2. Set up the Python environment and Docker-based database.
3. Run the automated tests and architecture checks.
4. Review the current implementation before contributing.
5. Develop new components in focused branches and integrate them through reviewed changes.

## Project Repository

[MAPMS on GitHub](https://github.com/gehad-abdulaziz/MAPMS)
