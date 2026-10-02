# curl collection

Every endpoint of `/api/v1`, in the order of a ticket's life. The blocks run top to bottom: later ones use variables set by earlier ones.
Against the local production stack the base URL is `http://127.0.0.1:8080` (Nginx). Interactive docs are off there; the full contract is
`docs/api-contract.md`.

## Setup

```bash
eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"   # do not echo this: it holds the stack's keys
.venv/bin/python -m scripts.create_demo_users                      # once (see docs/local-supabase.md)
eval "$(sh scripts/demo_env.sh)"                                   # CUSTOMER_TOKEN, AGENT_TOKEN, ADMIN_TOKEN (1 hour), API, UA
pretty() { python3 -m json.tool; }                                 # any JSON pretty printer works
```

A normal `User-Agent` is sent on every call (`$UA`). Errors always look like `{"error": {"code": "...", "message": "..."}}`.

## Identity and users

```bash
# 1. who am I (any role)
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/me" | pretty

# 2. list users (admin; filter by role; page and page_size work on every list)
curl -s -A "$UA" -H "Authorization: Bearer $ADMIN_TOKEN" "$API/api/v1/users?role=agent&page_size=10" | pretty

# 3. one user; keep the agent's id for later
AGENT_ID=$(curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/me" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -A "$UA" -H "Authorization: Bearer $ADMIN_TOKEN" "$API/api/v1/users/$AGENT_ID" | pretty

# 4. change a role (admin only; here the agent stays an agent, which is a no-op). A customer gets 403, an admin changing
#    their own role gets 409 cannot_change_own_role, and the last admin cannot be demoted.
curl -s -A "$UA" -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"role": "agent"}' "$API/api/v1/users/$AGENT_ID" | pretty
```

## Categories and SLA policies

```bash
# 5. list categories (agent: active only; admin may add include_inactive=true). Customers get 403.
curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/categories" | pretty

# 6. create a category (admin), then 7. deactivate it (soft delete; there is no hard delete)
CATEGORY_ID=$(curl -s -A "$UA" -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"name\": \"Curl demo $(date +%s)\", \"description\": \"Created by the curl collection\"}" "$API/api/v1/categories" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -A "$UA" -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"is_active": false}' "$API/api/v1/categories/$CATEGORY_ID" | pretty

# 8. SLA policies (one per priority, hours counted 24/7)
curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/sla-policies" | pretty

# 9. change a policy (admin). It only affects deadlines calculated afterwards; this call sets the same values.
curl -s -A "$UA" -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"response_hours": 8, "resolution_hours": 72}' "$API/api/v1/sla-policies/medium" | pretty
```

## Tickets

```bash
# 10. a customer creates a ticket: title and description only. The response is the customer allowlist.
TICKET=$(curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Cannot log in after the password reset", "description": "The reset email never arrives, I am locked out."}' \
  "$API/api/v1/tickets" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "$TICKET"

# 11. list: the customer sees only their own tickets; staff see more fields. Filters: status, priority, category_id,
#     unassigned, sla_breached, q (full text), created_after, created_before, assignee_id (admin); sort=-created_at (default),
#     created_at, first_response_due_at, resolution_due_at (staff).
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/tickets?page_size=5" | pretty
curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/tickets?unassigned=true&q=password&sort=first_response_due_at" | pretty
curl -s -A "$UA" -H "Authorization: Bearer $ADMIN_TOKEN" "$API/api/v1/tickets?sla_breached=false&priority=high" | pretty
# A customer using a staff-only filter is refused (403): it would reveal fields they may not see.
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/tickets?priority=high" | pretty

# 12. one ticket (the AI triage fills priority, category, sentiment and the draft within seconds; ai_status goes
#     pending -> completed or failed). The AI draft is visible to the assignee and admins only.
sleep 3
curl -s -A "$UA" -H "Authorization: Bearer $ADMIN_TOKEN" "$API/api/v1/tickets/$TICKET" | pretty

# 15. an agent claims it (409 already_assigned if someone else got there first)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/tickets/$TICKET/claim" | pretty

# 13. the assignee (or an admin) overrides the priority; both SLA deadlines are recalculated from created_at
curl -s -A "$UA" -X PATCH -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"priority": "high"}' "$API/api/v1/tickets/$TICKET" | pretty

# 19. SLA status
curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/tickets/$TICKET/sla" | pretty

# 14. status workflow: open -> in_progress -> waiting_on_customer / resolved -> closed (reopen: resolved -> in_progress)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "in_progress"}' "$API/api/v1/tickets/$TICKET/status" | pretty
# an invalid transition is a 409 with a clear message
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "closed"}' "$API/api/v1/tickets/$TICKET/status" | pretty

# 16. release it, 17. an admin assigns it to a specific agent (or admin)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/tickets/$TICKET/release" | pretty
curl -s -A "$UA" -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"assignee_id\": \"$AGENT_ID\"}" "$API/api/v1/tickets/$TICKET/assignee" | pretty
```

