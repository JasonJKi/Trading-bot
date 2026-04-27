# trading-bot

Multi-strategy paper-first trading bot platform with a comparative dashboard.

Runs several strategies side by side (Momentum, Mean Reversion, Congress
Copycat, News/Sentiment) on a single Alpaca paper account so you can see
which ones survive periods of high volatility before risking real money.

For the full operating manual see [`ARCHITECTURE.md`](./ARCHITECTURE.md).
For ops procedures (backups, secrets, DR) see [`OPS.md`](./OPS.md).
To wire up a new data source see [`docs/data-sources.md`](./docs/data-sources.md).

## What's in here

```
src/
  config.py                 # env-driven settings + live-trading guard
  core/
    strategy.py             # Strategy ABC (id, version, schedule, target_positions)
    broker.py               # Alpaca adapter; idempotent client_order_id; risk caps
    orchestrator.py         # runs all enabled bots, persists Orders, schedules reconciler
    reconciler.py           # polls non-terminal Orders, applies fills to BotPosition ledger
    store.py                # Trade / Order / Signal / BotPosition / BotStatus / AuditEvent
    metrics.py              # Sharpe, Sortino, drawdown, expectancy, correlation
    risk.py                 # per-bot circuit breaker + graduation gate
    alerter.py              # Slack / Discord / email / console alerting
    logging_setup.py        # rich (TTY) or JSON (prod) structured logs
  bots/
    momentum.py             # time-series momentum: EMA cross + MACD + ADX
    cross_momentum.py       # cross-sectional momentum: rank universe + vol-target sizing + regime
    mean_reversion.py       # RSI(2) + Bollinger Band
    congress.py             # placeholder (needs Quiver API key)
    sentiment.py            # placeholder (needs FinBERT install)
  data/
    bars.py                 # yfinance OHLCV fetcher
    features.py             # vol, z-score, beta, dispersion, breadth, correlation, …
  core/
    regime.py               # bull/bear/chop/crisis classifier (VIX + breadth + trend)
    allocator.py            # softmax(rolling Sharpe) dynamic per-bot capital
    sizing.py               # vol-targeted position sizing (risk-parity style)
    healthz.py              # /healthz endpoint on :8081 for uptime monitors
    backup.py               # online SQLite snapshot, gzipped, retention-aware
  backtest/
    runner.py               # walk-forward backtest using the same Strategy classes
    optimize.py             # Optuna walk-forward parameter search with overfit-gap report
  cli.py                    # run | backtest | dashboard | graduate | pause | enable | status
dashboard/
  app.py                    # Streamlit UI (read-only): live equity, positions, bot cards
scripts/run.sh              # local + container entrypoint (worker + dashboard)
tests/                      # pytest suite (no network required)
Dockerfile                  # multi-stage build, non-root user
docker-compose.yml          # local stack (sqlite default, --profile postgres for pg)
Makefile                    # `make help` for everything
fly.toml                    # production deploy config
```

## Quickstart (local — recommended for first run)

```bash
make install      # editable install with all extras
cp .env.example .env  # fill ALPACA_API_KEY / ALPACA_API_SECRET
make test         # 34 tests, no network
make run-once     # one cycle of every enabled bot
make dashboard    # http://localhost:8501
make run          # long-running scheduler
```

Or via docker-compose (one command):

```bash
make up           # SQLite, single container, dashboard on :8080
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

## Deploying to Fly.io

The bot deploys as a single Fly app running two processes in one machine:

- `python -m src.core.orchestrator` — the bot worker (no port).
- `streamlit run dashboard/app.py` — the dashboard, served on port 8080.

Both share the same SQLite file on a Fly volume. See `fly.toml` and
`scripts/run.sh`.

```bash
# 0. From a laptop with flyctl installed
fly launch --copy-config --no-deploy
# (decline any prompt to add Postgres or another HTTP service)

# 1. Create the volume (1 GB, same region as the app)
fly volumes create data --size 1 --region iad

# 2. Set Alpaca paper-trading secrets
fly secrets set ALPACA_API_KEY=PK... ALPACA_API_SECRET=...

# 3. Set a dashboard password — without this the dashboard logs a warning
#    and is open to anyone with the URL.
fly secrets set DASHBOARD_PASSWORD=$(openssl rand -hex 16)
# Show it once so you can save it in your password manager:
fly secrets list

# 4. Deploy
fly deploy
```

The dashboard will be at `https://<your-app>.fly.dev/`. The password gate uses
constant-time comparison against the `DASHBOARD_PASSWORD` secret; for stronger
auth, put Cloudflare Access in front of the same URL.

**The bot does not fit Vercel** — serverless functions can't run APScheduler,
WebSockets, or hold a SQLite file. If Fly doesn't suit you:

- **Hetzner Cloud CX22** — ~$4.50/mo, 2 vCPU / 4 GB, best price/perf.
- **Railway** — Vercel-style DX with long-running support, ~$5/mo.

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
