# Wiring up a new data source

This codebase has a consistent pattern for external data: **adapter → cache → strategy**. Every external source — bars, congressional disclosures, news, alt-data — follows the same shape.

## The three layers

```
┌──────────────────────────────────────────────────────────┐
│ 1. Adapter         src/data/<source>.py                  │
│    - Talks to ONE specific API                           │
│    - Pure I/O: fetch_*() -> list[Row]                    │
│    - Degrades to [] without creds                        │
│    - All HTTP done with httpx for testability            │
│                                                          │
│ 2. Cache           model in src/core/store.py            │
│    - external_id-keyed for idempotent upsert             │
│    - Indexed for fast strategy reads                     │
│    - Refreshed by a scheduled job, not by the strategy   │
│                                                          │
│ 3. Strategy        src/bots/<bot>.py                     │
│    - target_positions() reads ONLY from cache            │
│    - Never blocks on a network call                      │
│    - Stamped with strategy.version on every signal       │
└──────────────────────────────────────────────────────────┘
```

The split is the point: strategy code stays fast and reproducible from the DB; adapter code is exercised against mocked HTTP; the orchestrator schedules the refresh job independently from the strategy's own cron.

## Worked examples in this codebase

| Source | Adapter | Cache table | Strategy | Refresh schedule |
|---|---|---|---|---|
| Quiver Congressional trades | `src/data/congress.py` | `CongressDisclosure` | `src/bots/congress.py` | every 1h |
| Alpaca News + FinBERT | `src/data/news.py` + `src/data/sentiment.py` | `NewsItem` | `src/bots/sentiment.py` | every 5m (fetch + score) |
| OHLCV bars | `src/data/bars.py` | (no cache — pulled per cycle) | every bot | per cycle |

Read those for canonical examples. The Congress one is the cleanest template.

## Adding a new source — recipe

Say you want to wire up **options-flow alerts** (e.g. unusual options activity from a paid feed).

### Step 1 — adapter

`src/data/options_flow.py`:

```python
from dataclasses import dataclass
from datetime import datetime
import httpx
from src.config import get_settings

@dataclass(slots=True)
class FlowRow:
    external_id: str
    ts: datetime
    symbol: str
    contract: str        # e.g. "AAPL250620C00200000"
    side: str            # "call" / "put"
    sweep_size: float
    notional: float
    direction: str       # "buy" / "sell"
    meta: dict

def fetch_recent(hours: int = 4, http: httpx.Client | None = None) -> list[FlowRow]:
    settings = get_settings()
    if not settings.options_flow_api_key:
        return []
    # ... call your provider's API ...
    # ... map response into FlowRow objects ...
    return rows
```

Rules:
- Returns `[]` when creds are missing. Never raises in the no-creds case.
- HTTP via `httpx.Client` so tests can pass in a fake.
- Each row has a stable `external_id` for upsert keying.
- Every dataclass field is the minimum the strategy actually needs.

### Step 2 — cache table

In `src/core/store.py`, add the model:

```python
class OptionsFlowEvent(Base):
    __tablename__ = "options_flow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    external_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    contract: Mapped[str] = mapped_column(String(48))
    side: Mapped[str] = mapped_column(String(8))
    sweep_size: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
```

Key column to add: a unique `external_id` so upsert is one line.

Then back in `src/data/options_flow.py`, the upsert helper:

```python
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from src.core.store import OptionsFlowEvent, session_scope

def refresh_cache(hours: int = 4) -> int:
    rows = fetch_recent(hours=hours)
    with session_scope() as sess:
        for r in rows:
            stmt = sqlite_insert(OptionsFlowEvent).values(
                external_id=r.external_id, ts=r.ts, symbol=r.symbol, ...
            ).on_conflict_do_nothing(index_elements=["external_id"])
            sess.execute(stmt)
    return len(rows)
```

### Step 3 — strategy

`src/bots/options_flow_copycat.py`:

```python
from sqlalchemy import select, func
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.core.store import OptionsFlowEvent, session_scope

class OptionsFlowCopycat(Strategy):
    id = "options_flow"
    name = "Options Flow Copycat"
    version = "0.1"
    schedule = {"hour": "13-21", "minute": "*/30"}  # US session, 30min cadence

    def universe(self):
        return []  # universe comes from whatever shows up in flow

    def target_positions(self, ctx):
        # Aggregate cached events: for each symbol, sum bullish call sweeps
        # in the last 4h. Long if total > $X and clearly bullish.
        with session_scope() as sess:
            rows = list(sess.execute(
                select(OptionsFlowEvent)
                .where(OptionsFlowEvent.ts >= cutoff)
            ).scalars())
        # ... rank, filter, build targets ...
        return targets
```

