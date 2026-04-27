"""Walk-forward optimizer tests using synthetic bars (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import optimize as opt
from src.core.strategy import Strategy, StrategyContext, TargetPosition


optuna = pytest.importorskip("optuna")


def test_make_windows_rolls_correctly():
    start = datetime(2024, 1, 1)
    end = datetime(2024, 12, 31)
    windows = opt.make_windows(start, end, train_days=90, test_days=30, step_days=30)
    assert len(windows) >= 5
    # Each test window starts where the train ends.
    for w in windows:
        assert w.test_start == w.train_end
    # Successive train windows are step_days apart.
    assert (windows[1].train_start - windows[0].train_start).days == 30


def test_make_windows_too_short_raises_via_walk_forward():
    """A range smaller than train+test should produce zero windows."""
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 30)
    windows = opt.make_windows(start, end, train_days=180, test_days=30)
    assert windows == []


class _FlipParamStrategy(Strategy):
    """Trivial bot whose behavior depends on a single 'long' param.

    long=1.0 -> always buy 5% of bot equity in 'X'
    long=0.0 -> hold cash
    """
    id = "flipper"
    name = "Flipper"

    def __init__(self, params=None):
        super().__init__(params)
        self.long = float(self.params.get("long", 0.5))

    def universe(self):
        return ["X"]

    def target_positions(self, ctx: StrategyContext):
        if self.long > 0.5:
            return [TargetPosition(symbol="X", weight=0.05)]
        return []


def test_walk_forward_finds_better_params(monkeypatch):
    """When one param value clearly outperforms in-sample, the optimizer should pick it."""
    # Synthetic uptrend: holding X grows equity.
    idx = pd.date_range("2024-01-01", periods=400, freq="D")
    fake_bars = {"X": pd.DataFrame({"close": np.linspace(100, 200, 400)}, index=idx)}

    monkeypatch.setattr(opt, "fetch_daily_bars", lambda *a, **kw: fake_bars)

    def factory(p):
        return _FlipParamStrategy({"long": p.get("long", 0.5)})

    def space(trial):
        return {"long": trial.suggest_float("long", 0.0, 1.0)}

    res = opt.walk_forward(
        factory,
        universe=["X"],
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
        param_space=space,
        n_trials=10,
        train_days=120,
        test_days=30,
    )
    # On a pure uptrend, the optimizer should pick long > 0.5 for the buy branch.
    assert res.best_params["long"] > 0.5
    assert len(res.per_window) >= 5


def test_walk_forward_reports_overfit_gap(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=400, freq="D")
    rng = np.random.default_rng(42)
    fake_bars = {"X": pd.DataFrame({"close": 100 * (1 + rng.normal(0, 0.01, 400)).cumprod()}, index=idx)}
    monkeypatch.setattr(opt, "fetch_daily_bars", lambda *a, **kw: fake_bars)

    def factory(p):
        return _FlipParamStrategy({"long": p.get("long", 0.5)})

    def space(trial):
        return {"long": trial.suggest_float("long", 0.0, 1.0)}

    res = opt.walk_forward(
        factory,
        universe=["X"],
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
        param_space=space,
        n_trials=8,
        train_days=120,
        test_days=30,
    )
    # On random data the optimizer SHOULD show a meaningful overfit gap
    # (good in-sample, weak OOS), so this is a sanity check that the gap field works.
    assert isinstance(res.overfit_gap, float)
    assert isinstance(res.robust, bool)
