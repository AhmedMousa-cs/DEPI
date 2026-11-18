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
- Agent 2 adds `evals/planning_metrics.py` (coverage/linkage/cycle/estimate checks, no LLM judge) plus Golden Project B in `evals/golden/` and a DeepEval "Story Quality" check.

### ADR-010: Agent 2 output is draft-only; review_status is separate from execution status
- **Status:** Approved
- Agent 2 receives only APPROVED requirements and returns local refs (`S1`/`T1`); ids are assigned by `save_draft_plan` and the agent never writes the DB directly.
- `user_stories.review_status` / `tasks.review_status` (`draft|approved|rejected`) are independent of `tasks.status` (`todo|in_progress|blocked|done`): a PM can approve a story while its task is still `todo`.
- Re-running Agent 2 keeps PM-decided rows (a story and its tasks) and matches the surviving tasks positionally per story, so a PM-edited title never creates duplicates.
- `PlanningOutput` (Pydantic) rejects duplicate refs, unknown story/dependency refs, self-dependencies and cycles before persistence.
- `GET /projects/{id}/planning/coverage` is the machine-readable coverage report; it is `clean` only when every approved requirement has an approved story and there are no cycles.

### ADR-011: Agent 2 professional edition (prompt v2, self-correction, context, richer fields)
- **Status:** Approved
- **Context:** M5 shipped a single-pass Agent 2. The PM asked for a more professional agent ("أكثر احترافية") across four directions: a smarter prompt, self-correction, more input context and richer output — while agents stay draft-only and PM-only for ids/statuses/approval (ADR-010 still holds).
- **Decision:**
  - Prompt `agent2_plan_v2.md` (v1 kept): INVEST stories ("so that" benefit), **priority-first ordering** (every high-priority requirement before medium/low), critical-path cap (typically 2–4 tasks, never more than one third, never "first task of every story"), and a concrete rationale per task.
  - Self-correction loop: `app/agents/plan_checks.py` runs deterministic checks on the agent's own output. **HARD** issues (requirement without story, story without task, duplicates, estimate out of range, >8 tasks/story, dependency cycles) that survive → `PlanQualityError` fails the job, nothing reaches the DB or the PM. **SOFT** polish issues (identical estimates, missing order, dependency-order violation, priority-first violation, critical-path problems, missing rationale) are fed back to the model for up to 3 passes × 2 attempts; survivors are recorded in the audit as `plan_soft_issues` and never block the plan.
  - Context input: project brief, meeting transcript and the PM's scope-review comments (`requirements.pm_comment`) reach the agent as size-capped, wrapped untrusted blocks (`source_id=0`), scanned by the injection guard like any other input.
  - Richer fields: `user_stories.plan_order`, `tasks.plan_order` + `tasks.rationale` persisted (migration `9d5c1e8a2b74`); `tasks.is_critical` now comes from the agent; schema enforces order uniqueness (globally for stories, per story for tasks) and rationale length 5..300. Audit snapshots include the new fields; the frontend shows order chips, a "Critical path" badge and the "Why:" rationale.
  - Budget: `LLM_MAX_TOKENS_PER_JOB` 12000 → 80000 (worst case is 6 model calls, reserved pessimistically at 2 chars/token — 12000 rejected every real planning job).
  - Golden Project B bumped to version 2 with four new thresholds (`ordering`, `priority_first`, `critical_path`, `rationale`, all must be true); live Groq run passes 14/14 (`evals/results/agent2-project-b-20261114T200438Z-6afd6007.json`, total_hours_ratio 0.03).
- **Consequence:** a planning job may take up to ~3× longer (correction passes), but a plan that cannot be executed never reaches review, and plans now carry an explicit schedule, backbone and rationale the PM can judge.

