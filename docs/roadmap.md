# Roadmap

Phase-organized plan. Each phase is a coherent slice that ends with the
codebase visibly better against the engineering bar in
[`production-baseline.md`](./production-baseline.md). Check items off as
you ship them.

> Living doc. If you ship something that wasn't here, *add it
> retroactively* — the roadmap should always reflect what's in the
> codebase. If a phase becomes irrelevant, strike it through, don't
> delete (history matters).

## Phase 18 — Dashboard + research agent  (CURRENT, mostly done)

Replace the legacy Streamlit UI with a real web stack and add a
research-mining subsystem.

- [x] FastAPI + Next.js dashboard (replaces Streamlit)
- [x] Single-container prod build (FastAPI serves Next.js static export)
- [x] Three-agent PydanticAI research pipeline backed by Gemini
- [x] Source adapters: Reddit, arXiv, GitHub, HackerNews, YouTube, web (Tavily)
- [x] Strategy template library (`MA cross`, `MR-RSI`, `momentum-zscore`, `vol-breakout`)
- [x] Laptop-driven deploy to remote Mac mini
- [x] Delete Streamlit residue (`cli.py::dashboard`, `dashboard*` package include)
- [x] Refresh `ARCHITECTURE.md` for the Next.js + FastAPI dashboard (drop `:8080` Streamlit references)

## Phase 19 — Production baseline  (P0 from `production-baseline.md`)

Close the engineering-bar gaps that prevent us calling the core
"production-grade." Each item is a small, independent PR.

- [x] Alembic migrations (initial revision = current schema; `make db-upgrade`)
- [x] FastAPI `lifespan` context manager + global exception handler (replaces deprecated `on_event`)
- [x] Persistent signed session cookie (replaces in-memory `_session_token`)
- [x] Login rate limit on `POST /api/auth/login` (5 / 5min / IP)
- [x] `Settings.validate_for_runtime()` — fail loud on missing alert channels in live mode
- [x] `user_id` column on every tenant-scoped table (default `"demo"`)

**Done when:** the codebase passes its own bar audit at the P0 tier and
`production-baseline.md` is updated with a new "as of" date.

## Phase 20 — Observability + agent traces  (the demo-asset phase)

Make the system inspectable. This is where the codebase starts to *look
like* the agent-infrastructure pitch.

- [ ] Trace-id middleware (per-request UUID, contextvar, on every log line)
- [ ] Sentry wired (worker, API, agent; DSN from env; no-op when unset)
- [ ] Logfire / LangFuse on every PydanticAI call, tool call, and synthesis pass
- [ ] Agent-trace timeline UI in the dashboard's research detail page
- [ ] Demo mode (`DEMO_MODE=true`) — deterministic seeded dataset (a fake research run, a backtested spec, a paper bot with a week of snapshots)

**Done when:** a prospect can click into a research run and see every
step the agents took, with timings and costs, in 60 seconds, end-to-end,
without depending on a live LLM call or live broker data.

## Phase 21 — Worker / API split + analytical scheduler

Make the two-schedulers principle real in process boundaries, not just
on paper.

- [ ] `docker-compose.yml` grows separate `worker` and `api` services sharing a volume
- [ ] `src/research/scheduler.py` — APScheduler in its own process
- [ ] Research runs queued from the API, executed by the analytical scheduler
- [ ] Type-checker (pyright) added to CI

**Done when:** the trading scheduler, the analytical scheduler, and the
API run as three independent processes, communicating only through the
DB, with no shared imports at runtime.

## Phase 22 — Synthesis agent + per-bot analyst

Close the research → deploy loop. This is where the agent layer starts
producing real strategies.

- [ ] L2 Synthesis agent: `ResearchFinding` → `StrategySpec` (validated against `ParamSpec`)
- [ ] L3 Backtest upgrades: slippage model, deflated Sharpe, regime-stratified evaluation
- [ ] L5 Per-bot analyst: nightly `HealthReport`
- [ ] Eval registry: track which agent versions produced which outputs