## Comments

```bash
# 21. the assignee posts an internal note, then a public reply (the first public reply stops the first-response clock)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "Checked the mail logs: the reset email bounced.", "is_internal": true}' "$API/api/v1/tickets/$TICKET/comments" | pretty
curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "We fixed the mail problem. Please try the reset again."}' "$API/api/v1/tickets/$TICKET/comments" | pretty
# the customer can answer (public only; is_internal true is a 403)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "It works now, thank you."}' "$API/api/v1/tickets/$TICKET/comments" | pretty

# 20. list comments: staff see everything; the customer sees public comments with author_type customer or support, no names
curl -s -A "$UA" -H "Authorization: Bearer $AGENT_TOKEN" "$API/api/v1/tickets/$TICKET/comments" | pretty
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/tickets/$TICKET/comments" | pretty
```

## Audit trail and closing

```bash
# resolve and close, then 18. the audit trail (staff only, oldest first)
for s in resolved closed; do
  curl -s -A "$UA" -X POST -H "Authorization: Bearer $AGENT_TOKEN" -H "Content-Type: application/json" \
    -d "{\"status\": \"$s\"}" "$API/api/v1/tickets/$TICKET/status" -o /dev/null -w "$s: %{http_code}\n"
done
curl -s -A "$UA" -H "Authorization: Bearer $ADMIN_TOKEN" "$API/api/v1/tickets/$TICKET/events?page_size=100" | pretty
# a closed ticket takes no more comments (409 ticket_closed)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "One more thing"}' "$API/api/v1/tickets/$TICKET/comments" | pretty
```

## Things that fail on purpose

```bash
# no token: 401, the same body for every kind of bad token
curl -s -A "$UA" "$API/api/v1/me" -i | head -n 12
# another user's ticket looks like it does not exist (404), not "forbidden"
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/tickets/00000000-0000-0000-0000-000000000000" | pretty
# a customer on an admin route: 403
curl -s -A "$UA" -H "Authorization: Bearer $CUSTOMER_TOKEN" "$API/api/v1/users" | pretty
# malformed JSON: 400; wrong field: 422 (the message names the field and never echoes your input)
curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" -H "Content-Type: application/json" -d '{oops' "$API/api/v1/tickets" | pretty
curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" -H "Content-Type: application/json" -d '{"title": ""}' "$API/api/v1/tickets" | pretty
# body over 64 KiB: 413
head -c 70000 /dev/zero | tr '\0' x | curl -s -A "$UA" -X POST -H "Authorization: Bearer $CUSTOMER_TOKEN" --data-binary @- "$API/api/v1/tickets" | pretty
```

Rate limits answer `429` with a `Retry-After` header (per user: 10 tickets and 30 comments a minute; per client address: 120 a minute on
`/health` and `/ready`, 600 on `/api/v1`). They are per app instance: see the README.
