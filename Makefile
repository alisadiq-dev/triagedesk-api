# Override BIN= (empty) in CI where tools are already on PATH.
BIN ?= .venv/bin/

.PHONY: run test lint migrate seed audit

run:
	$(BIN)uvicorn app.main:app --reload

test:
	docker compose up -d --wait test-db
	$(BIN)pytest --cov=app --cov-report=term-missing

lint:
	$(BIN)ruff check .
	$(BIN)ruff format --check .
	$(BIN)mypy

migrate:
	@echo "make migrate: available from Phase 1 (Alembic is not set up yet)."

seed:
	@echo "make seed: available from Phase 1 (no tables to seed yet)."

audit:
	$(BIN)pip-audit -r requirements-dev.txt --no-deps --disable-pip
