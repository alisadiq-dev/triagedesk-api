# ADR 0001: FastAPI BackgroundTasks for AI triage (no Redis, no Celery)

Status: accepted (locked decision)

Context: Triage must not block ticket creation. The project stays small and cheap to deploy.
Decision: Run triage with FastAPI BackgroundTasks after the ticket is committed. No queue, no worker, no microservices.
Consequences: Fewer moving parts. Work is lost if the process restarts, so a ticket can stay `ai_status = pending`.
A fix is proposed in Phase 5 and built only after approval.
