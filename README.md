# trading-bot

Multi-strategy paper-first trading bot platform with a comparative dashboard.

Runs several strategies side by side (Momentum, Mean Reversion, Congress
Copycat, News/Sentiment) on a single Alpaca paper account so you can see
which ones survive periods of high volatility before risking real money.

## What's in here

```
src/
  config.py                 # env-driven settings + live-trading guard
  core/
    strategy.py             # Strategy ABC + dataclasses
    broker.py               # Alpaca adapter, enforces per-position cap
    orchestrator.py         # runs all enabled bots, persists trades + equity
    store.py                # SQLAlchemy models (Trade, EquitySnapshot, Signal)
    metrics.py              # Sharpe, Sortino, drawdown, expectancy, etc.
  bots/
    momentum.py             # EMA cross + MACD + ADX
    mean_reversion.py       # RSI(2) + Bollinger Band
    congress.py             # placeholder (needs Quiver API key)
    sentiment.py            # placeholder (needs FinBERT install)
  data/bars.py              # yfinance OHLCV fetcher
  backtest/runner.py        # walk-forward backtest using the same strategies
  cli.py                    # `trading-bot run|backtest|dashboard`
dashboard/
  app.py                    # Streamlit UI (read-only)
tests/                      # pytest suite (no network required)
```

## Quickstart

```bash
# 1. Install (Python 3.11+)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Fill ALPACA_API_KEY / ALPACA_API_SECRET from https://app.alpaca.markets/paper/dashboard/overview

# 3. Run all enabled bots once (paper)
python -m src.core.orchestrator --once

# 4. Open the dashboard
streamlit run dashboard/app.py

# 5. Run as a daemon (APScheduler with each bot's cron)
python -m src.core.orchestrator
```

## Tests

```bash
pytest -q
```

20 tests cover metrics, indicator math, the live-mode guard, and an
end-to-end orchestrator run with a fake broker. None touch the network.

## Going live (don't, until you've earned it)

Live trading is gated by **two** environment variables:

```bash
ALPACA_PAPER=false
ALPACA_LIVE_CONFIRM=YES_I_MEAN_IT
```

Setting only the first one will refuse to start. A strategy is ready for
real money when:

- ≥ 30 calendar days of paper trading,
- live Sharpe > 1.0 and within ±20% of backtested Sharpe,
- max drawdown ≤ backtested max DD × 1.25,
- no code changes in the last 7 days.

## Risk controls (always on)

| Limit | Default | Where |
| --- | --- | --- |
| Per-position notional | 5% of bot allocation | `BrokerAdapter.submit` |
| Per-bot capital cap | $25,000 | orchestrator |
| Global drawdown halt | 10% from starting equity | orchestrator |
| Live-mode confirm token | required | `Settings.assert_safe_to_trade` |

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
