# ADR 0003: Repository pattern over SQLAlchemy

Status: accepted (locked decision)

Decision: All database access goes through repositories. Routers never call the ORM; services call repositories.
Consequences: Services are unit-testable with fakes, queries live in one place (helps N+1 and index work).
Cost: an extra layer for simple reads.
