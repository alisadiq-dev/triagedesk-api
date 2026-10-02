# Override BIN= (empty) in CI where tools are already on PATH.
BIN ?= .venv/bin/

.PHONY: run test lint migrate seed audit

run:
	$(BIN)uvicorn app.main:app --reload

test:
	docker compose up -d --wait test-db
	$(BIN)pytest --cov=app --cov-report=term-missing
	$(BIN)coverage report --include="app/services/*,app/ai/*" --fail-under=85

lint:
	$(BIN)ruff check .
	$(BIN)ruff format --check .
	$(BIN)mypy

migrate:
	$(BIN)alembic upgrade head

seed:
	$(BIN)python -m app.seed

audit:
	$(BIN)pip-audit -r requirements-dev.txt --no-deps --disable-pip
