"""Allocator tests."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.config import Settings
from src.core import allocator
from src.core.store import EquitySnapshot, init_db, session_scope


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


def _seed(strategy_id: str, values, days_back: int = 30):
    base = datetime.now(timezone.utc)
    with session_scope() as sess:
        for i, v in enumerate(values):
            sess.add(
                EquitySnapshot(
                    ts=base - timedelta(days=days_back - i),
                    strategy_id=strategy_id,
                    cash=0,
                    position_value=v,
                    total_equity=v,
                )
            )


def test_bootstrap_when_no_history(temp_db):
    allocs = allocator.allocate(["a", "b", "c"], total_capital=100_000)
    assert len(allocs) == 3
    weights = [a.weight for a in allocs]
    assert all(0.32 < w < 0.35 for w in weights)  # equal-weight bootstrap


def test_softmax_favors_higher_sharpe(temp_db):
    # Bot A has clean uptrend, B has flat, C has drawdown.
    _seed("A", [100 * (1.005 ** i) for i in range(30)])
    _seed("B", [100.0] * 30)
    _seed("C", [100 * (0.99 ** i) for i in range(30)])
    allocs = {a.strategy_id: a for a in allocator.allocate(["A", "B", "C"], total_capital=100_000)}
    assert allocs["A"].weight > allocs["B"].weight
    assert allocs["A"].weight > allocs["C"].weight
    assert allocs["A"].sharpe_30d > 0


def test_floor_and_ceiling_enforced(temp_db):
    # Strongly winning bot should not exceed the ceiling.
    _seed("A", [100 * (1.02 ** i) for i in range(30)])
    _seed("B", [100.0] * 30)
    allocs = {
        a.strategy_id: a
        for a in allocator.allocate(
            ["A", "B"], total_capital=100_000, floor_pct=0.1, ceiling_pct=0.6
        )
    }
    assert allocs["A"].weight <= 0.6 + 1e-9
    assert allocs["B"].weight >= 0.1 - 1e-9


def test_weights_normalize_to_one(temp_db):
    _seed("A", [100 * (1.005 ** i) for i in range(30)])
    _seed("B", [100 * (1.001 ** i) for i in range(30)])
    allocs = allocator.allocate(["A", "B", "C"], total_capital=100_000)
    total = sum(a.weight for a in allocs)
    assert abs(total - 1.0) < 1e-6
    assert abs(sum(a.capital for a in allocs) - 100_000) < 1.0
