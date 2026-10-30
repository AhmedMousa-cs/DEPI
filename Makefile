.PHONY: up down logs migrate test eval-check eval-agent1

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose exec api alembic upgrade head
test:
	docker compose run --rm -v $(CURDIR)/backend:/code api python -m pytest -q

eval-check:
	PYTHONPATH=backend python -m pytest evals/tests -q

eval-agent1:
	PYTHONPATH=backend python -m evals.run_agent1 --golden project-a
