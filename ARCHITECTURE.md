# Architecture

This document is the operating manual. If something breaks at 4 AM, this is
where you start.

## High-level layout

```
┌─────────────────────────────────────────────────────────────┐
│  One process, many bots, one shared SQLite file             │
│                                                             │
│  src/core/orchestrator.py                                   │
│      │                                                      │
│      ├── APScheduler (BlockingScheduler, UTC)               │
│      │     ├─ cron job per bot           (Strategy.schedule)│
│      │     └─ interval job: reconciler   (every 30s)        │
│      │                                                      │
│      ├── BrokerAdapter ─────► Alpaca (paper or live)        │
│      │      enforces per-position cap                       │
│      │      generates idempotent client_order_id            │
│      │                                                      │
│      ├── Strategy.target_positions(ctx) (one per bot)       │
│      │                                                      │
│      └── Reconciler                                         │
│             polls non-terminal Orders                       │
│             writes Trade rows on fill                       │
│             updates BotPosition ledger                      │
│             writes AuditEvents                              │
│                                                             │
│  src/api/main.py (FastAPI on :8000)                         │
│      Pure reader. Reads SQLite + makes read-only Alpaca     │
│      calls. Never moves money. Password-gated.              │
│      In prod: also serves the Next.js static export at /.   │
└─────────────────────────────────────────────────────────────┘
```

## Order pipeline

The "intent vs fill" separation is the most important invariant in this codebase.

```
Strategy emits a TargetPosition
            │
            ▼
Orchestrator computes delta vs BotPosition ledger
            │  (skip if open Order already exists for this strategy+symbol)
            ▼
Persist Order row in 'new' state with deterministic client_order_id
            │  (intent survives a crash here)
            ▼
BrokerAdapter.submit() — risk caps applied, broker called
            │
            ▼
Update Order row: broker_order_id stamped, status = 'accepted'
            │  (we never write 'filled' here — reconciler is the SOLE source of truth)
            ▼
Reconciler (every 30s) polls broker for non-terminal Orders
            │
            ▼
On any new fill:
   • write Trade row
   • update BotPosition (avg price, qty, cost basis)
   • write AuditEvent kind=fill
On rejection:
   • write AuditEvent + fire 'error' alert
```

This guarantees:
- **No double-submits.** Idempotent `client_order_id` + in-flight guard.
- **No silent slippage.** Every fill is observed and recorded with its real price.
- **Crash-safe.** Order intent persists before the broker call; on restart, the
  reconciler picks up where we left off.

## Tables

| Table | Purpose | Mutability |
|---|---|---|
| `signals` | Every signal a bot emits, acted on or not. | Append-only. |
| `orders` | Every submitted order with reconciled status + fill info. | Status updated by reconciler; rows never deleted. |
| `trades` | Each *fill* (one Order can produce multiple). | Append-only. |
| `bot_positions` | Per-bot ledger keyed on `(strategy_id, symbol)`. | Updated by reconciler. Row deleted when qty hits zero. |
| `equity_snapshots` | Per-bot equity at end of cycle. | Append-only. |
| `bot_status` | enabled / paused / disabled + paper-validated timestamp. | Updated by circuit breaker, graduation gate, CLI. |
| `audit_events` | "What happened and why." Append-only event log. | **Never updated, never deleted.** |

## Risk controls

| Control | Defined in | Default |
|---|---|---|
| Per-position cap | `BrokerAdapter.submit` | 5% of bot allocation |
| Per-bot capital cap | settings | $25,000 (overridden by allocator) |
| Dynamic per-bot capital | `allocator.allocate` | softmax(30d Sharpe), 5–50% bounds |
| Vol-targeted sizing | `sizing.vol_target_qty` | optional, target=15% annual |
| Per-bot rolling 30-day DD | `risk.evaluate_circuit_breaker` | 15% |
| Global drawdown halt | `Orchestrator._global_drawdown_breached` | 10% |
| Live-mode token | `Settings.assert_safe_to_trade` | required |
| Graduation gate | `risk.assert_all_paper_validated` | 30d + Sharpe ≥ 1.0 |
| Walk-forward param robustness | `backtest.optimize.WalkForwardResult.robust` | OOS Sharpe ≥ 0.5, gap ≤ 1.0 |
| Regime stand-down | `Strategy` reads `ctx.regime` | crisis → flat |

## Failure modes & alerts

| Event | Severity | Channel |
|---|---|---|
| Orchestrator startup | info | all configured |
| Order submit failed | error | all configured |
| Order rejected (post-reconcile) | error | all configured |
| Bot circuit breaker tripped | error | all configured |
| Global DD halt | critical | all configured |
| Bot graduated | info | all configured |

Channels: console (always), Slack, Discord, email (SMTP). Configure via env.

## Local development

```
make install     # editable install with all extras
make test        # 34 tests, no network needed
make run-once    # run every enabled bot once and exit
make run         # full stack: worker + FastAPI + Next.js dev server

# Docker (one command):
make up          # SQLite by default — dashboard at http://localhost:8000
make up-pg       # add Postgres profile
```

