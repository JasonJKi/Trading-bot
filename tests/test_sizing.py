"""Sizing helper tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.core import sizing


def _series(values):
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"), dtype=float)


def test_vol_target_weight_smaller_for_higher_vol():
    low = sizing.vol_target_weight(symbol_vol=0.10)  # sleepy name
    high = sizing.vol_target_weight(symbol_vol=0.50)  # noisy name
    assert low > high
    assert 0 <= high <= 1
    assert 0 <= low <= 1


def test_vol_target_weight_caps_at_one():
    # If symbol vol is below target, weight wouldn't exceed 100% (no leverage).
    assert sizing.vol_target_weight(symbol_vol=0.05, target_annual_vol=0.15) == 1.0


def test_vol_target_qty_respects_dollar_budget():
    qty = sizing.vol_target_qty(price=100.0, symbol_vol=0.30, dollar_budget=10_000)
    notional = qty * 100.0
    # target/vol = 0.15/0.30 = 0.5 of budget = $5000 = 50 shares
    assert np.isclose(notional, 5_000.0)
    assert np.isclose(qty, 50.0)


def test_realized_vol_returns_finite_default_on_empty():
    assert sizing.realized_vol(pd.Series(dtype=float)) == sizing.DEFAULT_TARGET_VOL


def test_realized_vol_clamped():
    # Very high simulated vol should be clamped at MAX_VOL.
    rng = np.random.default_rng(0)
    s = _series(100 * (1 + rng.normal(0, 0.3, 60)).cumprod())
    v = sizing.realized_vol(s, window=20)
    assert sizing.MIN_VOL <= v <= sizing.MAX_VOL


def test_equal_risk_weights_assigns_more_to_sleepier():
    # Build two synthetic series: low vol vs high vol.
    rng = np.random.default_rng(1)
    low = pd.DataFrame({"close": 100 + rng.normal(0, 0.5, 60).cumsum() * 0.1})
    high = pd.DataFrame({"close": 100 + rng.normal(0, 5, 60).cumsum() * 0.1})
    weights = sizing.equal_risk_weights({"LOW": low, "HIGH": high})
    assert weights["LOW"] >= weights["HIGH"]


def test_normalize_to_sums_to_target():
    w = sizing.normalize_to({"a": 0.3, "b": 0.4, "c": 0.3}, total=1.0)
    assert abs(sum(w.values()) - 1.0) < 1e-9
