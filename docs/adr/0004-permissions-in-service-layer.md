# ADR 0004: Permissions enforced in the service layer

Status: accepted (locked decision)

Decision: Role and ownership checks live in services. The app does not rely on Supabase RLS (the Data API is off).
Roles come from `profiles.role`, never from JWT claims.
Consequences: One place to test every role on every endpoint. A missed check in a service is a real risk, so
permission tests are a CONSTRAINTS.md requirement.
