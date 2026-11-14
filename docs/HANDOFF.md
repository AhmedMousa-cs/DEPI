# NEXUS: Handoff

## State
M0-M3 code is on branch fix/m0-m3-closure. A milestone is DONE only with evidence recorded below.
M4 (Agent 1) and M5 (Agent 2: stories & tasks) are on branch feature/m5-agent2-stories-tasks.
M5.1 (Agent 2 professional edition) is on branch feature/agent2-v2-professional (stacked on M5).

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
