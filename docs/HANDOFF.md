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
M11 (change management) is on branch feature/m11-change-management (stacked on feature/m10-pdf-export); merged to main via PR #53 together with #52 (M9+M10).
M6-B (plan approval gate + Agent 4: plan_versions/risks, LangGraph review, PM-only gate) was merged to main via PR #47 (teammate Mahmoud) on top of M6.1 as a hybrid Agent 3 job; the PM Plan review screen, Golden Project C and the Agent 4 eval evidence follow on branch feature/m6-plan-review-ui.

## M6-B evidence (plan approval gate + Agent 4)
- [x] Merged main (M4.1/M6.1/M7/M8/M9/M10/M11) into `feature/m6-agent4-approval-gate`; conflicts resolved in models.py (PlanVersion+Risk kept alongside CoverageReport+ScheduleDraft), main.py (plan+scheduling routers), job_handlers.py (agent4_job), requirements.txt (langchain + reportlab union), decisions.md (teammate ADR renumbered 011 -> 018 -> **ADR-021**, M9 keeps ADR-018)
- [x] Hybrid Agent 3 contract: one `agent3.schedule` job persists BOTH the M6.1 `schedule_draft` (PM approval writes dates, unchanged) and a draft `plan_version` (feeds Agent 4 + plan gate), then queues `agent4.review`; agents still never write task rows
- [x] plan_versions/risks migration `9d4e6a8c1b25` re-chained on the single head (`d9e4f7b2c5a3` after M11 merged) - `alembic heads` = one head
- [x] Full backend suite in the container on the merged branch: **577 passed** (previous 469 + 18 Agent 4 job tests + M9/M10/M11 suites); single-run policy respected
- [x] Frontend **Plan review** tab (PM only): version chips with status, snapshot KPIs (start/end, duration, tasks, critical path, conflicts, open risks), Agent 4 verdict/findings card, approval warnings, diff vs the previous and vs the approved version, per-version risks, planned tasks, and the gate (open → waiting → approve/reject with an acknowledge-warnings checkbox); version diff renders deterministic change rows; ~75 Arabic strings added
- [x] Live browser e2e on project 8 "Clinic Booking" (pm@nexus.test): `POST /plan/generate` → job 25 (`version:1;tasks:2;draft:8;conflicts:0`), Agent 4 review job 26 → verdict `needs_attention` + rule risk `unassigned_tasks`, gate "Waiting for your decision", approve modal → "Plan v1 approved — 2 task date(s) written", project → `Active`, gate `completed`, risk → `open`; DB confirms `active | version 1 | approved | 2 dated`; zero console errors
- [x] Golden Project C (`evals/golden/project-c-{brief.md,transcript.txt,expected.json}`): FixMate plan with a known 15-working-day CPM (end 2026-12-04, critical path T1→T2→T4→T6→T8→T10), one intended overallocation (Sara, T4+T5, ratio 2.0) and target end 2026-12-01; 4 deterministic tests (`evals/tests/test_golden_project_c.py`) cover durations, dates/critical path, the single conflict and the exact Agent 4 rule set + `needs_attention` floor - **4/4 pass**
- [x] Agent 4 live eval (`python -m evals.run_agent4`, Groq gpt-oss-120b, DeepEval ToolCorrectness): **3/3 scenarios pass** (`double_booked` 0.857, `deadline_blown` 1.0, `clean_plan` 1.0), every read returned the snapshot data, risks saved and `review_source = agent4`; saved to `evals/results/agent4_tool_correctness.json`. The script now retries Groq TPM 429s (free tier is 8k tokens/minute)
- [x] Two loop-budget bugs found by the live eval and fixed: the summary trigger (3500) compacted the context after five reads and restarted the review, and the graph step limit (40) cut reviews off mid-run (every turn costs several middleware steps) - now `AGENT4_SUMMARY_TRIGGER_TOKENS=60000`, `KEEP=12`, `AGENT4_MAX_GRAPH_STEPS=120`, guarded by `test_summary_trigger_is_above_one_real_review`
- [x] Full backend suite after the fixes: **578 passed** (single-run policy respected)

## M11 evidence (change management)
- [x] New table `change_requests` (migration `d9e4f7b2c5a3`, chained on the single head `a7c3e1d9f2b8`; applied to the dev DB, `alembic current` = d9e4f7b2c5a3): field-level payload diff (before/after, only fields that actually change), stored deterministic impact, reason, status draft|approved|rejected
- [x] 6 new tests (`tests/test_change_management.py`): deterministic impact (critical delay + dependent shift), approve applies + reject closes + re-decide 409, capacity overload flag, draft task 409 / unknown 404 / no-op 422, TL 403, list project-scoped + status filter - **6/6 pass**
- [x] Full backend suite in the container: **469 passed** (463 + 6 change tests); single-run policy respected
- [x] Impact is computed by CODE, not an LLM: critical-path end delay  high + project end moves, dependent tasks listed from `task_dependencies`, past start  low, estimate/assignee change  capacity check (load vs weekly capacity in the window); overall severity = worst finding
- [x] API PM-only: `GET /projects/{pid}/changes[?status=]`, `POST /projects/{pid}/changes` (201, no-op 422, draft task 409, unknown task 404), `POST /projects/{pid}/changes/{id}/decision` (approve applies the change + audit `change.decision` with the applied snapshot; reject closes untouched; re-decide 409); proposals write audit `change.proposed`
- [x] Frontend: **Changes** tab (chips open / high-impact / applied) with diff table, impact findings, Approve & apply / Reject (with confirm); approved tasks in the Work plan get a **"Change..."** button  propose modal; proposing jumps to the Changes tab with the computed impact in the notice
- [x] Live e2e in the browser (project 14 "مدرسه"): proposed CR #1 on critical task #76 (end  2026-11-27) with an Arabic reason  impact high, 2 findings; Approve & apply applied it (DB: task end = 2026-11-27); audit rows 350-351 (`change.proposed` + `change.decision` with the applied snapshot); Arabic RTL verified ("التغييرات", chips "طلبات تغيير مفتوحة / أثر عالٍ / مطبَّق"); zero console errors

