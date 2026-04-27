"""Trend-following bot: EMA(20)/EMA(50) cross + MACD histogram + ADX(14) chop filter."""
from __future__ import annotations

import logging

import pandas as pd

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd, signal)
    return macd - signal_line


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = (high.diff()).where((high.diff() > low.diff().abs()) & (high.diff() > 0), 0.0)
    minus_dm = (-low.diff()).where((low.diff().abs() > high.diff()) & (low.diff() < 0), 0.0)
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


class MomentumStrategy(Strategy):
    id = "momentum"
    name = "Momentum / Trend"
    version = "1.0"  # bump when signal logic changes
    schedule = {"day_of_week": "mon-fri", "hour": "20", "minute": "5"}  # daily after US close UTC

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast = self.params.get("fast", 20)
        self.slow = self.params.get("slow", 50)
        self.adx_threshold = self.params.get("adx_threshold", 25.0)

    def universe(self) -> list[str]:
        return get_settings().momentum_symbols()

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        symbols = self.universe()
        bars = fetch_daily_bars(symbols, lookback_days=180)
        signals: list[TargetPosition] = []

        for symbol, df in bars.items():
            if df.empty or len(df) < max(self.slow, 30):
                continue
            close = df["close"]
            fast_ema = _ema(close, self.fast)
            slow_ema = _ema(close, self.slow)
            hist = _macd_hist(close)
            adx = _adx(df)

            if (
                fast_ema.iloc[-1] > slow_ema.iloc[-1]
                and hist.iloc[-1] > 0
                and adx.iloc[-1] >= self.adx_threshold
            ):
                signals.append(
                    TargetPosition(
                        symbol=symbol,
                        weight=1.0,  # post-normalization below
                        meta={
                            "adx": float(adx.iloc[-1]),
                            "macd_hist": float(hist.iloc[-1]),
                        },
                    )
                )

        if not signals:
            return []
        # Equal-weight across active longs, capped by per-position limit downstream.
        weight = 1.0 / len(signals)
        cap = get_settings().per_position_pct
        weight = min(weight, cap)
        for s in signals:
            s.weight = weight
        return signals
