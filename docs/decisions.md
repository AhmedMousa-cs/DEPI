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
