"""Indicator helpers shared across templates.

Kept tiny and dependency-free (just pandas). Each function takes a Series or
DataFrame and returns the same shape — no in-place mutation, no global state.
"""
from __future__ import annotations

import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def bollinger(close: pd.Series, window: int = 20, k: float = 2.0):
    """Returns (lower, mid, upper)."""
    ma = close.rolling(window).mean()
    sd = close.rolling(window).std()
    return ma - k * sd, ma, ma + k * sd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength oscillator (0..100)."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = (high.diff()).where((high.diff() > low.diff().abs()) & (high.diff() > 0), 0.0)
    minus_dm = (-low.diff()).where((low.diff().abs() > high.diff()) & (low.diff() < 0), 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / a)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / a)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def donchian(df: pd.DataFrame, lookback: int = 20):
    """Returns (lower, upper) — the rolling N-bar low and high."""
    return df["low"].rolling(lookback).min(), df["high"].rolling(lookback).max()