**Done when:** a research finding can graduate to a paper-trading
strategy without manual code, with a backtest gate refusing the unworthy.

## Phase 23 — Portfolio + governance

The meta-layer: the system makes recommendations about itself.

- [ ] L6 Portfolio Agent: daily `AllocationDecision`, correlation-aware
- [ ] L0 CIO Agent: weekly meta-actions (queues research topics, flags stale strategies)
- [ ] Cross-bot correlation in the allocator
- [ ] Per-agent / per-LLM-call cost accounting

## Phase 24 — Public bot tear sheets  (the marketing surface)

Make each bot's performance publicly viewable without exposing strategy
mechanics. A three-tier URL layout under `67quant.com`:

| URL | Audience | Auth |
|---|---|---|
| `67quant.com` | marketing landing | public, no auth |
| `app.67quant.com` | full operator dashboard | password-gated (current dashboard) |
| `bot.67quant.com/{id}` | public per-bot tear sheet | public, read-only |

The line we draw: **show track records, hide playbooks.**

| Public (no auth) | Private (auth) |
|---|---|
| Equity curve since inception | Current positions |
| Total return / Sharpe / max DD | Live signals (pre-execution) |
| Trade count, win rate | Strategy parameters / thresholds |
| Trades **delayed ≥ 1 trading day** | Specific tickers held right now |
| 1-line strategy description | Allocation knobs / risk caps |

The trade-delay is the load-bearing knob — a copyable real-time feed would
defeat the purpose; a T+1 historical record is transparent without leaking
edge.

- [x] DNS + Cloudflare Tunnel ingress for `app.67quant.com` (alongside `bot.`)
      — apex (`67quant.com`) added too, with `/` → `/welcome` redirect
- [x] `src/api/public_bot_routes.py` mounted at `/api/public/*`:
      `GET /api/public/bots`, `/api/public/bots/{id}`,
      `/api/public/bots/{id}/equity`, `/api/public/bots/{id}/trades`
- [x] `PUBLIC_TRADE_DELAY_DAYS` env knob (default `1`); applied as a SQL filter
      on every public trade query
- [x] `Strategy.description` field for public 1-liner; populated on each bot
- [ ] Next.js public route tree at `/public/bots/[id]` with hostname-aware
      layout (no auth chrome when `Host: bot.67quant.com`)
- [ ] Cut `bot.67quant.com` over to serve only public pages once UI is ready
- [ ] Update `system-architecture.md` "Trust boundary" section with the
      public/private API boundary
- [ ] Marketing landing at apex (`67quant.com`) — last; can be a separate
      static site if the Next.js app gets heavier

**Done when:** sharing `bot.67quant.com/momentum` in public chat shows a clean
tear sheet, and someone reading it cannot reverse-engineer the strategy
parameters or see live positions.

**Depends on:** Phase 19 P0 items — public endpoints are unauthenticated but
the auth wall around `app.67quant.com` (persistent session, login rate limit)
must be solid first.

## Future — Productization (not on the immediate path)

Captured here so we don't accidentally build it before the foundations
are ready, and so the door isn't closed.

- Crypto track (Freqtrade behind `BrokerAdapter`)
- Friends-and-family pool: per-LP equity tracking
- Real auth provider (replaces password gate via the existing middleware seam)
- Hosted-service shell: tenancy, billing, onboarding
- Public API for signal/research consumers

## How this doc is maintained

- Each phase ends with a tag (`phase-19-production-baseline`, etc.) and
  a short summary in the merge commit.
- When you ship something not on the roadmap, *add it retroactively* —
  the roadmap should always reflect what's in the codebase.
- When a phase becomes irrelevant, strike it through; don't delete.
- Major direction changes (e.g., the demo-vs-SaaS pivot in April 2026)
  update [`overview.md`](./overview.md) too.
- Don't move items between phases without leaving a note. Reordering is
  fine, silent reordering loses signal.
