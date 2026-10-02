# API contract (`/api/v1`): APPROVED (2026-10-02, all 11 decisions as proposed)

Follows `docs/PRD.md`. All endpoints need `Authorization: Bearer <token>`
(except `/health` and `/ready`, which exist already and are outside `/api/v1`). Interactive docs stay at `/docs` (FastAPI).

## Conventions

- JSON, `snake_case` field names, enum values lowercase as in the PRD. Timestamps are ISO-8601 UTC. Ticket, comment and user ids are UUIDs; category ids are integers.
- **Errors:** always `{"error": {"code": "...", "message": "..."}}`. Validation messages name fields and never echo input.
- **Visibility before permission:** the ticket is resolved first. Not visible to the caller gives 404 (customer: not own; agent: assigned to another agent). Visible but not allowed gives 403.
- **Status codes:** 400 unparseable request (malformed JSON); 401 missing or invalid token (identical body); 403 role or assignment not allowed; 404 not found or not yours; 409 conflict or invalid state; 422 schema validation (body, query, path types); 429 rate limited (Phase 8; only reserved for now); 503 `auth_unavailable`.
- **Error codes:** `bad_request`, `unauthorized`, `forbidden`, `not_found`, `validation_error`, `rate_limited`, `auth_unavailable`, and for 409: `invalid_transition`, `ticket_closed`, `already_assigned`, `name_taken`, `role_change_blocked`, `cannot_change_own_role`.
- **Pagination (one shape for every list endpoint):** query `page` (>= 1, default 1) and `page_size` (1 to 100, default 20), plus `sort`. Response:
  ```json
  {"items": [ ... ], "page": 1, "page_size": 20, "total": 142}
  ```
  `total` is the count after filters. An out-of-range page returns an empty `items`. Offset pagination is chosen because it gives `total` and works with the breach filter in SQL.
- **Retries:** `POST /tickets` and `POST .../comments` are not idempotent (no `Idempotency-Key`). Retrying after a timeout can create a duplicate; this is documented, not built, because you did not ask for it.
- Responses never contain internal columns (`search_vector`, `updated_by`, ...). Patch bodies reject unknown fields (422).

## Schemas

| Schema | Fields |
|---|---|
| `Me` | `id`, `email` (nullable), `role` |
| `User` | `id`, `email`, `role`, `created_at` |
| `Category` | `id`, `name`, `description` (nullable), `is_active` |
| `SlaPolicy` | `priority`, `response_hours`, `resolution_hours`, `updated_at` |
| `CustomerTicket` | `id`, `title`, `description`, `status`, `created_at`, `updated_at` (exactly this allowlist, tested) |
| `StaffTicket` | `CustomerTicket` fields plus `customer_id`, `customer_email`, `assignee_id`, `category_id`, `category_source`, `priority`, `priority_source`, `sentiment`, `ai_status`, `ai_suggested_reply`, `ai_model`, `ai_prompt_version`, `first_response_due_at`, `resolution_due_at`, `first_responded_at`, `resolved_at`, `sla_breached` |
| `CustomerComment` | `id`, `author_type` (`customer` or `support`), `body`, `created_at` |
| `StaffComment` | `id`, `author_id`, `author_role`, `is_internal`, `body`, `created_at` |
| `TicketEvent` | `id`, `event_type`, `actor_id` (null = system or AI), `from_value`, `to_value`, `created_at` |
| `SlaStatus` | `first_response_due_at`, `resolution_due_at`, `first_responded_at`, `resolved_at`, `first_response_breached`, `resolution_breached`, `breached` |
| `Page[T]` | `items`, `page`, `page_size`, `total` |

## Endpoints

`C` customer, `A` agent, `X` admin. "assignee" means the agent the ticket is assigned to. Phase column shows when each endpoint is built.

### Identity and users

| # | Method and path | Roles | Request | Success | Errors | Phase |
|---|---|---|---|---|---|---|
| 1 | `GET /me` | C A X | none | 200 `Me` | 401 | 3 |
| 2 | `GET /users` | X | query: `role?`, page, page_size | 200 `Page[User]` | 401 403 422 | 3 |
| 3 | `GET /users/{id}` | X | none | 200 `User` | 401 403 404 422 | 3 |
| 4 | `PATCH /users/{id}` | X | `{"role": "customer|agent|admin"}` | 200 `User` | 401 403 404 409 `cannot_change_own_role`, 409 `role_change_blocked` (agent to customer with non-closed assigned tickets), 422 | 3 |

Role change writes one structured log line (actor id, target id, old role, new role, request id).

### Categories and SLA policies

| # | Method and path | Roles | Request | Success | Errors | Phase |
|---|---|---|---|---|---|---|
| 5 | `GET /categories` | A X | query: `include_inactive?` (X only; A gets active only) | 200 `list[Category]` (small, unpaginated) | 401 403 | 3 |
| 6 | `POST /categories` | X | `{"name", "description?"}` | 201 `Category` | 401 403 409 `name_taken`, 422 | 3 |
| 7 | `PATCH /categories/{id}` | X | `{"name?", "description?", "is_active?"}` | 200 `Category` | 401 403 404 409 `name_taken`, 422 | 3 |
| 8 | `GET /sla-policies` | A X | none | 200 `list[SlaPolicy]` (always 4) | 401 403 | 3 |
| 9 | `PATCH /sla-policies/{priority}` | X | `{"response_hours?", "resolution_hours?"}` (resolution >= response after merge) | 200 `SlaPolicy` | 401 403 404 422 | 3 |