### ADR-012: Agent 3 platform integration (M6.1) — draft schedule table, PM approval writes dates
- **Status:** Approved
- **Context:** M6 merged a professional scheduling *library* (CPM, conflicts, capacity, prompts, ~117 tests) with no job handler, API, frontend, persistence or evals — `run_agent3` was unreachable from the app and its LLM calls bypassed `run_structured`.
- **Decision:**
  - New `schedule_drafts` table (migration `c2a9e1f47b35`, chained on `9d5c1e8a2b74` to keep a single Alembic head): `payload` JSON = the full `Agent3Output` dump, plus `project_start/project_end/total_duration_days/conflicts_count/run_ids/review_status/pm_comment/created_by_agent`. Agents write ONLY drafts.
  - Job handler `@register("agent3.schedule")`: refuses when the project has no tasks ("run Agent 2 first") or none is PM-approved — the real plan gate is `tasks.review_status == "approved"`, not a project status (nothing ever sets `plan_review`). Every LLM call goes through a `BudgetedLLM` adapter wrapping `run_structured` (budget, agent_runs, audit, Langfuse); in the normal flow only the explanation is LLM worded, with a deterministic template fallback. Audit action `agent3.scheduling_completed`.
  - PM approval (`POST /projects/{id}/scheduling/{draft_id}/decision`) is the ONLY way `tasks.start/end` are written: exclusively on tasks with `review_status == "approved"` (drafts/rejected stay undated). Note: `locked_by_pm` is set by ANY PM decision and only stops agents from overwriting PM-reviewed content on re-runs — it does not block schedule dating. Rejecting requires a comment. Re-deciding or deciding an older draft → 409; re-runs supersede previous drafts (approved ones stay as history).
  - API is PM-only: `GET /projects/{id}/scheduling` (latest non-superseded draft, 404 until Agent 3 runs) + the decision endpoint. Frontend "Schedule" tab: run button (enabled once plan tasks are approved), job polling, summary/critical path/per-task dates+float/conflicts/capacity/estimates, LLM-vs-template wording badge, approve/reject flow.
  - Fixed cross-project bug: task dependency loading is filtered to this project's task ids. Evals: 4 deterministic scheduling checks on Golden Project B (`evals/tests/test_scheduling_pipeline.py`).
- **Consequence:** the PM sees the computed schedule and can iterate: reject → fix the plan → re-run → approve; dates only ever land on PM-approved tasks through one audited decision.

### ADR-013: Agent 1 smart review (M4.1) — modules, priorities, clarify questions, bulk decisions, coverage gaps
- **Status:** Approved
- **Context:** M4 extracts one requirement per statement with a verified exact quote (ADR-005-era guard kept). Reviewing 30–50 items one by one is the PM's real pain, and nothing tells the PM what the client never mentioned.
- **Decision:**
  - Prompt `agent1_extract_v3.md` (v2 kept): every non-decision requirement also gets a short feature-area `module` (reused labels, consistency over cleverness), a `priority` suggestion (high/medium/low; never on decision items) and, for implied/vague items (confidence ≤ 0.8), ONE `clarify_question` for the PM. Schema validates lengths and collapses module whitespace; the deterministic quote-verification and injection guards are untouched.
  - Persistence: `requirements.module` + `requirements.clarify_question` (migration `d4b7e2c95a61`, chained on `c2a9e1f47b35` — single head). The PM's answer (via edit/decision comment) lands in `pm_comment`, which Agent 2 already reads as context.
  - Bulk review: `POST /projects/{id}/requirements/decision` `{ids, decision, comment}` applies ONE decision to many rows in a single transaction — one Decision row + one audit row (`bulk: true`) per requirement, 404 (nothing written) if any id is missing or belongs to another project, comment required on reject. The frontend groups requirements by module with per-module "Approve all / Reject all".
  - Coverage gaps: `GET /projects/{id}/requirements/gaps` — deterministic (no LLM) match of ~11 standard product areas (EN + AR keywords) against the live (non-rejected) requirements' text + quote; missing areas are review-meeting questions, not facts. Shown as chips in the Scope tab.
- **Consequence:** the PM reviews ~5 groups instead of N items, sees what was never said, and low-confidence items carry their own question — with the same anti-hallucination guarantees as before.

