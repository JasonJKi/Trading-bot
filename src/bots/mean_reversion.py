"""Short-window mean reversion: RSI(2) extremes + lower Bollinger Band touch."""
from __future__ import annotations

import logging

import pandas as pd

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)


def _rsi(close: pd.Series, period: int = 2) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def _bbands(close: pd.Series, window: int = 20, k: float = 2.0):
    ma = close.rolling(window).mean()
    sd = close.rolling(window).std()
    return ma - k * sd, ma, ma + k * sd


class MeanReversionStrategy(Strategy):
    id = "mean_reversion"
    name = "Mean Reversion"
    description = "Short-window reversal on US ETFs — daily, oversold/overbought."
    version = "1.0"
    schedule = {"day_of_week": "mon-fri", "hour": "20", "minute": "10"}

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.rsi_buy = self.params.get("rsi_buy", 10.0)
        self.rsi_exit = self.params.get("rsi_exit", 60.0)

    def universe(self) -> list[str]:
        return get_settings().mean_reversion_symbols()

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        symbols = self.universe()
        bars = fetch_daily_bars(symbols, lookback_days=120)
        candidates: list[TargetPosition] = []
        held = set(ctx.positions.keys())

        for symbol, df in bars.items():
            if df.empty or len(df) < 25:
                continue
            close = df["close"]
            rsi = _rsi(close)
            lower, mid, upper = _bbands(close)

            last_close = float(close.iloc[-1])
            last_rsi = float(rsi.iloc[-1])
            last_lower = float(lower.iloc[-1])
            last_mid = float(mid.iloc[-1])

            entry = last_rsi <= self.rsi_buy and last_close <= last_lower
            holding = symbol in held
            exit_signal = last_rsi >= self.rsi_exit or last_close >= last_mid

            if entry and not holding:
                candidates.append(
                    TargetPosition(
                        symbol=symbol,
                        weight=1.0,
                        meta={"rsi": last_rsi, "lower_band": last_lower},
                    )
                )
            elif holding and not exit_signal:
                # Keep existing position alive without resizing.
                candidates.append(
                    TargetPosition(
                        symbol=symbol,
                        weight=1.0,
                        meta={"rsi": last_rsi, "hold": True},
                    )
                )
            # else: drop -> orchestrator closes the position.

        if not candidates:
            return []
        weight = 1.0 / len(candidates)
        cap = get_settings().per_position_pct
        weight = min(weight, cap)
        for c in candidates:
            c.weight = weight
        return candidates
