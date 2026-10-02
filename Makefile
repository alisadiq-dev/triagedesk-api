# Override BIN= (empty) in CI where tools are already on PATH.
BIN ?= .venv/bin/

.PHONY: run test lint migrate seed audit bench

run:
	API_DOCS_ENABLED=true $(BIN)uvicorn app.main:app --reload

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

# Needs the test Postgres (make test starts it). Creates and drops a throwaway database.
bench:
	docker compose up -d --wait test-db
	PYTHONPATH=. BENCH_ADMIN_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/postgres $(BIN)python scripts/benchmark_list.py
