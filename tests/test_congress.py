"""Congress adapter + bot tests using mocked HTTP and a real SQLite cache."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.data import congress
from src.bots.congress import CongressStrategy
from src.core.store import CongressDisclosure, init_db, session_scope
from src.core.strategy import StrategyContext


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("QUIVER_API_KEY", "test_key")
    from src import config
    from src.core import store

    config._settings = None
    store._engine = None
    store._SessionLocal = None
    init_db()
    yield tmp.name
    os.unlink(tmp.name)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, headers=None):
        self.calls += 1
        return _FakeResponse(self.payload)


def _quiver_row(rep, ticker, txn, when, amount):
    return {
        "Representative": rep,
        "Ticker": ticker,
        "Transaction": txn,
        "TransactionDate": when,
        "ReportDate": when,
        "Range": amount,
        "House": "House",
        "Party": "D",
    }


def test_parse_amount_band():
    assert congress._parse_amount_band("$1,001 - $15,000") == (1001.0, 15000.0)
    assert congress._parse_amount_band("$50,000") == (50000.0, 50000.0)
    assert congress._parse_amount_band("") == (0.0, 0.0)


def test_normalize_side():
    assert congress._normalize_side("Purchase") == "buy"
    assert congress._normalize_side("Sale (Full)") == "sell"
    assert congress._normalize_side("Exchange") == "exchange"


def test_fetch_recent_returns_empty_without_key(temp_db, monkeypatch):
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    from src import config

    config._settings = None
    assert congress.fetch_recent_disclosures(days=30) == []


def test_fetch_and_cache_round_trip(temp_db):
    today = datetime.now(timezone.utc).date().isoformat()
    payload = [
        _quiver_row("Nancy Pelosi", "NVDA", "Purchase", today, "$1,001 - $15,000"),
        _quiver_row("Daniel Crenshaw", "MSFT", "Purchase", today, "$15,001 - $50,000"),
        _quiver_row("Random Person", "AAPL", "Sale", today, "$1,001 - $15,000"),
    ]
    fake = _FakeClient(payload)
    rows = congress.fetch_recent_disclosures(days=30, http=fake)
    assert len(rows) == 3
    assert all(r.symbol for r in rows)

    # Now cache them.
    congress.refresh_cache.__wrapped__ if False else None  # noop; here we exercise the helper directly
    with session_scope() as sess:
        for r in rows:
            sess.add(
                CongressDisclosure(
                    external_id=r.external_id,
                    politician=r.politician,
                    chamber=r.chamber,
                    party=r.party,
                    symbol=r.symbol,
                    side=r.side,
                    amount_low=r.amount_low,
                    amount_high=r.amount_high,
                    transaction_date=r.transaction_date,
                    disclosure_date=r.disclosure_date,
                    source=r.source,
                    meta=r.meta,
                )
            )

    buys = congress.recent_buys_for(politicians=["Nancy Pelosi", "Daniel Crenshaw"], days=30)
    symbols = {b.symbol for b in buys}
    assert "NVDA" in symbols and "MSFT" in symbols
    # Random Person + AAPL filtered out by allowlist + 'sell' side.
    assert "AAPL" not in symbols


def test_strategy_emits_targets_from_cache(temp_db):
    base = datetime.now(timezone.utc) - timedelta(days=1)
    with session_scope() as sess:
        sess.add(CongressDisclosure(
            external_id="x|NVDA|1", politician="Nancy Pelosi", chamber="House", party="D",
            symbol="NVDA", side="buy", amount_low=1001, amount_high=15000,
            transaction_date=base, disclosure_date=base, source="quiver", meta={},
        ))
        sess.add(CongressDisclosure(
            external_id="x|MSFT|1", politician="Daniel Crenshaw", chamber="House", party="R",
            symbol="MSFT", side="buy", amount_low=1001, amount_high=15000,
            transaction_date=base, disclosure_date=base, source="quiver", meta={},
        ))

    bot = CongressStrategy({"min_unique_politicians": 1})
    ctx = StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={},
        bot_equity=25_000, regime="bull",
    )
    targets = bot.target_positions(ctx)
    syms = {t.symbol for t in targets}
    assert "NVDA" in syms and "MSFT" in syms
    assert all(t.weight > 0 for t in targets)


def test_strategy_idle_without_key(temp_db, monkeypatch):
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    from src import config

    config._settings = None
    bot = CongressStrategy()
    ctx = StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={},
        bot_equity=25_000, regime="bull",
    )
    assert bot.target_positions(ctx) == []
