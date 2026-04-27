import numpy as np
import pandas as pd

from src.bots.momentum import _adx, _ema, _macd_hist
from src.bots.mean_reversion import _bbands, _rsi


def _ohlc(close):
    close = pd.Series(close, dtype=float)
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close})


def test_ema_converges_to_constant():
    s = pd.Series([5.0] * 100)
    assert np.isclose(_ema(s, 10).iloc[-1], 5.0)


def test_macd_hist_zero_on_flat_series():
    s = pd.Series([100.0] * 60)
    assert abs(_macd_hist(s).iloc[-1]) < 1e-9


def test_adx_returns_finite_on_trend():
    close = pd.Series(np.linspace(100, 200, 100))
    df = _ohlc(close)
    adx = _adx(df)
    assert np.isfinite(adx.iloc[-1])
    assert adx.iloc[-1] > 0


def test_rsi_extremes():
    rising = pd.Series(np.linspace(100, 200, 30))
    falling = pd.Series(np.linspace(200, 100, 30))
    assert _rsi(rising).iloc[-1] > 80
    assert _rsi(falling).iloc[-1] < 20


def test_bbands_envelope():
    s = pd.Series(np.linspace(100, 110, 30) + np.random.default_rng(0).normal(0, 0.1, 30))
    lower, mid, upper = _bbands(s)
    assert (upper.dropna() >= mid.dropna()).all()
    assert (mid.dropna() >= lower.dropna()).all()