Important:
- `target_positions` only reads from the cache. No HTTP.
- Strategy's `version` bumps when the *signal logic* changes; that gets stamped on every Order/Trade/Signal so historical replay stays meaningful.

### Step 4 — register the bot

In `src/core/orchestrator.py::load_enabled_bots`, add the strategy class:

```python
from src.bots.options_flow_copycat import OptionsFlowCopycat
registry["options_flow"] = OptionsFlowCopycat
```

And in `main()`, schedule the refresh job alongside the others:

```python
if "options_flow" in bot_ids:
    from src.data.options_flow import refresh_cache as refresh_flow
    sched.add_job(refresh_flow, IntervalTrigger(minutes=15),
                  id="refresh_options_flow", replace_existing=True)
```

### Step 5 — config

`src/config.py`:

```python
class Settings(BaseSettings):
    ...
    options_flow_api_key: str = ""
```

`.env.example`:

```
OPTIONS_FLOW_API_KEY=
```

### Step 6 — tests

`tests/test_options_flow.py`:

```python
class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
    def get(self, url, **kwargs):
        class R:
            def raise_for_status(self): return None
            def json(self): return self.payload
        return R()

def test_no_creds_returns_empty(temp_db, monkeypatch):
    monkeypatch.delenv("OPTIONS_FLOW_API_KEY", raising=False)
    assert options_flow.fetch_recent() == []

def test_round_trip(temp_db):
    monkeypatch.setenv("OPTIONS_FLOW_API_KEY", "x")
    fake = _FakeClient({"events": [...]})
    rows = options_flow.fetch_recent(http=fake)
    assert len(rows) == ...
```

Same `temp_db` fixture pattern as the Congress and News tests use.

That's the whole loop. Three modules + one config knob + one schedule line.

## Tips & gotchas

| | |
|---|---|
| **Cache freshness** | Refresh at the cadence the data actually changes. Congress disclosures are reported up to 45 days late, so 1h is plenty. Options flow is intra-second relevant, so 5–15 min is the floor at retail-data quality. |
| **Backoff** | If your provider rate-limits, wrap the httpx call in `tenacity` with exponential backoff. The Quiver adapter is single-shot because the endpoint is small; high-volume sources should retry. |
| **PII / sensitive data** | The `meta` JSON column is for adapter-specific extras. Don't put credentials, user identifiers, or anything you wouldn't paste in a Slack channel. |
| **Adapter degradation** | If the API call fails, log the exception and return `[]`. Don't raise. Strategy should treat "no data" identically to "no signal". |
| **Idempotent upsert** | Always use `on_conflict_do_nothing` (or `on_conflict_do_update` if you need to refresh fields). A re-fetch should not create duplicate rows. |
| **Time zones** | Persist UTC. SQLite stores naive datetimes; if you compare aware datetimes against a SQLite column, strip tz first (see `src/data/sentiment.py::rolling_sentiment` for the pattern). |
| **Strategy versioning** | Bump `Strategy.version` whenever signal logic changes. This is stamped on every signal/order/trade so old rows stay reproducible. |

## Choosing the right data source

| What you actually want | Reasonable provider | Cost |
|---|---|---|
| Congressional trades | Quiver Quantitative | $10/mo |
| News headlines | Alpaca News (already free with broker) | $0 |
| Sentiment on those headlines | FinBERT (HF, runs on CPU) | $0 |
| Earnings calendar | Finnhub free tier | $0 |
| Options flow / unusual activity | Cheddar Flow / Unusual Whales / Quiver | $30–80/mo |
| Insider transactions (Form 4) | SEC EDGAR (free) or Quiver | $0–$10/mo |
| Short interest | NASDAQ Data Link | $50/mo+ |
| Alternative data (web scrapes, app downloads, etc) | Various; expensive | $$$$ |
| Real-time minute bars | Alpaca paid feed | $9/mo |

Pick what your strategy demonstrably needs after backtesting. Don't pre-buy data for hypotheticals.
