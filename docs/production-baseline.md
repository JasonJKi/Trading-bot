# Production baseline

The engineering bar this codebase commits to, where we are against it today,
and what to build next. Sister to the other two architecture docs:

| Doc | Question it answers |
|---|---|
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | *How does the running system work today?* (operating manual) |
| [`docs/system-architecture.md`](./system-architecture.md) | *What are we building toward and why?* (north star) |
| **`docs/production-baseline.md` (this)** | *What is the engineering bar, and how close are we to it?* |

## Philosophy

> **Production-grade core, demo-grade edges.**

The application is built as if it will be deployed at scale, even though
right now it serves one operator on a Mac mini and a public demo URL. The
SaaS *plumbing* — auth, billing, multi-tenancy, onboarding flows — is a thin
shell to be added later. It must not leak into domain code. The
*application*, the *data plane*, the *trust boundary*, and the *operational
posture* are non-negotiably production-grade today.

The test we apply to every change:

> *If a senior engineer were handed this repo tomorrow and told to launch it
> publicly with paying users in a week, would they need to rewrite anything,
> or just bolt on auth/billing/hosting?*

If the answer is "just bolt on," we're on the line we want.

## The bar (what production-grade core means here)

Twelve concrete requirements. Every change is measured against this list.

### Code & contracts

1. **Pydantic at every boundary.** Settings, HTTP requests/responses, agent
   inputs/outputs, store rows. Validation happens at the edge; internal code
   trusts the types.
2. **Trust boundary enforced in code, not docs.** LLM proposals never reach
   the broker without going through a deterministic Python validator that
   shares the same code path as the orchestrator's risk checks.
3. **Versioning on every replayable artifact.** `Strategy.version`,
   `StrategyTemplate.version`, `Agent.version`, stamped on each row that
   depends on them.

### State & data

4. **The DB is the spine.** No agent, scheduler, or worker holds state
   the DB doesn't know about. Restart-safe, replayable, debuggable from the
   DB alone.
5. **Append-only audit, idempotent writes.** `AuditEvent` is never updated;
   external integrations use stable `external_id`s; broker writes use
   deterministic `client_order_id`s.
6. **Schema changes go through migrations.** `Alembic`-managed; no
   hand-edited DDL; every revision is reviewable in the diff.

### Process & isolation

7. **Two schedulers, never one.** Trading scheduler is real-time and never
   blocks on an LLM. Analytical scheduler is slow and async. They communicate
   *only* through the DB.
8. **Worker and API are separable processes.** Today they may co-locate for
   convenience, but neither imports the other's runtime; either can be
   pulled into its own container without code changes.

### Observability & operations

9. **Structured logs with correlation.** JSON in prod, every request and
   every agent run gets a `trace_id` that propagates through logs.
10. **Errors are tracked, not just logged.** A real error sink (Sentry or
    equivalent) captures unhandled exceptions in API, worker, and agents.
11. **Agent runs are inspectable.** Every LLM call, tool call, cost, and
    latency is captured (Logfire / LangFuse). For a demo this is a feature;
    for production it is debugging oxygen.

### Security posture

12. **Secrets fail loud, never silently.** Settings refuses to start in live
    mode without the live confirm token *and* validates that the alerting
    channels live mode requires are actually configured. Cookies are signed,
    not memory-tokens; auth survives restarts.

## Audit: where we are today

Ratings: **OK** = meets the bar, **partial** = meets some of it,
**gap** = doesn't meet it yet.

