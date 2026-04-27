"""Feature library tests — pure-function, deterministic, no network."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data import features as F


def _series(values, freq="D"):
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq=freq), dtype=float)


def test_realized_vol_positive_on_volatile_series():
    rng = np.random.default_rng(0)
    s = _series(100 * (1 + rng.normal(0, 0.02, 100)).cumprod())
    v = F.realized_vol(s, window=20)
    assert v.dropna().min() > 0


def test_zscore_centered_on_constant_series():
    s = _series([10.0] * 50)
    z = F.zscore(s, window=20).dropna()
    assert (z == 0).all() or z.isna().all()  # zero variance -> nan or 0


def test_momentum_skip_avoids_recent_window():
    # Strong recent reversal — pure momentum without skip would be wrong sign.
    s = _series([100] * 60 + [105] * 30 + [80] * 5)
    m = F.momentum(s, lookback=60, skip=5).iloc[-1]
    assert m > 0  # the older rise dominates because the recent dip is skipped


def test_distance_from_high_zero_at_top():
    s = _series([100, 110, 120, 110, 100])
    d = F.distance_from_high(s, window=5)
    assert d.iloc[2] == 0.0
    assert d.iloc[4] < 0


def test_beta_close_to_one_for_self():
    s = _series([100 + i for i in range(60)])
    b = F.beta(s, s, window=20).dropna()
    assert np.isclose(b.iloc[-1], 1.0, atol=1e-6)


def test_cross_section_zscore_zero_mean():
    v = pd.Series({"A": 1, "B": 2, "C": 3, "D": 4})
    z = F.cross_section_zscore(v)
    assert np.isclose(z.mean(), 0.0)


def test_top_decile_picks_best():
    v = pd.Series({"A": 1.0, "B": 5.0, "C": 3.0, "D": 9.0, "E": 2.0,
                   "F": 4.0, "G": 6.0, "H": 7.0, "I": 8.0, "J": 0.5})
    top = F.top_decile(v, n=10)
    assert top == ["D"]


def test_breadth_in_zero_one():
    panel = pd.DataFrame(
        {f"S{i}": np.linspace(100, 100 + i, 250) for i in range(5)},
        index=pd.date_range("2023-01-01", periods=250, freq="D"),
    )
    b = F.breadth(panel, ma_window=50).dropna()
    assert (b >= 0).all() and (b <= 1).all()


def test_realized_dispersion_zero_when_all_same():
    panel = pd.DataFrame({"A": _series([100] * 30), "B": _series([100] * 30)})
    d = F.realized_dispersion(panel, window=10).dropna()
    assert (d.abs() < 1e-9).all()
