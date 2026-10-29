# NEXUS: Architecture Decisions

Date: ____ | Team: ____

These decisions are agreed upon and must not be changed without a documented reason. Assumptions still require confirmation.

| Topic | Decision | Notes |
|---|---|---|
| Backend | Python 3.11+ | |
| API | FastAPI + Uvicorn | |
| Database | PostgreSQL with pgvector | Required for PostgresSaver, pgvector, and Docker |
| ORM and migrations | SQLAlchemy 2 + Alembic | |
| Validation | Pydantic v2 | Agent outputs must conform to schemas |
| Agents | LangChain v1 (`create_agent` + middleware) | Use for tool-calling loops |
| Orchestration | LangGraph (`StateGraph`, `interrupt()`, `Command(resume=...)`) | One graph definition, one instance per project |
| Checkpointer | PostgresSaver | MemorySaver is for quick tests only |
| LLM | Anthropic via `langchain-anthropic` | Configure model through environment variables |
| Observability | Langfuse Cloud | Free tier; self-hosting is optional |
| Evaluations | DeepEval with pytest + deterministic tests | |
| Background jobs | Jobs table + worker process + polling | No Celery or Redis |
| Supported files | TXT, Markdown, DOCX, and text-based PDF | Maximum 5 MB; no OCR |
| Authentication | JWT + seeded accounts; PM and TEAM_LEADER roles | No self-registration |
| Frontend | Keep the existing frontend and connect it to the API | Revisit the final frontend decision at the end of M2 |
| Deployment | Docker Compose: database, API, worker, and frontend | Single-machine deployment |
| LLM budget | Per-job token limit and daily spending limit | Configure through environment variables |

## Non-Negotiable Architecture Rules

1. The database is the source of truth. Graph state represents execution progress only.
2. Agents may only propose changes. They create drafts through `save_draft_*` functions. Approval must use endpoints protected by the PM role.
3. Every extracted item must include `source_id`, `quote`, `run_id`, `created_by_agent`, `status`, and `confidence`.
4. Quotes must be verified programmatically. If a quote does not appear verbatim in the source, mark the item as `unverified` and reduce its confidence.
5. The Global Supervisor must not modify project data. It only produces recommendations.
6. Every applied plan change requires PM approval, creates a new `plan_version`, and generates an `audit_log` entry.

## Out of Scope

- OCR
- Meeting recording and transcription
- Automatic sending of external messages
- An independent agent instance for every project
- Automatic resource optimization
- Full multi-tenant support
- Email notifications

## Decision Log

| Date | Decision | Reason |
|---|---|---|
| | | |

## Architecture Decision Records

### ADR-002: Deferring Full Project Access Control & Audit Triggers to M2
- **Status:** Approved
- **Context:** Core DB models and initial migrations are verified in M1.
- **Decision:** Defer complex RBAC access rules and detailed audit log triggers to Milestone 2 to focus on Project & File Ingestion APIs first.
- **Consequence (M2):** Every M2 create/update endpoint writes an `audit_log` row in the same transaction, and project-scoped endpoints use `require_project_access`; both are covered by tests.

### ADR-003: Single Alembic head and isolated test database
- **Status:** Approved
- **Context:** A duplicate `initial_schema` revision produced two root heads and broke `alembic upgrade head`. Tests also ran against the development database.
- **Decision:** One linear migration chain, enforced by `test_alembic_has_single_head`. Tests run only against `nexus_test` (the suite refuses any database whose name does not end in `_test`), built from the real migrations and cleaned after every test.

### ADR-004: Frontend continues as vanilla JS (decision at end of M2)
- **Status:** Approved
- **Context:** The blueprint requires a keep-or-migrate decision at the end of M2. The inherited prototype stored everything in `localStorage`, which the M2 DoD forbids.
- **Decision:** Keep the existing vanilla JS + CSS front-end (no React). `app.js` was rewritten to use the API as the only data source, reusing `styles.css` and `auth.js`. Screens for later milestones (scope review, plan, updates, decisions) are disabled until their milestone and will be rebuilt on the real API.
- **Why:** Login, project, sources and team screens, file upload, and CSV import were all delivered without a build step or framework; the app has few screens; Docker/CI stay simple.
- **Revisit if:** M7 (change review with diffs) or M8 (portfolio dashboard) needs more client-side state than plain JS can maintain cleanly.
- **Consequence:** The old localStorage prototype remains available in Git history only.

### ADR-005: Source ingestion and CSV import rules (M2)
- Only extracted text is stored; uploads are never written to disk or executed.
- Limits: 5 MB per file; types `.txt .md .docx .pdf`; scanned or empty PDFs are rejected (no OCR).
- Team CSV import is all-or-nothing, reports errors per line, and rejects duplicate names.
- A Team Leader gets project access only through `project_members`, created when the PM links a team member to a user with the `TEAM_LEADER` role.
