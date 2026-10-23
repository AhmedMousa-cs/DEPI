
.PHONY: up down test lint check

up:
	docker compose up -d

down:
	docker compose down

test:
	python -m pytest -v

lint:
	lint-imports --no-cache

check: test lint
