# ADR 0008: The AI is a classifier behind an interface, never an authority

Status: accepted (2026-10-02)

Context: Ticket text comes from customers and may try to steer the model. The model costs money per call and can fail or be cut off.
Decision:
- `TriageModel` is a Protocol; the Gemini adapter (`google-genai`, default `gemini-3.5-flash-lite`) is one implementation, a disabled model
  another. Tests never call a model.
- Ticket text is untrusted data in a per-call random boundary and is clipped. The output is validated strictly (exactly four fields, enums,
  an active category id, length); anything else is a failure. Only the AI fields are ever written, never over a human override, and never
  roles, assignees, status or permissions.
- Any failure (timeout, error, invalid or cut-off answer, over the call budget) takes the keyword fallback: priority from rules,
  `ai_status = failed`, category and sentiment empty. Ticket creation never waits for the model.
- Cost controls: a timeout, `AI_MAX_OUTPUT_TOKENS` (a cut-off answer is a failure), a global cap on calls per minute, a per-user ticket rate
  limit, and a recovery sweeper that gives up on a ticket after three failed attempts.
Accepted limitation (security review item L4): a hostile ticket can persuade the model to answer "urgent", which shortens that ticket's
SLA deadlines and moves it up the staff queue. It changes no role, permission or ownership. Staff see `priority_source = ai` and can
override the priority. A possible later mitigation is to cap the AI at "high" unless a keyword rule matches.
Consequences: A ticket refused by the budget stays `failed` (there is no re-triage endpoint, by design). The suggested reply is text written
by a model that read attacker-controlled input: staff must read it before sending, and it is never posted automatically.