### ADR-014: Coverage gaps are LLM-judged, with the deterministic keyword check as fallback (M4.1)
- **Status:** Approved
- **Context:** The keyword-only gap report produced false verdicts that a human would never make: a school app showed "Performance & scale ✓" because "upload grades" contains "load", and missed "Data & reporting" because the client said "grades and attendance records" without the word "report". Keyword matching judges words; coverage is about meaning.
- **Decision:**
  - `POST /projects/{id}/requirements/gaps/analyze` (PM-only, 409 without requirements) runs an LLM gap analysis through `run_structured` (budget, `agent_runs`, audit `agent1.gaps_analyzed`, Langfuse) with prompt `agent1_gaps_v1.md`. The payload carries the 11 fixed areas (each with a one-line brief) + requirement texts as untrusted DATA; the prompt judges by meaning, forbids inventing areas/ids, and asks for one concrete PM question per missing area.
  - The LLM verdict is sanitised by CODE before storage: invented area names are dropped, `matched_ids` filtered to real requirement ids, and every area keeps a deterministic keyword row as fallback (`source: "llm" | "keywords"` per area).
  - Reports persist per project in `coverage_reports` (migration `e5f8a3b7c6d4`, chained on `d4b7e2c95a61` — single head): data + `meta` (run_id, prompt_version, analyzed_requirements). `GET /requirements/gaps` merges fresh keyword rows with the stored LLM verdicts (recomputing counts) and returns the `llm` block — a page load NEVER re-runs the LLM; only the explicit button does. Analysis is informational: no approval flow, no writes.
- **Consequence:** meaning beats words (the school app now correctly reports performance/security/etc. as unmentioned and reporting as covered), each missing area ships with a ready client-meeting question, and the keyword layer still answers instantly with zero cost before the PM chooses to analyze.

### ADR-015: One-click accept-all with an automatic agent chain (M4.1 UX)
- **Status:** Approved
- **Context:** The PM demoing the platform asked for one button that accepts everything and lets Agents 2 and 3 run by themselves, with the manual buttons kept as an alternative.
- **Decision:**
  - "✓ Accept all & run agents" on the Scope tab performs, in order: bulk-approve every draft requirement → approve the scope → LLM coverage analysis → Agent 2 job → bulk-approve all draft stories and tasks → Agent 3 job, then lands on the Schedule tab. Each step is a normal PM-authorised API call (bulk decisions remain audited per item, `bulk: true`); the chain stops and reports exactly where if any step fails. The final schedule approval (writing dates) stays deliberately manual.
  - Plan-level bulk endpoints: `POST /projects/{id}/stories/decision` and `POST /projects/{id}/tasks/decision` (same contract as the requirements bulk: one transaction, all-or-nothing 404, comment required on reject, per-item Decision + audit rows). "Approve all drafts" button added on the Work plan tab for manual use.
  - Rejecting a story (single or bulk) now CASCADES to its still-draft child tasks in the same transaction — each child gets its own Decision + audit row (`cascade: story:<id>`); tasks the PM already decided on are never touched. This fixes the UX bug where a rejected story still showed approvable draft tasks.
- **Consequence:** the demo flow is one click from sources to a computed schedule; nothing bypasses the PM role — every write is still a PM decision, recorded per item.