| # | Requirement | Status | Notes |
|---|---|---|---|
| 1 | Pydantic at boundaries | OK | `src/config.py`, `src/api/schemas.py`, `src/research/schemas.py`, `src/templates/base.py::ParamSpec` |
| 2 | Trust boundary enforced | OK | `risk.py` checks shared between orchestrator + paper→live gate; `BrokerAdapter.submit` enforces per-position cap |
| 3 | Versioning stamped | OK | `Trade/Signal/Order.strategy_version`; templates carry `version`; agent versions TODO when synthesis lands |
| 4 | DB is the spine | OK | `src/core/store.py` — no shadow state in any module |
| 5 | Append-only audit, idempotent | OK | `AuditEvent`, `client_order_id`, `external_id` on Reddit/Congress/News/ResearchDocument |
| 6 | Migrations | OK | Alembic wired in (`alembic.ini`, `alembic/`, initial revision = current schema). `init_db()` upgrades to head; pre-alembic DBs are auto-stamped at the initial revision. `make db-revision MSG="..."` for new revisions. |
| 7 | Two schedulers | partial | Trading scheduler (orchestrator + reconciler) is real and runs in `cli.py::run`. **Analytical scheduler is documented but not built** (`src/research/scheduler.py` is the placeholder). Research only runs on demand. |
| 8 | Worker/API separable | partial | Code is split (`src/core/orchestrator.py` vs `src/api/`), no shared runtime, but the Dockerfile co-locates and the README treats them as one deploy. Needs an explicit second compose service / `Procfile`. |
| 9 | Structured logs + correlation | partial | `logging_setup.py` produces JSON in prod; **no `trace_id` middleware**, no per-request log binding. Agent runs not yet correlated to their DB row. |
| 10 | Error tracking (Sentry) | **gap** | Not wired. Production-scale failure analysis is impossible without it. |
| 11 | Agent inspectability | **gap** | `logfire` is in `[research]` extras but never `configure()`d. Research runs are opaque past the rows they write. **Highest-leverage demo upgrade.** |
| 12 | Secrets fail loud | OK | `Settings.validate_for_runtime()` runs at every entry point: requires DATABASE_URL always; in live mode requires the live token, both Alpaca creds, and at least one alert channel. Session cookies are HMAC-signed (`SESSION_SECRET`); they survive restarts and work across multiple workers. |

Other gaps surfaced during the audit, lower-priority:

- ~~`@app.on_event("startup")` is deprecated~~ — fixed: replaced with `lifespan` context manager.
- ~~No login rate-limit~~ — fixed: 5 attempts / 5 min / IP, in-memory bucket, returns 429.
- ~~Streamlit residue still in the tree~~ — fixed: deleted `cli.py::dashboard`, `dashboard*` package include, ARCHITECTURE.md / README.md / CI references.
- ~~No `user_id` column on tenant-scoped tables~~ — fixed: 11 tenant-scoped tables (Trade, Order, Signal, EquitySnapshot, BotPosition, BotStatus, AuditEvent, ResearchQuery, ResearchFinding, StrategySpec, BacktestReport) carry `user_id` indexed, defaulted to `"demo"`. Shared/cache tables (CongressDisclosure, NewsItem, ResearchDocument) intentionally do not.
- No type-checker in CI (ruff is lint-only) — punted to Phase 21.
- API has unit tests but no integration test against the FastAPI app — added partial coverage via `tests/test_auth.py`; full integration tests still TODO.
- ~~`ARCHITECTURE.md` references the Streamlit dashboard on :8080~~ — fixed.
- 22 pre-existing ruff F401 (unused-import) findings across `src/`. Not introduced by Phase 19; sweep PR pending.

## Punch list, prioritized

### P0 — close production-grade gaps (do soon)

These keep the codebase honest against the bar above. Each is a small,
independent PR.

1. **Alembic migrations.** Initial revision = current schema. From now on,
   schema changes ship as revisions. `make db-upgrade` runs them.
2. **Global FastAPI exception handler + lifespan.** Replace `on_event` with
   a `lifespan(app)` async context manager; wrap unhandled exceptions to
   return a sanitized `{"error": "internal"}` and log with full stack.
3. **Persistent session secret.** Replace `_session_token` with a signed
   cookie (`itsdangerous` or `fastapi-csrf-protect`-style). Secret comes
   from `SESSION_SECRET` env, generated once. Cookie survives restarts and
   multiple workers.
4. **Login rate limit.** Trivial in-memory bucket (slowapi or hand-rolled)
   on `POST /api/auth/login`. 5 attempts / 5 minutes / IP.
5. **Settings.validate_for_runtime().** New method called from every entry
   point. In live mode: require alpaca creds *and* at least one alert
   channel. In any mode: require either `DATABASE_URL` writable, or fail.
6. **`user_id` column on tenant-scoped tables.** Default `"demo"` on every
   row. Update queries to filter by it. Multi-tenancy then becomes a
   middleware change, not a migration.
7. **Streamlit residue removed.** Delete `cli.py::dashboard`, the
   `dashboard/` package, README/ARCHITECTURE references. The Next.js +
   FastAPI dashboard is the single dashboard.

### P1 — production-scale enablement

8. **Sentry wired.** Worker, API, agent. DSN from env; no-op when unset.
   Request `trace_id` becomes Sentry tag.
