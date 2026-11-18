# NEXUS: Handoff

## State
M0-M3 code is on branch fix/m0-m3-closure. A milestone is DONE only with evidence recorded below.
M4 (Agent 1) and M5 (Agent 2: stories & tasks) are on branch feature/m5-agent2-stories-tasks.
M5.1 (Agent 2 professional edition) is on branch feature/agent2-v2-professional (stacked on M5).
M6.1 (Agent 3 platform integration) is on branch feature/m6-agent3-integration (stacked on M5.1).
M4.1 (Agent 1 smart review: modules/priorities/questions/bulk/gaps) is on branch feature/agent1-smart-review (stacked on M6.1).
M7 (PM command center: portfolio/resources/risks + assistant chat) is on branch feature/m7-pm-command-center (stacked on feature/agent1-smart-review).
M8 (frontend polish: dark mode + Arabic/RTL + portfolio charts) is on branch feature/m8-polish (stacked on M7).
M9 (delivery tracking + Gantt) is on branch feature/m9-gantt-delivery (stacked on feature/m8-polish).
M10 (PDF export) is on branch feature/m10-pdf-export (stacked on feature/m9-gantt-delivery).

## M10 evidence (PDF export)
- [x] `GET /projects/{pid}/report.pdf` PM-only; reportlab + bundled Amiri (Regular+Bold, Latin+Arabic coverage verified via cmap); arabic_reshaper + python-bidi for shaping/order — no LLM anywhere
- [x] 4 new tests (`tests/test_report_pdf.py`): real %PDF bytes + content-type + download filename + pypdf page/text checks ("Project status report", "Tasks"), Arabic project name doesn't break the PDF, TL 403, unknown project 404 — **4/4 pass**
- [x] Full backend suite in the container: **463 passed** (459 + 4 report tests)
- [x] Content mirrors the app: header + summary (health, hours-weighted progress, window, next deadline, task counts, pending drafts, overdue), drawn Gantt (same colors/logic as M9 UI, today line), full tasks table, project risks with suggested actions, governance footer
- [x] Frontend "⬇ Export PDF" on the project header (PM only): fetch → blob → download (JWT in the header, so a plain link would not authenticate); verified live on project 14 (36.8KB, starts with %PDF-); Arabic notice strings included
- [x] Small robustness fix found during verification: project header now guards `p.sources` before the reload completes

## M9 evidence (delivery tracking + Gantt)
- [x] Backend: `PATCH /projects/{pid}/tasks/{tid}/progress` PM-only; `todo → 0%`, `done → 100%`, `in_progress` keeps/sets the percent; draft task 409, unknown 404, non-PM 403; audit `task.progress` with before/after — **6/6 new tests pass** (`tests/test_delivery_progress.py`); service `app/services/delivery.py`
- [x] Full backend suite in the container: **459 passed** (453 baseline + 6 delivery tests)
- [x] No migration needed: progress_pct/status/start/end already existed on tasks
- [x] Gantt on the Schedule tab (no libraries): rows for approved+dated tasks over the project window; done → green, critical → orange, overdue → dashed red outline; red "today" marker; per-bar progress fill + % label; two-line row layout keeps it usable narrow and in RTL (direction-aware `margin-inline-start`/`inset-inline-*`)
- [x] Per-row delivery controls (Todo / ▶ Start / Done + % input) call the PATCH and re-render; summary chips: hours-weighted overall %, done/total, in progress, overdue
- [x] Verified live in the browser (project 14 "مدرسه", 11 dated approved tasks): Start → In progress 0%, % → 45% (chips: 5% overall), Done → 100% green (chips: 12% overall, 1/11); Arabic RTL Gantt fully translated ("مخطط جانت — تتبع التنفيذ", "مكتملة", dates "17 نوفمبر 2026"); dark mode colors follow the CSS variables; zero console errors
- [x] Portfolio auto-sync with zero extra code: after the Done click the command-center card for "مدرسه" showed 12% · 1/11 tasks done; audit rows 341-343 show the full before/after chain (todo 0 → in_progress 45 → done 100)

## M8 evidence (frontend polish)
- [x] `node --check` passes; verified in a real browser: both toggles persist across a reload (`nx-theme`, `nx-lang` in localStorage)
- [x] Dark mode: variable-only override (`[data-theme="dark"]`); sidebar/topbar/buttons all legible; no console errors
- [x] Arabic/RTL: `dir=rtl` flips the shell; dictionary + regex DOM translation covers chrome, portfolio, tabs, empty states and dynamic strings (counts, health reasons, "next deadline"); dates render in the Arabic locale (`30 نوفمبر 2026`); un-translated strings degrade to English, never break
- [x] Charts (no libraries): health donut + progress bars (Projects), utilization/capacity bars (Resources), risks-by-type bars + severity donut (Risks)
- [x] Live Arabic chat e2e in dark RTL mode: Arabic question → Arabic answer naming the draft backlogs (مدرسة 17, مطعم 13, Clinic Booking 1) with Arabic suggestion chips

