# ADR 0002: Schema changes only through Alembic migrations

Status: accepted (locked decision)

Decision: Every schema change is an Alembic migration with a working downgrade. No manual database edits.
After the first deploy, changes follow expand, migrate, contract.
Consequences: Reproducible schema, CI can run `alembic upgrade head` on a fresh Postgres. Slightly more ceremony per change.