9. **Trace-id middleware.** Per-request UUID into a contextvar; logs and
   Sentry events pick it up. Agent runs get their own `trace_id` (the
   `ResearchQuery.id` is a fine seed).
10. **Logfire / LangFuse on agent calls.** Configure once at agent startup;
    every PydanticAI call, tool call, and synthesis pass emits a span.
    Surface a "View trace" link in the dashboard's research detail page.
11. **Worker/API split as separate compose services.** `docker-compose.yml`
    grows a `worker:` and `api:` service sharing the volume. The Mac mini
    deploy still runs both, but on Fly/Railway you can scale them
    independently.
12. **Analytical scheduler (`src/research/scheduler.py`).** APScheduler in
    its own process that wakes the research agent on a cadence
    (e.g. nightly), reads `ResearchQuery` rows queued by the API, writes
    findings.
13. **Type-checker in CI.** Pyright (faster) or mypy on `src/`. Start with
    `--strict-optional`, ratchet up.

### P2 — demo polish (demo-grade edges)

14. **Demo mode.** `--demo` flag (and `DEMO_MODE=true` env) seeds a
    deterministic dataset on a fresh DB: a fake research run with rich
    findings, a backtested spec, a paper-trading bot with a week of
    snapshots. The dashboard then has something interesting on first load.
    Critical for showing the system in 60 seconds without depending on a
    live LLM call or live broker data.
15. **Agent trace timeline in the dashboard.** Per `ResearchQuery`, render
    the planner → researcher (per-source) → synthesizer steps with
    timings, token costs, and tool calls. *This is the
    single highest-impact demo asset for an AI-consulting pitch.*
16. **Landing tile + 10-second pitch.** Above-the-fold on the dashboard
    home: what the system is, who built it, link to a demo run, link to
    GitHub, "I build agent infrastructure for businesses — book a call."
17. **Doc refresh.** `ARCHITECTURE.md` updated for the Next.js dashboard;
    `OPS.md` updated for split compose services and Sentry triage.

### P3 — nice-to-have

- `make ci` target chaining lint + type-check + test + docker build.
- API integration tests against `httpx.AsyncClient` + a temp SQLite.
- `AuditEvent.message` cap test + truncation helper.
- Connection-pool config in `init_db()` for the day SQLite turns into Postgres.

## Build order, recommended

The cheapest path that makes the codebase visibly better, in order:

```
P0.7 (delete Streamlit)        — quick, removes confusion
P0.1 (Alembic)                  — unblocks every future schema change
P0.5 (validate_for_runtime)     — fail-loud in 30 lines
P0.2 (lifespan + exc handler)   — stops silent 500s, fixes deprecation
P0.6 (user_id columns)          — one migration, never want to do this later
P0.3 (signed cookies)           — auth that survives a restart
P0.4 (login rate limit)         — closes brute-force surface
─────── production-grade core, the floor ───────
P1.9 (trace-id middleware)      — substrate for everything else
P1.8 (Sentry)                   — first error you debug pays this back
P1.10 (Logfire/LangFuse)        — the agent traces start flowing
P2.15 (trace timeline UI)       — the demo asset prospects remember
P2.14 (demo mode)               — repeatable showcase, no live deps
P1.11 (worker/API compose split)
P1.12 (analytical scheduler)    — agent runs on cadence
P1.13 (type-checker)
P2.16 (landing tile)
P2.17 (doc refresh)
─────── ready to scale, ready to sell ───────
```

The first block (P0.x) is roughly 1–2 sessions of focused work. The middle
block (P1.9 → P2.14) is where the demo really starts to look like the agent
infrastructure pitch. Everything below is sweep work, easy to slot in.

## What "scale-ready" looks like at the end

- Hosted on Fly.io: two services (`worker`, `api`), one volume, one Postgres
  (or still SQLite for the demo — the migration path is a connection-string
  flip, no code change).
- Sentry catches every unhandled exception with a `trace_id`.
- Logfire/LangFuse shows every agent run as a tree of spans with costs.
- The dashboard's home page is a 10-second elevator pitch + a live link to
  an end-to-end demo run.
- Adding a real auth provider is a middleware swap — `require_auth` is the
  only seam that has to change, no domain code touches user identity.
- The database has `user_id` on every tenant-scoped row, defaulted to
  `"demo"`. Multi-tenancy is a query-filter change, not a schema migration.

That's the floor we're building to. Everything beyond — billing,
onboarding, marketing — is shell, deferred.