## M7 evidence (PM command center)
- [x] full backend suite in the container: **453 passed** (441 + 12 new portfolio tests)
- [x] `alembic current` = `a7c3e1d9f2b8` (head); `agent_runs.project_id` nullable for portfolio-level runs
- [x] Deterministic endpoints (no LLM): `/portfolio/overview` (health rules: overdue→red, drafts/plan-end→yellow, hours-weighted progress), `/portfolio/resources` (utilization %, name-matched cross-project conflicts), `/portfolio/risks` (schedule/aging-drafts/scope-gaps/overallocation/conflicts, severity-ordered)
- [x] `POST /portfolio/chat` PM-only, through `run_structured`: live Groq e2e answered from the snapshot with 3 sanitised suggestions (`open_project` → projects 8/10/11); `agent_runs` row #28 `project_id=NULL, status=completed`, audit `portfolio.assistant_chat` written
- [x] Chat sanitisation tested: invented project ids dropped, unknown actions dropped, history capped at last 12, 422 without a user message
- [x] Frontend: Portfolio page = segmented command center (Projects | Resources | Risks | ✦ Assistant) with summary chips, health badges, progress bars, utilization bars, severity-grouped risks and a chat panel with clickable suggestion chips; `node --check` passes

## M5 evidence (Agent 2 - stories & tasks)
- [x] full backend suite in the container (Python 3.11): 284 passed
- [x] deterministic Golden Project B evals (`evals/tests`): 30 passed
- [x] `python -m evals.run_agent2 --golden project-b --mode oracle --strict` exits 0 (all thresholds pass)
- [x] Agent 2 only receives APPROVED requirements and never writes the DB directly (`save_draft_plan` is the single writer)
- [x] deterministic validation rejects cycles, dangling refs, self-dependencies and out-of-range/identical estimates
- [x] PM-only review endpoints + `GET /projects/{id}/planning/coverage`; frontend "Work plan" tab
- [x] `make eval-agent2` live run (Groq `openai/gpt-oss-120b`): all thresholds pass, saved as `evals/results/agent2-project-b-20261113T105446Z-5e7306c6.json`
      (story_coverage 1.0, story_recall 1.0, task_recall 0.9167, cycle_free/link_integrity true, total_hours_ratio 0.24)
- [x] `make eval-agent2-quality` live run: DeepEval "Story Quality" score 0.9 (threshold 0.7), PASSED

## M5.1 evidence (Agent 2 professional edition)
- [x] full backend suite in the container: 298 passed (13 new: self-correction loop, plan checks, context, soft-issue audit)
- [x] deterministic Golden Project B evals: 38 passed (8 new mutations for ordering / priority_first / critical_path / rationale)
- [x] migration `9d5c1e8a2b74` applied (`alembic current` = 9d5c1e8a2b74, head)
- [x] prompt v2 (`agent2_plan_v2.md`): INVEST stories, priority-first ordering, critical-path cap, per-task rationale
- [x] self-correction loop (`app/agents/plan_checks.py`): HARD issues fail the job with `PlanQualityError`; SOFT polish issues fed back (3 passes × 2 attempts) and survivors recorded in audit `plan_soft_issues`
- [x] context input: brief + transcript + PM scope-review comments reach the agent as wrapped untrusted blocks with char caps
- [x] richer fields persisted (`plan_order`, `is_critical`, `rationale`) and shown in the frontend (order chips, "Critical path" badge, "Why:" line)
- [x] `LLM_MAX_TOKENS_PER_JOB` 12000 → 80000 (worst case = 6 model calls; the old value rejected every real planning job)
- [x] live Groq run (`python -m evals.run_agent2 --golden project-b --strict`): all 14 thresholds pass, saved as `evals/results/agent2-project-b-20261114T200438Z-6afd6007.json`
      (ordering true, priority_first true, critical_path true — 6/30 critical, rationale true, total_hours_ratio 0.03)

