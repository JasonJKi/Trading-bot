"""Orchestrator integration test with a fake broker.

Exercises the v2 order pipeline: signal -> Order row -> broker submit ->
reconciler picks up fills -> Trade row + BotPosition update.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import select

from src.config import Settings
from src.core.broker import BrokerAdapter, OrderResult, Position
from src.core.orchestrator import Orchestrator
from src.core.reconciler import reconcile_open_orders
from src.core.store import BotPosition, EquitySnapshot, Order, Trade, init_db, session_scope
from src.core.strategy import Strategy, StrategyContext, TargetPosition


class _FakeClient:
    def __init__(self):
        self.equity_value = 100_000.0
        self.positions: dict[str, Position] = {}
        self.prices = {"SPY": 500.0, "QQQ": 400.0}
        self.orders_by_client_id: dict[str, OrderResult] = {}
        self._broker_seq = 0

    def get_account_equity(self):
        return self.equity_value

    def get_positions(self):
        return list(self.positions.values())

    def get_latest_price(self, symbol):
        return self.prices.get(symbol, 100.0)

    def submit_market_order(self, symbol, side, qty, client_order_id):
        self._broker_seq += 1
        price = self.prices.get(symbol, 100.0)
        # Simulate immediate fill — paper Alpaca behaves this way for liquid names.
        order = OrderResult(
            order_id=f"o{self._broker_seq}",
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            status="filled",
            filled_qty=qty,
        )
        self.orders_by_client_id[client_order_id] = order
        cur = self.positions.get(symbol)
        cur_qty = cur.qty if cur else 0.0
        new_qty = cur_qty + qty if side == "buy" else cur_qty - qty
        if abs(new_qty) < 1e-9:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = Position(symbol, new_qty, price, new_qty * price)
        return order

    def get_order_by_client_id(self, client_order_id):
        return self.orders_by_client_id.get(client_order_id)


class _StubStrategy(Strategy):
    id = "stub"
    name = "Stub"
    version = "test"

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
    assert results[0].submitted >= 1

    # Reconciler turns the filled Order into a Trade + BotPosition.
    reconcile_open_orders(broker)

    with session_scope() as sess:
        orders = sess.execute(select(Order)).scalars().all()
        trades = sess.execute(select(Trade)).scalars().all()
        positions = sess.execute(select(BotPosition)).scalars().all()
        snaps = sess.execute(select(EquitySnapshot)).scalars().all()

    assert len(orders) >= 1
    assert all(o.client_order_id for o in orders)
    assert any(o.status == "filled" for o in orders)
    assert len(trades) >= 1
    assert all(t.strategy_version == "test" for t in trades)
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].qty > 0
    assert len(snaps) >= 1


def test_per_position_cap_enforced(temp_db):
    settings = Settings(
        alpaca_paper=True,
        per_bot_cap=25_000.0,
        per_position_pct=0.05,
        enabled_bots="stub",
        database_url=f"sqlite:///{temp_db}",
    )
    fake = _FakeClient()
    fake.prices["SPY"] = 100.0
    broker = BrokerAdapter(client=fake, settings=settings)
    coid = BrokerAdapter.make_client_order_id("test", "SPY")
    res = broker.submit("SPY", "buy", qty=50.0, bot_allocation=settings.per_bot_cap, client_order_id=coid)
    assert res is not None
    assert res.qty * fake.prices["SPY"] <= settings.per_bot_cap * settings.per_position_pct + 1e-6


def test_idempotency_prevents_double_submit(temp_db):
    """Two cycles in a row with an in-flight order should not re-submit."""
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

    # First cycle: submits an order. We DON'T reconcile so it stays in-flight
    # if status weren't 'filled'. Force the broker to return non-terminal status:
    # simulate by manually setting a fake status post-submit.
    orch.run_once()

    # Mark the order as 'accepted' (not terminal) to simulate it being open.
    with session_scope() as sess:
        o = sess.execute(select(Order)).scalar_one()
        o.status = "accepted"
        o.filled_qty = 0
    fake.orders_by_client_id[o.client_order_id].status = "accepted"
    fake.orders_by_client_id[o.client_order_id].filled_qty = 0

    # Second cycle should see the in-flight order and skip.
    n_orders_before = len(fake.orders_by_client_id)
    orch.run_once()
    n_orders_after = len(fake.orders_by_client_id)
    # No new broker submissions for SPY because the in-flight Order blocks it.
    assert n_orders_after == n_orders_before


def test_make_client_order_id_format():
    coid = BrokerAdapter.make_client_order_id("momentum", "SPY")
    assert coid.startswith("momentum-SPY-")
    assert len(coid) <= 48
    coid2 = BrokerAdapter.make_client_order_id("momentum", "SPY")
    assert coid != coid2  # nonce differs