## Deployment topology

The same code runs on three deployment targets, with increasing operational
rigor:

| Target | Process layout | Public URL | Use |
|---|---|---|---|
| Local / docker compose | one host, no tunnel | `http://localhost:8000` | development |
| Fly.io | one machine, one volume (`scripts/run.sh`) | `*.fly.dev` | demo / cloud |
| Mac mini (primary) | three `launchd` agents + Cloudflare named tunnel | `app.67quant.com`, `bot.67quant.com` | always-on home server |

### Mac mini layout

Three independent LaunchAgents under `~/Library/LaunchAgents/`. Templates
live in [`deploy/launchd/`](./deploy/launchd/) with `__APP_DIR__` /
`__API_PORT__` / `__HOME__` placeholders rendered by
[`deploy/services-install.sh`](./deploy/services-install.sh).

| Label | What it runs | Purpose |
|---|---|---|
| `com.tradingbot.orchestrator` | `python -m src.core.orchestrator` | trading scheduler, all bots, reconciler |
| `com.tradingbot.api` | `uvicorn src.api.main:app` (loopback) | FastAPI backend; also serves the Next.js static export |
| `com.tradingbot.tunnel` | `cloudflared tunnel run` (named tunnel `trading-bot`) | brings public traffic in via Cloudflare |

All three have `KeepAlive=true` (auto-restart on crash) and `RunAtLoad=true`
(start at login). Server-side state:

| Path | Contents | Lifecycle |
|---|---|---|
| `~/Trading-bot/` | full app tree | rsync'd from laptop on `make mac-deploy` |
| `~/Trading-bot/data/trading.db` | SQLite DB | host volume; `data/` is excluded from rsync, never overwritten |
| `~/Trading-bot/logs/{orchestrator,api,tunnel}.{out,err}.log` | launchd-captured stdout/err | append, host volume |
| `~/Trading-bot/.env` | secrets | excluded from rsync; pushed once via `make mac-env-push` |
| `~/.cloudflared/{cert.pem,<uuid>.json,config.yml}` | tunnel credentials + ingress | scp'd via `make mac-tunnel-creds-push`; tracked as a template at [`deploy/cloudflared/config.yml.template`](./deploy/cloudflared/config.yml.template) |

The dashboard build happens on the laptop (`NEXT_BUILD_MODE=export npm run
build` → `web/out/`); only the artifact ships. The server has no Node
toolchain, no editable Python install, and no source build hooks — Python
deps are pinned in [`deploy/requirements.txt`](./deploy/requirements.txt) and
installed as a "simple declared-dependency install."

The full workflow (one-time setup, day-to-day, named-tunnel upgrade) lives in
[`deploy/README.md`](./deploy/README.md). Runbook commands are in
[`OPS.md`](./OPS.md).

### URL layout

Per Phase 24 in [`docs/roadmap.md`](./docs/roadmap.md):

| Hostname | Today | Target |
|---|---|---|
| `67quant.com` | (none — DNS only) | marketing landing |
| `app.67quant.com` | full operator dashboard, password-gated | same |
| `bot.67quant.com` | mirrors `app.` (placeholder) | public per-bot tear sheets, read-only |

All three resolve to the same Cloudflare tunnel and the same uvicorn process
on the Mac mini; FastAPI / Next.js will branch on the request `Host` header
once the public UI ships. The ingress rules live in
[`deploy/cloudflared/config.yml.template`](./deploy/cloudflared/config.yml.template).

### Cloud (Fly.io)

The same image still runs on Fly for cloud demos. See `fly.toml` and
`scripts/run.sh`. The worker (orchestrator + reconciler) and the FastAPI
backend share one host and one SQLite volume.

```
make fly-deploy
make fly-logs
make fly-ssh
```

## Going live (the only acceptable path)

1. Run paper for ≥ 30 days with all alerts configured.
2. `python -m src.cli status` — every enabled bot must show a non-trivial
   `days` count and a Sharpe within range.
3. `python -m src.cli graduate --strategy <id>` per bot. Fails loudly if
   the gate isn't met.
4. Set `ALPACA_PAPER=false` AND `ALPACA_LIVE_CONFIRM=YES_I_MEAN_IT`.
5. Start with **5–10% of intended capital**. The orchestrator refuses to
   start live if any enabled bot has not been graduated.
6. Watch for a month. Tier-up only on evidence.

## Where to look when things break

| Symptom | First place to look |
|---|---|
| Bot did nothing today | `audit_events` (kind=`broker_error` or `bot_error`) |
| Trade looks wrong | `orders` row → `trades` rows (status, filled_qty, error) |
| "Where did this position come from?" | `bot_positions` joined with recent `trades` |
| Surprise drawdown | `equity_snapshots` per bot + dashboard "Drawdown" chart |
| Circuit breaker tripped | `bot_status.reason` + most recent `audit_events` |
| Money disappeared | `audit_events` for the day, then Alpaca order history |