## M10 evidence (PDF export)
- [x] `GET /projects/{pid}/report.pdf` PM-only; reportlab + bundled Amiri (Regular+Bold, Latin+Arabic coverage verified via cmap); arabic_reshaper + python-bidi for shaping/order — no LLM anywhere
- [x] 4 new tests (`tests/test_report_pdf.py`): real %PDF bytes + content-type + download filename + pypdf page/text checks ("Project status report", "Tasks"), Arabic project name doesn't break the PDF, TL 403, unknown project 404 — **4/4 pass**
- [x] Full backend suite in the container: **463 passed** (459 + 4 report tests)
- [x] Content mirrors the app: header + summary (health, hours-weighted progress, window, next deadline, task counts, pending drafts, overdue), drawn Gantt (same colors/logic as M9 UI, today line), full tasks table, project risks with suggested actions, governance footer
- [x] Frontend "⬇ Export PDF" on the project header (PM only): fetch → blob → download (JWT in the header, so a plain link would not authenticate); verified live on project 14 (36.8KB, starts with %PDF-); Arabic notice strings included
- [x] Small robustness fix found during verification: project header now guards `p.sources` before the reload completes
- [x] Gantt geometry fix (2026-11-22): the PDF now uses the same inclusive end-date window as the UI (`max(end)+1 day`), so a task on the last day gets a full slot instead of a clamped sliver; `_gantt_span_days`/`_gantt_fraction` are unit-tested (2 one-day tasks = 50% each on a two-day window)

## M9 evidence (delivery tracking + Gantt)
- [x] Backend: `PATCH /projects/{pid}/tasks/{tid}/progress` PM-only; `todo → 0%`, `done → 100%`, `in_progress` keeps/sets the percent; draft task 409, unknown 404, non-PM 403; audit `task.progress` with before/after — **6/6 new tests pass** (`tests/test_delivery_progress.py`); service `app/services/delivery.py`
- [x] Full backend suite in the container: **459 passed** (453 baseline + 6 delivery tests)
- [x] No migration needed: progress_pct/status/start/end already existed on tasks
- [x] Gantt on the Schedule tab (no libraries): rows for approved+dated tasks over the project window; done → green, critical → orange, overdue → dashed red outline; red "today" marker; per-bar progress fill + % label; two-line row layout keeps it usable narrow and in RTL (direction-aware `margin-inline-start`/`inset-inline-*`)
- [x] Per-row delivery controls (Todo / ▶ Start / Done + % input) call the PATCH and re-render; summary chips: hours-weighted overall %, done/total, in progress, overdue
- [x] Verified live in the browser (project 14 "مدرسه", 11 dated approved tasks): Start → In progress 0%, % → 45% (chips: 5% overall), Done → 100% green (chips: 12% overall, 1/11); Arabic RTL Gantt fully translated ("مخطط جانت — تتبع التنفيذ", "مكتملة", dates "17 نوفمبر 2026"); dark mode colors follow the CSS variables; zero console errors
- [x] Portfolio auto-sync with zero extra code: after the Done click the command-center card for "مدرسه" showed 12% · 1/11 tasks done; audit rows 341-343 show the full before/after chain (todo 0 → in_progress 45 → done 100)
- [x] Gantt geometry fix (2026-11-22): end dates are inclusive, so the chart covers `min .. max+1` and a one-day task fills its slot (before, two one-day tasks rendered as a 2% sliver plus a bar pushed off the track); `today` now uses the local date, not the UTC one; approving a schedule writes `is_critical` too, so the orange critical bars work through both approval paths — verified live (project 8: 50% + 50% bars) and in `test_agent3_job.py`

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



Agent 1 live eval, Project A, openai/gpt-oss-120b (Groq), agent1_extract_v4 (2 runs):
recall 0.9333 / 0.9333, precision 0.7619 / 0.7273 (threshold 0.70), quote_faithfulness 1.0, source_verification_rate 1.0, injection_resistant True.
Not passing: duplicate_detection False (1 duplicate: same quote with and without speaker prefix).
Non-deterministic: forbidden open questions F01 (run 1), F01+F02 (run 2).
Previous: v3 on the same model: recall 1.0, precision 0.6957 (below threshold). Local qwen-nexus baseline: recall 0.47, precision 0.54.

## Agent 1 live eval, Project A (2026-11-23)
Model openai/gpt-oss-120b (Groq), prompt agent1_extract_v4, 2 runs:
recall 0.9333 / 0.9333, precision 0.7619 / 0.7273 (threshold 0.70), quote_faithfulness 1.0, source_verification_rate 1.0, injection_resistant True.
Not passing: duplicate_detection False (same quote with and without speaker prefix, e.g. "Mona:").
Non-deterministic: forbidden open questions extracted F01 (run 1), F01+F02 (run 2).
Previous: v3 same model recall 1.0, precision 0.6957 (below threshold). Local qwen-nexus baseline: recall 0.47, precision 0.54, injection_resistant False.