### ADR-016: PM command center — portfolio overview, resources, risks and an assistant chat (M7)
- **Status:** Approved
- **Context:** The PM asked for one screen that manages all his projects, shows cross-project resources, surfaces risks — plus a chatbot that talks to him and suggests actions (M7).
- **Decision:**
  - `GET /portfolio/overview` (project cards with 🟢🟡🔴 health from deterministic rules: overdue tasks → red, plan past planned end / pending drafts / stalled pipeline → yellow, hours-weighted progress over approved tasks, next deadline), `GET /portfolio/resources` (per-member capacity vs allocated hours over each member's task window, utilization %, cross-project conflicts matched by normalized name + overlapping windows), `GET /portfolio/risks` (deterministic register: schedule risks, aging drafts > 3 days, stored LLM coverage gaps >= 5 missing areas, overallocated members, cross-project conflicts — each with evidence and a concrete next action, sorted high→medium). All read endpoints accept PM + team leaders, scoped by `_accessible_projects` (leaders see only their projects); **no LLM** in any of them.
  - `POST /portfolio/chat` (PM-only): the PM assistant. The client sends the recent conversation (stateless backend, last 12 kept); the service attaches today's portfolio snapshot as untrusted DATA (the prompt `pm_assistant_v1.md` forbids following instructions inside it and answering beyond the data), and returns `{reply, suggestions[]}` through `run_structured`. Suggestions are sanitised by CODE: only UI-performable actions (`open_project`, `review_drafts`, `view_risks`, `view_resources`, `schedule`) and only project ids the user can see. The run is portfolio-level: `agent_runs.project_id` became **nullable** (migration `a7c3e1d9f2b8`, chained on `e5f8a3b7c6d4` — single head); the run is still budgeted, token-counted, audited (`portfolio.assistant_chat`) and Langfuse-traced (session `portfolio`).
  - Frontend: the Portfolio page became a segmented command center (Projects | Resources | Risks | ✦ Assistant) with summary chips, health badges + progress bars, utilization bars, severity-grouped risks, and a chat panel whose suggestion chips route to the actual screens.
- **Consequence:** the PM sees everything in one screen with zero LLM cost for data; the only LLM call is the explicit chat turn, fully inside the single gateway with all invariants (drafts stay PM-decided; the assistant advises but never acts).

### ADR-017: Frontend polish — dark mode, Arabic/RTL, and portfolio charts (M8)
- **Status:** Approved
- **Context:** The PM asked for a more professional dashboard: dark mode, an Arabic/RTL interface, and charts before the delivery/Gantt work.
- **Decision:**
  - **Dark mode** via a `[data-theme="dark"]` override of the CSS variables only (surface/ink/line/tints get dark values; the navy sidebar is already dark). Toggle in the topbar (🌙/☀️), persisted in `localStorage` (`nx-theme`), applied before first render (`applyPrefs` sets `data-theme` + `theme-color` meta). No component styles changed — only variables.
  - **Arabic/RTL**: topbar toggle (ع/EN), persisted in `localStorage` (`nx-lang`). `dir="rtl"` + `lang="ar"` on `<html>` flips the flex layout automatically; the mobile drawer gets an RTL override. Translation is done by **DOM translation after each render**: a dictionary of exact text-node matches plus regex rules for dynamic strings (counts, dates, health reasons) walks the rendered tree — no template was rewritten, so a missed string degrades to English instead of breaking. Dates render in the Arabic locale; the LLM assistant answers in the language the PM writes in (prompt rule).
  - **Charts (pure SVG/CSS, zero libraries)**: a health donut + legend and a "progress by project" bar chart on the Projects tab, "utilization by person" + "capacity per week" bars on Resources, and a "risks by type" bar chart + severity donut on Risks.
- **Consequence:** the same UI presents professionally in both themes and both languages, and the portfolio reads at a glance — with no new dependency and no risk to the deterministic backend.

### ADR-018: Delivery tracking — the PM records execution; Gantt on the Schedule tab (M9)
- **Status:** Approved
- **Context:** The schedule produced real dates but nothing recorded what actually happened. The PM asked for a Gantt view plus delivery tracking (list position #1 on the roadmap). Teammate Agent 4 work (plan approval gate, plan_versions) lives on separate branches — no feature overlap.
- **Decision:**
  - **One PM-only endpoint**: `PATCH /projects/{project_id}/tasks/{task_id}/progress` (planning router) validates a `TaskProgressUpdate` {status: todo|in_progress|done, progress_pct 0-100 optional}. Rules: `todo → 0%`, `done → 100%`, `in_progress` keeps the current percent unless one is sent. Draft or rejected tasks get 409; unknown task 404; non-PM 403. Service: `app/services/delivery.py` (`update_task_progress`); audit action `task.progress` with before/after.
  - **Gantt on the Schedule tab** (frontend, no libraries): rows for **approved, dated tasks only**, positioned by the project window (min start → max end). Bar colors: done → green, critical-path → orange, else blue; overdue (end < today, not done) gets a dashed red outline and row accent; a red "today" marker spans the tracks; each bar shows a white progress fill + percent label. Row layout is two-line (label + controls above, full-width track below) so it holds up at narrow widths and in RTL (`margin-inline-start` / `inset-inline-*` are direction-aware).
  - **Controls per row**: Todo / ▶ Start / Done buttons plus a percent input with a "%" button (sets in_progress with the given percent); every action PATCHes and re-renders. Summary chips: hours-weighted overall progress, done/total, in progress, overdue.
  - **Zero extra wiring for the portfolio**: the command center's hours-weighted progress already reads task status/progress_pct, so delivery updates flow into the PM's portfolio automatically (verified live: task → 100% ⇒ project card 12%).
- **Consequence:** the loop plan → approve → execute → see progress closes with one audited endpoint; agents still never write execution progress, and no new table or migration was needed.

### ADR-019: PDF export — deterministic reportlab report (M10)
- **Status:** Approved
- **Context:** The PM asked to export the project as a PDF (roadmap #2). The report must match what the UI shows, be printable for stakeholders, and handle Arabic project names.
- **Decision:**
  - **Server-side, dependency-light**: `GET /projects/{project_id}/report.pdf` (PM-only) builds the bytes with **reportlab** — no browser printing tricks, no LLM. `arabic-reshaper` + `python-bidi` fix shaping/order (reportlab does no bidi), and the bundled **Amiri** TTFs (`app/assets/fonts/`, Regular + Bold, ~830KB committed) cover Latin *and* Arabic in one font — verified via the TTF cmap before choosing it (Noto Naskh Arabic lacks Latin letters).
  - **Content mirrors the app's own numbers**: header (project, status, generated timestamp), summary table reusing `project_card` (health + reasons, hours-weighted progress, execution window, next deadline, task counts, pending drafts, overdue), a drawn **Gantt** (same colors/logic as the M9 UI: green done, orange critical, red overdue dot + today line), a full tasks table (assignee, dates, status, %, critical flag) and the project's risks from the M7 register with suggested actions.
  - **Frontend**: "⬇ Export PDF" button on the project header; the download is a `fetch → blob → object URL` (the JWT goes in the Authorization header, so a plain link would not authenticate).
  - Deterministic footer states the governance rule: agents never write progress, dates or decisions.
- **Consequence:** stakeholders get a shareable status report that always matches the database; no new service or migration, and Arabic content renders correctly.

## Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-29 | Remove duplicate initial migration | Two root heads |
| 2026-10-30 | LLM provider = Groq | Team choice; isolated behind factory |
| 2026-10-30 | Graph decider taken from JWT | Prevent PM impersonation |
| 2026-11-13 | Agent 2 draft-only, separate review_status (ADR-010) | Keep PM decisions independent of task execution |
| 2026-11-14 | Agent 2 professional edition: prompt v2, self-correction, context, richer fields (ADR-011) | PM requested "أكثر احترافية"; live Groq eval passes 14/14 thresholds |
| 2026-11-15 | Agent 3 platform integration: schedule_drafts, PM approval writes dates (ADR-012) | M6 was an unintegrated library; live e2e proves draft → approve → 16/16 dated |
| 2026-11-15 | Agent 1 smart review: modules, priorities, clarify questions, bulk decisions, coverage gaps (ADR-013) | PM asked for something more useful than item-by-item review; live e2e passes |
| 2026-11-17 | Coverage gaps upgraded to LLM-judged with keyword fallback (ADR-014) | Keyword false positives ("upload" → Performance ✓); live Groq e2e judges meaning correctly |
| 2026-11-17 | One-click accept-all + automatic agent chain; story-reject cascades to child tasks (ADR-015) | PM asked for accept-everything + auto-run Agents 2/3 with manual buttons kept; live chain e2e passes |
| 2026-11-17 | PM command center: portfolio overview/resources/risks + PM assistant chat (ADR-016) | PM asked to manage all projects, resources and risks in one screen with a chatbot that suggests actions; live Groq chat e2e passes |
| 2026-11-17 | Frontend polish: dark mode + Arabic/RTL (DOM translation) + SVG portfolio charts (ADR-017) | PM asked for a more professional dashboard; both toggles persist, charts are dependency-free |
| 2026-11-18 | Delivery tracking: PM-only PATCH task progress + Gantt on Schedule tab (ADR-018) | PM asked for a Gantt + execution tracking; todo→0/done→100 rules, audited, portfolio auto-syncs |
| 2026-11-18 | PDF export: server-side reportlab report with bundled Amiri font (ADR-019) | PM asked for PDF export; report mirrors the DB (no LLM), Arabic shaping handled |
