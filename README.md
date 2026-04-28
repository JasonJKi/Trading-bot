# trading-bot

Multi-strategy paper-first trading bot platform with a comparative dashboard.

Runs several strategies side by side (Momentum, Mean Reversion, Congress
Copycat, News/Sentiment) on a single Alpaca paper account so you can see
which ones survive periods of high volatility before risking real money.

For the full doc set (overview, system design, roadmap, audit) start at
[`docs/`](./docs/README.md). Quick links:
- [`docs/overview.md`](./docs/overview.md) — what this is, in 5 minutes
- [`docs/system-architecture.md`](./docs/system-architecture.md) — north-star design
- [`docs/production-baseline.md`](./docs/production-baseline.md) — engineering bar + current audit
- [`docs/roadmap.md`](./docs/roadmap.md) — what's being built next
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — how the running system works today
- [`OPS.md`](./OPS.md) — runbook (backups, secrets, DR)
- [`docs/data-sources.md`](./docs/data-sources.md) — adding a new data source

## What's in here

```
src/
  config.py                 # env-driven settings + live-trading guard
  core/                     # orchestrator, strategy ABC, broker, store, metrics, risk, regime, …
  bots/                     # momentum, cross_momentum, mean_reversion, congress, sentiment
  data/                     # bars (yfinance), features, congress, news adapters
  api/                      # FastAPI dashboard backend — read-only over the SQLite store
  backtest/                 # walk-forward runner + Optuna optimizer
  cli.py                    # run | backtest | graduate | pause | enable | status
web/                        # Next.js 15 + Tailwind dashboard (consumes src/api)
scripts/run.sh              # one-shot: orchestrator + FastAPI + Next.js dev server
tests/                      # pytest suite (no network required)
Dockerfile                  # worker + API image (Next.js ships separately)
docker-compose.yml          # local stack (sqlite default, --profile postgres for pg)
Makefile                    # `make help` for everything
fly.toml                    # production deploy config (worker + API only)
```

## Quickstart (local — recommended for first run)

You'll need **Python ≥ 3.11** and **Node ≥ 20**.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make install            # editable install (Python deps)
cd web && npm install   # JS deps for the dashboard
cd ..

cp .env.example .env    # fill ALPACA_API_KEY / ALPACA_API_SECRET
make test               # pytest suite, no network
make run-once           # one cycle of every enabled bot

make run                # full stack: worker + api + dashboard
# → orchestrator runs in the background
# → http://localhost:8000  FastAPI (OpenAPI docs at /docs)
# → http://localhost:3000  Next.js dashboard
```

Or via docker-compose (one command):

```bash
make up           # SQLite, single container, dashboard on :8000
make up-pg        # adds Postgres profile for a real DB
```

## Tests

```bash
pytest -q
```

34 tests cover metrics, indicator math, the live-mode guard, the
order pipeline (idempotency, in-flight guard, reconciliation), the
alerter (fan-out, fault-tolerance), and the risk module (circuit
breaker, graduation gate). No network required.

## Going live (don't, until you've earned it)

Live trading is gated by **three** layers:

1. **Two env vars** must both be set:
   ```
   ALPACA_PAPER=false
   ALPACA_LIVE_CONFIRM=YES_I_MEAN_IT
   ```
2. **Every enabled bot must be paper-validated.** Run:
   ```
   python -m src.cli graduate --strategy momentum
   ```
   The gate refuses to flip `paper_validated_at` unless the bot has ≥ 30
   days of equity snapshots and a Sharpe ≥ 1.0.
3. **At startup**, the orchestrator double-checks every enabled bot has
   been graduated. If any haven't, it refuses to run.

## Risk controls (always on)

| Limit | Default | Where |
| --- | --- | --- |
| Per-position notional | 5% of bot allocation | `BrokerAdapter.submit` |
| Per-bot capital cap | $25,000 | orchestrator |
| Per-bot 30-day DD halt | 15% (auto-pauses bot) | `risk.evaluate_circuit_breaker` |
| Global drawdown halt | 10% from starting equity | orchestrator |
| Live-mode confirm token | required | `Settings.assert_safe_to_trade` |
| Paper-validated graduation | required for live | `risk.assert_all_paper_validated` |
| Idempotent order submission | client_order_id | `BrokerAdapter.make_client_order_id` |
| In-flight order guard | open `Order` row blocks resubmit | `Orchestrator._submit_intent` |

## Deploying

Two pieces deploy separately:

- **Worker + API** (`src/`) — long-running, holds SQLite, runs APScheduler. Needs Fly /
  Railway / Hetzner / any always-on host. The Dockerfile here is for this piece.
- **Dashboard** (`web/`) — stateless Next.js. Vercel, Netlify, or another Fly app.
  Set `NEXT_PUBLIC_API_URL` in the dashboard's env to point at the worker's URL.

For the worker on Fly:

```bash
fly launch --copy-config --no-deploy   # decline Postgres prompt
fly volumes create data --size 1 --region iad
fly secrets set ALPACA_API_KEY=PK... ALPACA_API_SECRET=...
fly secrets set DASHBOARD_PASSWORD=$(openssl rand -hex 16)
fly deploy
```

**The worker doesn't fit Vercel** — serverless functions can't run APScheduler
or hold a SQLite file. The dashboard fits Vercel perfectly.

## Backtesting

```bash
python -m src.backtest.runner --strategy momentum --start 2024-01-01 --end 2025-12-31
```

The backtest reuses the same `Strategy` subclass as the live orchestrator,
so the research → production gap is zero.

## Adding a new strategy

1. Create `src/bots/my_bot.py` with a class inheriting `Strategy`.
2. Implement `universe()` and `target_positions(ctx)`.
3. Register it in `src/core/orchestrator.py::load_enabled_bots`.
4. Add it to `ENABLED_BOTS` in `.env`.

That's it — backtest, live execution, and dashboard pick it up automatically.
