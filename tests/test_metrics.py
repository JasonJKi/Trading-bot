import numpy as np
import pandas as pd

from src.core import metrics


def _equity(values, freq="D"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


def test_total_return_basic():
    eq = _equity([100, 110, 121])
    assert np.isclose(metrics.total_return(eq), 0.21)


def test_total_return_empty_or_zero_start():
    assert metrics.total_return(pd.Series(dtype=float)) == 0.0
    assert metrics.total_return(_equity([0, 10])) == 0.0


def test_max_drawdown_negative_or_zero():
    eq = _equity([100, 120, 90, 110])
    assert metrics.max_drawdown(eq) < 0
    assert metrics.max_drawdown(_equity([100, 100, 100])) == 0.0


def test_sharpe_zero_when_flat():
    assert metrics.sharpe(_equity([100, 100, 100, 100])) == 0.0


def test_sharpe_positive_when_growing():
    eq = _equity([100 * (1.001 ** i) for i in range(60)])
    assert metrics.sharpe(eq) > 0


def test_trade_stats_handles_empty():
    win, w, l, exp = metrics.trade_stats(pd.Series(dtype=float))
    assert (win, w, l, exp) == (0.0, 0.0, 0.0, 0.0)


def test_trade_stats_basic():
    pnls = pd.Series([10, -5, 20, -2, 8])
    win, w, l, exp = metrics.trade_stats(pnls)
    assert win == 0.6
    assert w > 0 > l
    assert np.isclose(exp, 0.6 * w + 0.4 * l)


def test_correlation_matrix_shape():
    a = _equity([100, 101, 103, 102, 104])
    b = _equity([200, 199, 198, 200, 201])
    m = metrics.correlation_matrix({"a": a, "b": b})
    assert m.shape == (2, 2)
    assert np.isclose(m.loc["a", "a"], 1.0)
