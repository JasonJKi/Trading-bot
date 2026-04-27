"""End-to-end-ish test using a fake broker and stub strategy. Verifies the orchestrator
persists trades + equity snapshots and respects the per-position cap."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from src.config import Settings
from src.core.broker import BrokerAdapter, OrderResult, Position
from src.core.orchestrator import Orchestrator
from src.core.store import EquitySnapshot, Trade, init_db, session_scope
from src.core.strategy import Strategy, StrategyContext, TargetPosition


class _FakeClient:
    def __init__(self):
        self.equity_value = 100_000.0
        self.positions: dict[str, Position] = {}
        self.prices = {"SPY": 500.0, "QQQ": 400.0}
        self.orders: list[OrderResult] = []
        self._order_seq = 0

    def get_account_equity(self):
        return self.equity_value

    def get_positions(self):
        return list(self.positions.values())

    def get_latest_price(self, symbol):
        return self.prices.get(symbol, 100.0)

    def submit_market_order(self, symbol, side, qty):
        self._order_seq += 1
        price = self.prices.get(symbol, 100.0)
        order = OrderResult(
            order_id=f"o{self._order_seq}", symbol=symbol, side=side, qty=qty, price=price
        )
        self.orders.append(order)
        # Update fake position book.
        cur = self.positions.get(symbol)
        cur_qty = cur.qty if cur else 0.0
        new_qty = cur_qty + qty if side == "buy" else cur_qty - qty
        if abs(new_qty) < 1e-9:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = Position(symbol, new_qty, price, new_qty * price)
        return order


class _StubStrategy(Strategy):
    id = "stub"
    name = "Stub"

    def universe(self):
        return ["SPY"]

    def target_positions(self, ctx: StrategyContext):
        return [TargetPosition(symbol="SPY", weight=0.05)]


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    # Reset cached settings + engine.
    from src import config
    from src.core import store

    config._settings = None
    store._engine = None
    store._SessionLocal = None
    init_db()
    yield tmp.name
    os.unlink(tmp.name)


def test_orchestrator_runs_one_cycle_and_persists(temp_db):
    settings = Settings(
        alpaca_paper=True,
        per_bot_cap=25_000.0,
        per_position_pct=0.05,
        enabled_bots="stub",
        database_url=f"sqlite:///{temp_db}",
    )
    fake = _FakeClient()
    broker = BrokerAdapter(client=fake, settings=settings)
    orch = Orchestrator(broker=broker, settings=settings)
    orch.bots = [_StubStrategy()]

    results = orch.run_once()

    assert len(results) == 1
    assert results[0].strategy_id == "stub"
    assert results[0].submitted >= 1
    assert len(fake.orders) >= 1

    with session_scope() as sess:
        trades = sess.execute(select(Trade)).scalars().all()
        snaps = sess.execute(select(EquitySnapshot)).scalars().all()
    assert len(trades) >= 1
    assert all(t.strategy_id == "stub" for t in trades)
    assert len(snaps) >= 1


def test_per_position_cap_enforced(temp_db):
    """A target weight that would exceed the per-position cap is trimmed by BrokerAdapter."""
    settings = Settings(
        alpaca_paper=True,
        per_bot_cap=25_000.0,
        per_position_pct=0.05,  # 5% of $25k = $1,250 per position
        enabled_bots="stub",
        database_url=f"sqlite:///{temp_db}",
    )
    fake = _FakeClient()
    fake.prices["SPY"] = 100.0  # pick a clean price
    broker = BrokerAdapter(client=fake, settings=settings)

    # Try to buy $5k of SPY through the adapter — cap is $1,250.
    res = broker.submit("SPY", "buy", qty=50.0, bot_allocation=settings.per_bot_cap)
    assert res is not None
    assert res.qty * fake.prices["SPY"] <= settings.per_bot_cap * settings.per_position_pct + 1e-6
