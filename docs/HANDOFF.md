# NEXUS: Handoff

## State
M0-M3 code is on branch fix/m0-m3-closure. A milestone is DONE only with evidence recorded below.
M4 (Agent 1) and M5 (Agent 2: stories & tasks) are on branch feature/m5-agent2-stories-tasks.
M5.1 (Agent 2 professional edition) is on branch feature/agent2-v2-professional (stacked on M5).
M6.1 (Agent 3 platform integration) is on branch feature/m6-agent3-integration (stacked on M5.1).
M4.1 (Agent 1 smart review: modules/priorities/questions/bulk/gaps) is on branch feature/agent1-smart-review (stacked on M6.1).

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
