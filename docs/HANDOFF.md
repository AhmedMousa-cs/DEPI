# NEXUS: Handoff

## State
M0-M3 code is on branch fix/m0-m3-closure. A milestone is DONE only with evidence recorded below.
M4 (Agent 1) and M5 (Agent 2: stories & tasks) are on branch feature/m5-agent2-stories-tasks.

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