No delete endpoints for categories or SLA policies. Customers get 403 on 5 to 9.

### Tickets

| # | Method and path | Roles | Request | Success | Errors | Phase |
|---|---|---|---|---|---|---|
| 10 | `POST /tickets` | C | `{"title" (1..200), "description" (1..10000)}` | 201 `CustomerTicket` | 401 403 422 | 3 |
| 11 | `GET /tickets` | C A X | query: page, page_size, `sort`, `status?`; later `priority?`, `category_id?`, `assignee_id?` (X), `unassigned?`, `sla_breached?`, `q?`, `created_after?`, `created_before?` | 200 `Page[CustomerTicket]` for C, `Page[StaffTicket]` for A and X | 401 422 | 3 (page, sort, status), 7 (rest) |
| 12 | `GET /tickets/{id}` | C (own) A (visible) X | none | 200 `CustomerTicket` or `StaffTicket` | 401 404 422 | 3 |
| 13 | `PATCH /tickets/{id}` | A (assignee) X | `{"category_id?", "priority?"}`, at least one; category must be active | 200 `StaffTicket` | 401 403 404 409 `ticket_closed`, 422 | 3 |
| 14 | `POST /tickets/{id}/status` | A (assignee) X | `{"status": "in_progress|waiting_on_customer|resolved|closed"}` | 200 `StaffTicket` | 401 403 404 409 `invalid_transition`, 409 `ticket_closed`, 422 | 4 |
| 15 | `POST /tickets/{id}/claim` | A X | none | 200 `StaffTicket` | 401 404 409 `already_assigned`, 409 `ticket_closed` | 3 |
| 16 | `POST /tickets/{id}/release` | A (assignee) X | none | 200 `StaffTicket` | 401 403 404 409 `ticket_closed` | 3 |
| 17 | `PUT /tickets/{id}/assignee` | X | `{"assignee_id": uuid}` (must be agent or admin) | 200 `StaffTicket` | 401 403 404 409 `ticket_closed`, 422 | 3 |
| 18 | `GET /tickets/{id}/events` | A (visible) X | query: page, page_size | 200 `Page[TicketEvent]` (oldest first) | 401 403 404 | 4 |
| 19 | `GET /tickets/{id}/sla` | A (visible) X | none | 200 `SlaStatus` | 401 403 404 | 6 |

Notes:
- Customers on endpoints 13 to 19 get 404 for another user's ticket and 403 for their own (role not allowed).
- Allowed status transitions (anything else is 409 `invalid_transition`): `open` to `in_progress`; `in_progress` to `waiting_on_customer` or `resolved`; `waiting_on_customer` to `in_progress`; `resolved` to `closed` or `in_progress` (reopen, which clears `resolved_at`). `closed` is final.
- An agent who sees an unassigned ticket must claim it (15) before changing it, commenting or overriding; otherwise 403.
- `PATCH /tickets` sets `category_source` or `priority_source` to `human`, recalculates both SLA deadlines from `created_at` when priority changes, and writes `ticket_events`.
- Tickets are created with priority `medium` and `ai_status = pending`; AI triage arrives in Phase 5 (no endpoint changes).

### Comments

| # | Method and path | Roles | Request | Success | Errors | Phase |
|---|---|---|---|---|---|---|
| 20 | `GET /tickets/{id}/comments` | C (own) A (visible) X | query: page, page_size | 200 `Page[CustomerComment]` (public only) for C, `Page[StaffComment]` (all) for A and X; oldest first | 401 404 | 3 |
| 21 | `POST /tickets/{id}/comments` | C (own, public only) A (assignee) X | `{"body" (1..10000), "is_internal?": false}` | 201 `CustomerComment` for C, `StaffComment` for A and X | 401 403 (customer sending `is_internal: true`, or agent who is not the assignee) 404 409 `ticket_closed`, 422 | 3 |

A public comment by an agent or admin sets `first_responded_at` once, in the same transaction.

## Not in the contract (on purpose)

No ticket deletion, no editing title or description, no customer status changes, no re-triage endpoint, no AI-draft endpoint (the draft is a field on `StaffTicket`), no category or SLA deletion, no user deactivation, no comment edit or delete.

## Decisions I need from you

1. `snake_case` JSON fields (matches the PRD and the database), not camelCase.
2. Offset pagination with the `Page[T]` shape above, `page_size` capped at 100.
3. `GET /me` (endpoint 1) is my addition: without it a client cannot learn its own role. Keep or drop?
4. Only customers create tickets (agents and admins get 403). Alternative: admins may create tickets for themselves.
5. Status is changed with `POST /tickets/{id}/status` and the transition table above (it allows `in_progress` to `resolved` directly and does not allow `waiting_on_customer` to `resolved`). OK?
6. `claim` and `release` as action sub-resources, and `PUT .../assignee` for admin assignment.
7. An admin cannot change their own role (409 `cannot_change_own_role`), so the system can never end up with no admin. My addition. Keep or drop?
8. Customers get 403 (not 404) on staff-only sub-resources of their own ticket, 404 for someone else's.
9. Malformed JSON is 400; schema validation failures are 422.
10. Categories list is staff-only (customers never see categories).
11. Ticket and comment creation are documented as unsafe to retry (no idempotency key).
