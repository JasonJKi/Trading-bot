"""Feature engineering library.

Reusable, pure-function feature computers. Each takes pandas Series/DataFrame
and returns the same shape so they compose cleanly. No hidden state.

Naming:
  - `realized_*` are computed from past prices only (no look-ahead).
  - `cross_section_*` operate across symbols at a single point in time.
  - All windows are in trading days unless noted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --- single-series features ----------------------------------------------
def realized_vol(close: pd.Series, window: int = 20, annualize: int = 252) -> pd.Series:
    """Annualized realized volatility from close-to-close returns."""
    rets = close.pct_change()
    return rets.rolling(window).std() * np.sqrt(annualize)


def zscore(series: pd.Series, window: int = 20) -> pd.Series:
    """Rolling z-score: (x - rolling_mean) / rolling_std."""
    mu = series.rolling(window).mean()
    sd = series.rolling(window).std().replace(0, np.nan)
    return (series - mu) / sd


def returns(close: pd.Series, periods: int = 1) -> pd.Series:
    return close.pct_change(periods)


def log_returns(close: pd.Series, periods: int = 1) -> pd.Series:
    return np.log(close).diff(periods)


def momentum(close: pd.Series, lookback: int = 60, skip: int = 5) -> pd.Series:
    """Classic time-series momentum: return over [lookback] minus the most recent [skip] days.

    Skipping the latest week mitigates short-term reversal contamination — Asness et al.
    """
    return close.pct_change(lookback - skip).shift(skip)


def distance_from_high(close: pd.Series, window: int = 252) -> pd.Series:
    """How far below the rolling-window high (negative = below). 0 = at high."""
    high = close.rolling(window, min_periods=1).max()
    return close / high - 1.0


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. Expects columns: high, low, close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift()
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def realized_skew(close: pd.Series, window: int = 60) -> pd.Series:
    """Rolling skewness of daily returns. Negative = downside-fat-tailed."""
    return close.pct_change().rolling(window).skew()


def realized_kurt(close: pd.Series, window: int = 60) -> pd.Series:
    return close.pct_change().rolling(window).kurt()


def beta(symbol_close: pd.Series, benchmark_close: pd.Series, window: int = 60) -> pd.Series:
    """Rolling beta of `symbol` to `benchmark` (e.g. SPY)."""
    sr = symbol_close.pct_change()
    br = benchmark_close.pct_change()
    cov = sr.rolling(window).cov(br)
    var = br.rolling(window).var().replace(0, np.nan)
    return cov / var


def correlation(symbol_close: pd.Series, benchmark_close: pd.Series, window: int = 60) -> pd.Series:
    return symbol_close.pct_change().rolling(window).corr(benchmark_close.pct_change())


# --- cross-sectional features --------------------------------------------
def cross_section_zscore(values: pd.Series) -> pd.Series:
    """Z-score across the universe at a single timestamp.

    Input: indexed by symbol. Output: same shape, demeaned + std-normalized.
    """
    if values.empty or values.std() == 0:
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / values.std()


def cross_section_rank(values: pd.Series, ascending: bool = False) -> pd.Series:
    """Percentile rank across the universe (1.0 = best when ascending=False)."""
    return values.rank(pct=True, ascending=ascending)


def top_decile(values: pd.Series, n: int = 10, ascending: bool = False) -> list[str]:
    """Return symbol names in the top decile by `values`."""
    if values.empty:
        return []
    k = max(1, len(values) // n)
    return list(values.sort_values(ascending=ascending).head(k).index)


# --- multi-asset features ------------------------------------------------
def realized_dispersion(close_panel: pd.DataFrame, window: int = 20) -> pd.Series:
    """Cross-sectional dispersion of returns: stdev of returns *across symbols* at each ts.

    High dispersion -> stock-picking environment. Low dispersion -> macro-driven.
    Input: columns are symbols, index is dates.
    """
    daily_rets = close_panel.pct_change()
    return daily_rets.rolling(window).std().mean(axis=1)


def breadth(close_panel: pd.DataFrame, ma_window: int = 200) -> pd.Series:
    """Fraction of names trading above their N-day moving average. Classic bull/bear gauge."""
    ma = close_panel.rolling(ma_window).mean()
    above = (close_panel > ma).astype(float)
    return above.mean(axis=1)


def average_correlation(close_panel: pd.DataFrame, window: int = 60) -> pd.Series:
    """Mean pairwise correlation of returns across the universe — proxy for systemic risk."""
    rets = close_panel.pct_change().dropna(how="all")
    out = pd.Series(index=rets.index, dtype=float)
    for ts in rets.index:
        slice_ = rets.loc[:ts].tail(window)
        if len(slice_) < window // 2:
            continue
        corr = slice_.corr()
        n = corr.shape[0]
        if n < 2:
            continue
        # Mean of upper triangle excluding diagonal.
        mask = np.triu(np.ones((n, n), dtype=bool), k=1)
        out.loc[ts] = float(corr.values[mask].mean())
    return out
