# Architecture

TriageDesk API is a single FastAPI service. This page shows how the pieces fit; the decisions behind them are in `docs/adr/`,
the data model (with its ER diagram) in `docs/data-model.md`, and the endpoints in `docs/api-contract.md`.

## 1. Context

```mermaid
flowchart LR
    customer([Customer]) --> nginx
    agent([Agent]) --> nginx
    admin([Admin]) --> nginx
    subgraph local["One machine (Docker Compose)"]
        nginx[Nginx<br/>127.0.0.1:8080] --> app[FastAPI app<br/>1 uvicorn worker]
        app --> db[(Postgres 17<br/>named volume)]
    end
    app -- "fetch signing keys (JWKS)" --> supabase[Local Supabase stack<br/>token issuer, ES256]
    app -- "classify ticket (background)" --> gemini[Gemini API]
    customer -. "sign in (password)" .-> supabase
    agent -. "sign in" .-> supabase
    admin -. "sign in" .-> supabase
```

Supabase only issues tokens. The API verifies them and keeps roles in its own database; it never trusts a role claim.

## 2. Inside the app

```mermaid
flowchart TB
    req[HTTP request] --> mw["Middleware: security headers, request id, access log,<br/>body size limit, per-IP rate limit"]
    mw --> router["Routers (thin): schemas in, schemas out"]
    router --> deps["Dependencies: verify token, load role from the database,<br/>per-user rate limit on creates"]
    deps --> service["Services: permissions, visibility, workflow, SLA"]
    service --> repo["Repositories: all SQL"]
    repo --> pg[(Postgres)]
    service --> ai["app/ai: prompt, TriageModel interface, output validation"]
    ai --> gem[Gemini adapter]
```

Rules that hold everywhere: routers never touch the ORM; permissions live in the service layer (not in Postgres policies);
a ticket you may not see is a 404; customers get separate allowlist response schemas.

## 3. Authentication and role lookup

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant S as Supabase (JWKS)
    participant D as Postgres
    C->>A: request with Bearer token
    A->>A: per-IP rate limit (before any work)
    A->>S: signing keys (cached, rate-limited refetch)
    A->>A: check alg ES256, kid, signature, exp, iss, aud, not anonymous
    A->>D: profile for sub (created as customer on first request)
    D-->>A: role (the only source of truth)
    A->>A: service checks the role and the ticket's visibility
    A-->>C: response (identical 401 for any bad token)
```

## 4. Ticket creation and AI triage

```mermaid
sequenceDiagram
    participant C as Customer
    participant A as API
    participant D as Postgres
    participant R as TriageRunner (background task)
    participant G as Gemini
    C->>A: POST /tickets (title, description)
    A->>D: insert ticket (priority medium, ai_status pending, SLA from medium) + event
    A-->>C: 201 (customer allowlist)
    A--)R: after the response
    R->>D: read ticket and active categories (connection released)
    R->>G: prompt: ticket text is untrusted data, per-call boundary
    alt valid answer
        G-->>R: JSON (category, priority, sentiment, draft)
        R->>R: strict validation, category must be active
        R->>D: lock row, write AI fields (never over a human override), SLA recalculated from created_at
    else timeout, error, cut-off or invalid answer
        R->>D: keyword priority, ai_status failed, category and sentiment stay empty
    end
    Note over R,D: A sweeper re-runs triage for tickets still pending after 2 minutes (startup, then every 60 s)
```

## 5. Status workflow

```mermaid
stateDiagram-v2
    [*] --> open
    open --> in_progress
    in_progress --> waiting_on_customer
    waiting_on_customer --> in_progress
    in_progress --> resolved
    resolved --> in_progress: reopen
    resolved --> closed
    closed --> [*]
```

Only the assignee agent or an admin changes status; customers never do. Every other transition is a 409 `invalid_transition`.
`closed` is final and takes no comments. Every change writes a `ticket_events` row in the same transaction.

## 6. Deployment (local production)

```mermaid
flowchart LR
    host([Host: curl, browser]) -- "127.0.0.1:8080 only" --> nginx
    subgraph net["Docker network 172.29.77.0/24"]
        nginx["nginx 172.29.77.10<br/>overwrites X-Forwarded-For<br/>64k body limit, no docs"] --> app["app<br/>trusts X-Forwarded-For only from 172.29.77.10<br/>read-only, non-root"]
        app --> db[("db<br/>no published port")]
        migrate["migrate (one-shot)<br/>alembic upgrade head"] --> db
    end
    app -. "host.docker.internal:54321 (JWKS)" .-> sup[Supabase stack on the host]
    db --- vol[(named volume)]
```

See `docs/runbook.md` for start, stop, backup and restore.
