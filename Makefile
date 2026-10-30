# recipes start with ">" (not TAB) so copy/paste can't break this file
.RECIPEPREFIX = >
.PHONY: up down logs migrate seed testdb test eval eval-check eval-agent1

up:
> docker compose up -d --build

down:
> docker compose down

logs:
> docker compose logs -f

migrate:
> docker compose exec api alembic upgrade head

seed:
> docker compose exec api python -m app.seed

testdb:
> docker compose up -d db
> docker compose exec -T db psql -U nexus -d nexus -tAc "SELECT 1 FROM pg_database WHERE datname='nexus_test'" | grep -q 1 || docker compose exec -T db psql -U nexus -d nexus -c "CREATE DATABASE nexus_test"

test: testdb
> docker compose run --rm -e RUN_TESTS_IN_DOCKER=1 -v $(CURDIR)/backend:/code api python -m pytest -q

eval-check:
> PYTHONPATH=backend python -m pytest evals/tests -q

eval-agent1:
> PYTHONPATH=backend python -m evals.run_agent1 --golden project-a

eval:
> PYTHONPATH=backend deepeval test run evals/test_deepeval_smoke.py
