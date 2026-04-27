"""Risk module tests: circuit breaker + graduation gate."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from src.config import Settings
from src.core import risk
from src.core.store import BotStatus, EquitySnapshot, init_db, session_scope


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


def _seed_equity(strategy_id: str, values, days_back: int = 30):
    """Insert one equity snapshot per day going backwards from today."""
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


def test_circuit_breaker_does_not_trip_on_steady_growth(temp_db):
    _seed_equity("momentum", [100 * (1.001 ** i) for i in range(40)])
    assert not risk.evaluate_circuit_breaker("momentum", max_dd=0.15)


def test_circuit_breaker_trips_on_deep_drawdown(temp_db):
    eq = [100, 110, 120, 105, 95, 90, 85, 80, 75, 70, 70, 70]  # ~42% DD
    _seed_equity("momentum", eq)
    assert risk.evaluate_circuit_breaker("momentum", max_dd=0.15)


def test_trip_circuit_breaker_pauses_bot(temp_db, monkeypatch):
    monkeypatch.setenv("PER_BOT_MAX_DRAWDOWN", "0.15")
    from src import config
    config._settings = None
    eq = [100, 110, 120, 105, 95, 90, 85, 80, 75, 70, 70, 70]
    _seed_equity("momentum", eq)

    tripped = risk.trip_circuit_breaker_if_needed("momentum")
    assert tripped is True
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == "momentum")
        ).scalar_one()
    assert row.state == "paused"


def test_graduation_rejects_short_sample(temp_db):
    _seed_equity("momentum", [100, 101, 102], days_back=3)
    with pytest.raises(RuntimeError, match="not ready"):
        risk.graduate("momentum")


def test_graduation_rejects_low_sharpe(temp_db):
    # 60 days of flat equity -> Sharpe ~ 0.
    _seed_equity("momentum", [100.0] * 60, days_back=60)
    with pytest.raises(RuntimeError, match="Sharpe"):
        risk.graduate("momentum")


def test_graduation_passes_with_strong_paper_record(temp_db):
    rng = np.random.default_rng(42)
    # Slow upward drift with low vol -> high Sharpe.
    rets = rng.normal(0.005, 0.005, 60)
    eq = [100.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r))
    _seed_equity("momentum", eq[1:], days_back=60)
    check = risk.graduate("momentum")
    assert check.passed
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == "momentum")
        ).scalar_one()
    assert row.paper_validated_at is not None


def test_assert_all_paper_validated_blocks_unvalidated(temp_db):
    with pytest.raises(RuntimeError, match="not been paper-validated"):
        risk.assert_all_paper_validated(["momentum", "mean_reversion"])


def test_pause_and_enable_round_trip(temp_db):
    risk.pause_bot("momentum", reason="manual")
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == "momentum")
        ).scalar_one()
    assert row.state == "paused"
    risk.enable_bot("momentum")
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == "momentum")
        ).scalar_one()
    assert row.state == "enabled"
