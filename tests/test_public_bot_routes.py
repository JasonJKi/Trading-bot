"""Tests for /api/public/* — the unauthenticated bot tear-sheet routes.

Verifies, in priority order:
  1. None of the routes require auth (no DASHBOARD_PASSWORD cookie sent).
  2. Trades filter applies — fills younger than PUBLIC_TRADE_DELAY_DAYS are
     hidden, older ones are returned.
  3. Unknown bot id returns 404.
  4. /equity is NOT delay-filtered (only per-trade rows are).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A TestClient against a fresh sqlite DB with a few seeded rows.

    Important: drops any DASHBOARD_PASSWORD so the AUTH'd routes still work
    via auth-disabled fallback (we're not testing them here, but FastAPI's
    test client shares the app instance across the whole module).
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PUBLIC_TRADE_DELAY_DAYS", "1")
    monkeypatch.setenv("ENABLED_BOTS", "momentum,mean_reversion")
    # Reset cached settings.
    from src import config

    config._settings = None

    # Import after settings reset so the app picks up our env.
    from src.api.main import app
    from src.core.store import (
        EquitySnapshot,
        Trade,
        BotStatus,
        init_db,
        session_scope,
    )

    init_db()

    now = datetime.now(timezone.utc)
    with session_scope() as sess:
        # equity curve: 30 daily snapshots, slow uptrend
        for i in range(30):
            sess.add(
                EquitySnapshot(
                    ts=now - timedelta(days=29 - i),
                    strategy_id="momentum",
                    cash=10_000.0,
                    position_value=0.0,
                    total_equity=10_000.0 + i * 50,
                )
            )
        # trades: 3 old (>1 day), 2 fresh (<1 day) — only old ones should be public
        sess.add_all(
            [
                Trade(
                    ts=now - timedelta(days=10),
                    strategy_id="momentum",
                    symbol="AAPL",
                    side="buy",
                    qty=1.0,
                    price=180.0,
                    notional=180.0,
                    order_id="o1",
                ),
                Trade(
                    ts=now - timedelta(days=5),
                    strategy_id="momentum",
                    symbol="AAPL",
                    side="sell",
                    qty=1.0,
                    price=190.0,
                    notional=190.0,
                    order_id="o2",
                ),
                Trade(
                    ts=now - timedelta(days=2),
                    strategy_id="momentum",
                    symbol="MSFT",
                    side="buy",
                    qty=2.0,
                    price=400.0,
                    notional=800.0,
                    order_id="o3",
                ),
                # fresh — should be hidden:
                Trade(
                    ts=now - timedelta(hours=12),
                    strategy_id="momentum",
                    symbol="NVDA",
                    side="buy",
                    qty=1.0,
                    price=900.0,
                    notional=900.0,
                    order_id="o4",
                ),
                Trade(
                    ts=now - timedelta(hours=2),
                    strategy_id="momentum",
                    symbol="NVDA",
                    side="sell",
                    qty=1.0,
                    price=910.0,
                    notional=910.0,
                    order_id="o5",
                ),
            ]
        )
        sess.add(BotStatus(strategy_id="momentum", state="enabled", reason=""))

    yield TestClient(app)


def test_list_returns_200_no_auth(client):
    r = client.get("/api/public/bots")
    assert r.status_code == 200, r.text
    bots = r.json()
    ids = {b["id"] for b in bots}
    assert "momentum" in ids
    # Each entry has the required redacted fields and no leak of params/positions.
    sample = next(b for b in bots if b["id"] == "momentum")
    assert set(sample.keys()) == {
        "id",
        "name",
        "description",
        "version",
        "state",
        "total_return",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "n_trades",
    }
    # description is the public 1-liner from the strategy class — non-empty.
    assert sample["description"]


def test_detail_returns_200_no_auth(client):
    r = client.get("/api/public/bots/momentum")
    assert r.status_code == 200, r.text
    detail = r.json()
    # Detail superset includes CAGR/Sortino/expectancy/inception.
    for k in ("cagr", "sortino", "expectancy", "inception"):
        assert k in detail
    assert detail["inception"] is not None  # we seeded 30 snapshots


def test_unknown_bot_404(client):
    r = client.get("/api/public/bots/does-not-exist")
    assert r.status_code == 404


def test_trades_delay_filter_hides_fresh_fills(client):
    """3 trades older than 1d, 2 trades within 1d. Public sees only the 3."""
    r = client.get("/api/public/bots/momentum/trades")
    assert r.status_code == 200, r.text
    trades = r.json()
    # We seeded 5 trades; 2 are fresh (<1d), 3 are old. Only 3 should appear.
    assert len(trades) == 3
    symbols = {t["symbol"] for t in trades}
    # Fresh trades were on NVDA — must NOT leak.
    assert "NVDA" not in symbols
    # Old trades were on AAPL + MSFT.
    assert symbols == {"AAPL", "MSFT"}
    # Trade row shape is the redacted public version (no order_id, strategy_id).
    sample = trades[0]
    assert set(sample.keys()) == {"ts", "symbol", "side", "qty", "price", "notional"}


def test_equity_is_not_delay_filtered(client):
    """Equity curve is the headline number; we expose all 30 days of snapshots."""
    r = client.get("/api/public/bots/momentum/equity")
    assert r.status_code == 200, r.text
    points = r.json()
    assert len(points) == 30
    sample = points[0]
    # Public shape — no cash/position-value split.
    assert set(sample.keys()) == {"ts", "total_equity"}


def test_n_trades_in_listing_matches_delay_filter(client):
    """The bot listing's n_trades should reflect the public count, not total."""
    r = client.get("/api/public/bots")
    bots = r.json()
    momentum = next(b for b in bots if b["id"] == "momentum")
    # 5 total trades, 2 are fresh; public count should be 3.
    assert momentum["n_trades"] == 3