## M6.1 evidence (Agent 3 platform integration)
- [x] full backend suite in the container: 423 passed (8 new agent3-job tests: draft-only, PM approval dating, supersede, gates, 403)
- [x] deterministic scheduling evals: 4 passed (`evals/tests/test_scheduling_pipeline.py` on Golden Project B, no LLM)
- [x] migration `c2a9e1f47b35` applied (`alembic current` = c2a9e1f47b35, head); chained on `9d5c1e8a2b74` (single head preserved)
- [x] job handler `agent3.schedule` registered; every LLM call goes through `run_structured` (`BudgetedLLM` adapter) — audit `agent3.scheduling_completed`, agent_runs + Langfuse wired
- [x] schedule_drafts persistence: agents write ONLY drafts; `tasks.start/end` are written exclusively by PM approval, and only on PM-approved tasks
- [x] PM-only API: `GET /projects/{id}/scheduling` + `POST .../scheduling/{draft_id}/decision` (comment required on reject, 409 on re-decide/older draft); frontend "Schedule" tab
- [x] fixed cross-project dependency leak in `_task_inputs` (dependencies filtered to this project's task ids)
- [x] live e2e on the dev stack: agent3 job done (explanation source = llm, run_ids recorded), draft #2 created (16 tasks, critical path 1→2→5→7→16, end 2026-11-24), 0 dates before approval, 16/16 dated after, re-decide 409, leader 403

## M4.1 evidence (Agent 1 smart review)
- [x] full backend suite in the container: 433 passed (10 new: v3 schema fields, persistence, bulk decision, gaps, audit snapshots)
- [x] migration `d4b7e2c95a61` applied (`alembic current` = d4b7e2c95a61, head); chained on `c2a9e1f47b35` (single head preserved)
- [x] prompt v3 (`agent1_extract_v3.md`, v2 kept): module + priority suggestion + clarify question for implied/vague items; decision items never carry a priority
- [x] quote verification and injection guards untouched — anti-hallucination guarantees identical to M4
- [x] bulk PM decision `POST /projects/{id}/requirements/decision`: one transaction, per-requirement audit rows with `bulk: true`, all-or-nothing 404 on unknown/foreign ids, comment required on reject
- [x] coverage gaps `GET /projects/{id}/requirements/gaps`: 11 standard areas, EN+AR keywords, rejected requirements excluded
- [x] frontend: requirements grouped by module with per-module Approve/Reject all, ❓ clarify questions shown with an answer hint, coverage-gap chips in the Scope tab
- [x] live e2e on the dev stack: agent1 v3 job done → 5/5 requirements with modules (Authentication | Bookings | Payments | Records | Scheduling) + priorities; gaps flagged 8 genuinely unmentioned areas; bulk approved a module; bulk reject without comment refused
- [x] coverage gaps upgraded to LLM-judged (ADR-014): prompt `agent1_gaps_v1.md` via `run_structured`, verdict sanitised by code (invented areas dropped, hallucinated ids filtered), stored per project in `coverage_reports` (migration `e5f8a3b7c6d4`, chained, single head), GET merges fresh keyword rows with the stored verdict and never re-runs the LLM
- [x] backend suite: 436 passed (13 gap tests: sanitising, persistence, endpoints, roles); migration applied (`alembic current` = e5f8a3b7c6d4)
- [x] live Groq e2e on the "مدرسة" project (6 requirements): keyword layer had "Performance & scale ✓" (from "upload") and "Data & reporting ✗"; the LLM verdict corrected both — reporting COVERED (reqs 33/34), performance/missing with a ready client question; all 11 areas `source: llm`, run_id stored, GET returns the saved report
- [x] one-click accept-all chain (ADR-015): bulk-approve reqs → approve scope → LLM gaps → Agent 2 → bulk-approve plan drafts → Agent 3 → lands on Schedule; plan-level bulk endpoints for stories/tasks; "Approve all drafts" button on Work plan; final schedule approval stays manual
- [x] story-reject cascade: rejecting a story rejects its draft child tasks in the same transaction (per-task Decision + audit `cascade: story:<id>`; decided tasks untouched)
- [x] backend suite: 440 passed (+4: cascade, bulk stories, bulk tasks + guards); live chain e2e ran the full flow twice (4 reqs → 3 approved/1 rejected → scope → gaps run_id stored → Agent 2 (3 stories, 9 tasks) → bulk approved → Agent 3 draft created)

## Evidence log (fill in with real output)
- [ ] clean clone -> make up -> /health ok -> alembic current = 5b7e1c9a2d84 (head)
- [ ] make test (container, Python 3.11): all passed
- [ ] kill-worker recovery: job done with attempts=2
- [ ] graph survives `docker compose restart api` (waiting_for_pm before and after)
- [ ] Langfuse trace with project_id (+ cost if pricing configured)
- [ ] `make eval` ran one DeepEval test
- [ ] main protected, board M0-M9 created, decisions.md reviewed by everyone

## Rules
- Never commit .env or keys. Never use `docker compose down -v` on the dev stack.
- Run tests in the container (Python 3.11), not in the host Python 3.14.
