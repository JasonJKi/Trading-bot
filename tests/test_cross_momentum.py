"""Cross-sectional momentum bot tests using synthetic bars."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.bots import cross_momentum as xm
from src.core.strategy import StrategyContext


def _bars(n: int, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, n)
    close = 100 * np.exp(rets.cumsum())
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close},
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def test_xs_momentum_picks_winners(monkeypatch):
    """Names with the strongest recent return should land in the long bucket."""
    panel = {
        "WIN1": _bars(120, drift=0.003, seed=1),
        "WIN2": _bars(120, drift=0.003, seed=2),
        "FLAT1": _bars(120, drift=0.0, seed=3),
        "FLAT2": _bars(120, drift=0.0, seed=4),
        "LOSE1": _bars(120, drift=-0.003, seed=5),
        "LOSE2": _bars(120, drift=-0.003, seed=6),
        "MEH1": _bars(120, drift=0.0005, seed=7),
        "MEH2": _bars(120, drift=-0.0005, seed=8),
        "MEH3": _bars(120, drift=0.0001, seed=9),
        "MEH4": _bars(120, drift=-0.0001, seed=10),
    }
    monkeypatch.setattr(xm, "fetch_daily_bars", lambda *a, **kw: panel)
    monkeypatch.setattr(
        xm.get_settings.__wrapped__ if hasattr(xm.get_settings, "__wrapped__") else xm.get_settings,
        "__call__",
        None,
        raising=False,
    )

    bot = xm.CrossSectionalMomentum({"top_n_buckets": 5})
    # Override universe to match our panel keys.
    bot.universe = lambda: list(panel.keys())  # type: ignore[method-assign]

    ctx = StrategyContext(
        now=datetime(2024, 4, 1, tzinfo=timezone.utc),
        cash=25_000,
        positions={},
        bot_equity=25_000,
        regime="bull",
    )
    targets = bot.target_positions(ctx)
    assert targets, "should produce at least one target"
    names = {t.symbol for t in targets}
    # Top bucket size = 10 // 5 = 2 — top 2 winners should be longs.
    assert names <= {"WIN1", "WIN2"}


def test_xs_momentum_stands_down_in_crisis(monkeypatch):
    panel = {"WIN1": _bars(120, drift=0.003, seed=1)}
    monkeypatch.setattr(xm, "fetch_daily_bars", lambda *a, **kw: panel)
    bot = xm.CrossSectionalMomentum()
    bot.universe = lambda: list(panel.keys())  # type: ignore[method-assign]

    ctx = StrategyContext(
        now=datetime(2024, 4, 1, tzinfo=timezone.utc),
        cash=25_000,
        positions={},
        bot_equity=25_000,
        regime="crisis",
    )
    assert bot.target_positions(ctx) == []


def test_xs_momentum_reduces_gross_in_bear(monkeypatch):
    panel = {
        f"S{i}": _bars(120, drift=0.001 * i, seed=i) for i in range(1, 11)
    }
    monkeypatch.setattr(xm, "fetch_daily_bars", lambda *a, **kw: panel)
    bot = xm.CrossSectionalMomentum({"top_n_buckets": 5})
    bot.universe = lambda: list(panel.keys())  # type: ignore[method-assign]

    bull = bot.target_positions(StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={}, bot_equity=25_000, regime="bull"
    ))
    bear = bot.target_positions(StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={}, bot_equity=25_000, regime="bear"
    ))
    bull_gross = sum(abs(t.weight) for t in bull)
    bear_gross = sum(abs(t.weight) for t in bear)
    assert bull_gross > 0
    assert bear_gross == pytest.approx(bull_gross * 0.5, rel=0.01)
