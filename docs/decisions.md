# NEXUS: Architecture Decisions

Date: 2026-10-30 | Team: NEXUS (DEPI)

These decisions are agreed upon and must not be changed without a documented reason.

| Topic | Decision | Notes |
|---|---|---|
| Backend | Python 3.11+ | Containers run 3.11 |
| API | FastAPI + Uvicorn | |
| Database | PostgreSQL with pgvector | Required for PostgresSaver, pgvector, Docker |
| ORM and migrations | SQLAlchemy 2 + Alembic | One linear chain (ADR-003) |
| Validation | Pydantic v2 | Agent outputs must conform to schemas |
| Agents | LangChain v1 (`create_agent` + middleware) | For tool-calling loops (M6+) |
| Orchestration | LangGraph (`StateGraph`, `interrupt()`, `Command(resume=...)`) | One graph definition, one thread per project |
| Checkpointer | PostgresSaver | MemorySaver only in quick tests |
| LLM | Groq (`openai/gpt-oss-120b`) behind `llm/factory.py` | Deviation from the blueprint: ADR-006 |
| Observability | Langfuse Cloud | Free tier; self-host optional |
| Evaluations | DeepEval + deterministic pytest | ADR-009 |
| Background jobs | Jobs table + worker + polling | No Celery or Redis |
| Supported files | TXT, MD, DOCX, text-based PDF | Max 5 MB; no OCR |
| Authentication | JWT + seeded accounts; PM and TEAM_LEADER | No self-registration |
| Frontend | Keep the existing vanilla JS frontend and connect it to the API | ADR-004 |
| Deployment | Docker Compose: db, api, worker (+ frontend in M9) | Single machine |
| LLM budget | Per-job token limit + daily USD limit | Env variables |

## Non-Negotiable Architecture Rules

1. The database is the source of truth. Graph state is execution progress only.
2. Agents only propose changes (`save_draft_*`). Approval uses PM-only endpoints.
3. Every extracted item has `source_id`, `quote`, `run_id`, `created_by_agent`, `status`, `confidence`.
4. Quotes are verified programmatically; a quote not found verbatim is `unverified` with lower confidence.
5. The Global Supervisor never modifies project data; it only writes recommendations.
6. Every applied plan change requires PM approval, creates a new `plan_version` and an `audit_log` entry.

## Out of Scope

OCR; meeting recording/transcription; automatic external messages; one independent agent per project; automatic resource optimization; full multi-tenant; email notifications.

## Architecture Decision Records

### ADR-002: Deferring Full Project Access Control & Audit Triggers to M2
- **Status:** Approved
- **Decision:** Defer detailed RBAC and audit writes to M2.
- **Consequence:** Every M2 create/update endpoint writes `audit_log` in the same transaction; project-scoped endpoints use `require_project_access`; both are covered by tests.

### ADR-003: Single Alembic head and isolated test database
- **Status:** Approved
- **Decision:** One linear migration chain, enforced by `test_alembic_has_single_head`. Tests run only against `nexus_test` (refuses any database not ending in `_test`), built from the real migrations and cleaned after each test.

### ADR-004: Frontend continues with the existing vanilla JS
- **Status:** Proposed by engineering, needs team confirmation
- **Context:** The prototype was localStorage-only; M2 connected it to the API (login, projects, sources, team, CSV import). No framework was needed for M2 screens.
- **Decision:** Keep vanilla JS (`frontend/`), no React migration. PostgreSQL is the only source of truth; `sessionStorage` holds only the JWT.
- **Revisit:** if M7/M8 screens (diff review, portfolio) become unmanageable, decide within half a day.

### ADR-005: Source ingestion
- **Status:** Approved
- Only extracted text is stored (`sources.content_text`); uploaded files are not kept. Limit 5 MB; unsupported, corrupt, encrypted or empty/scanned PDFs are rejected with 415/413/422. Team CSV import is all-or-nothing and reports errors per line.

### ADR-006: LLM provider is Groq through one entry point
- **Status:** Approved by the team lead
- **Context:** The blueprint proposes Anthropic via langchain-anthropic but isolates the provider behind `llm/factory.py`.
- **Decision:** Use Groq. Agents call only `app/llm/runner.py::run_structured`.
- **Consequence:** LangChain middleware and the Langfuse LangChain callback do not apply to this client; `run_structured` does budget, usage/cost, `agent_runs`, audit and Langfuse tracing explicitly.

### ADR-007: agent_runs, budgets and pricing
- Column names follow `input_tokens/output_tokens/total_tokens/trace_id` (blueprint: tokens_in/tokens_out/langfuse_trace_id).
- Money is Decimal/Numeric(14,8). A run reserves worst-case cost/tokens under an advisory lock before the call.
- Prices come only from `LLM_PRICING_JSON`. No price configured => cost 0 and flagged; the per-job token limit still applies.
- Langfuse is best-effort: metadata whitelist, secrets redacted.

### ADR-008: LangGraph spike contract
- Nodes: draft_node -> await_pm_node (interrupt) -> finalize_node. Thread id `project:{id}`.
- Decision = `{decision, comment, decided_by}`; `decided_by` always comes from the JWT, never from the request body; comment required for rejected/modified.
- The business write is idempotent per run_id. API: `POST /projects/{id}/graph/start|resume` (PM only), `GET /projects/{id}/graph/status`.

### ADR-009: Evals
- `evals/metrics.py` is deterministic (quote matching, no LLM judge). DeepEval GEval is added per agent; judge model is `MODEL_JUDGE_EVAL`. `make eval` runs DeepEval and costs money.

## Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-29 | Remove duplicate initial migration | Two root heads |
| 2026-10-30 | LLM provider = Groq | Team choice; isolated behind factory |
| 2026-10-30 | Graph decider taken from JWT | Prevent PM impersonation |
