# NEXUS: Handoff

## State
M0-M3 code is on branch fix/m0-m3-closure. A milestone is DONE only with evidence recorded below.

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
