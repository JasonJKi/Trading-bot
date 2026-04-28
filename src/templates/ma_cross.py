"""Moving-average cross template — generalized trend-following bot.

Goes long when fast MA > slow MA; flat (or short, if `allow_short=True`) when
the cross reverses. Optional ADX filter rejects whipsaws in chop regimes.

This is a strict generalization of `src/bots/momentum.py`; if you set
ma_type="ema", fast=20, slow=50, adx_threshold=25, you get the same signals.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars
from src.templates._indicators import adx as adx_fn
from src.templates._indicators import ema, sma
from src.templates.base import ParamSpec, StrategyTemplate, _FunctionalStrategy, register


@register
class MovingAverageCrossTemplate(StrategyTemplate):
    id: ClassVar[str] = "ma_cross"
    name: ClassVar[str] = "Moving Average Cross"
    description: ClassVar[str] = (
        "Trend-following strategy: long when a fast moving average is above a "
        "slow moving average. Optional ADX filter rejects ranging markets. Works "
        "well on liquid index ETFs and large-cap equities; fails badly in low-ADX "
        "chop. Daily bars; rebalances after the close."
    )
    version: ClassVar[str] = "1"
    category: ClassVar[str] = "trend"
    asset_classes: ClassVar[list[str]] = ["equity", "etf", "crypto"]

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                "fast", "int",
                "Fast MA period in trading days. Must be < slow.",
                default=20, low=3, high=60,
            ),
            ParamSpec(
                "slow", "int",
                "Slow MA period in trading days.",
                default=50, low=10, high=250,
            ),
            ParamSpec(
                "ma_type", "choice",
                "Moving-average flavor. EMA reacts faster; SMA is steadier.",
                default="ema", choices=["ema", "sma"],
            ),
            ParamSpec(
                "use_adx_filter", "bool",
                "If True, only enter when ADX(14) is above threshold (filter chop).",
                default=True,
            ),
            ParamSpec(
                "adx_threshold", "float",
                "Minimum ADX(14) required to enter when filter is on. Typical 20-30.",
                default=25.0, low=10.0, high=50.0,
            ),
            ParamSpec(
                "allow_short", "bool",
                "If True, take short positions on bearish crosses. Most retail "
                "should leave this off — shorting equities has structural costs.",
                default=False,
            ),
            ParamSpec(
                "lookback_days", "int",
                "Historical bars to fetch each cycle. Must comfortably exceed slow.",
                default=180, low=60, high=500,
            ),
        ]

    @classmethod
    def instantiate(
        cls,
        *,
        bot_id: str,
        params: dict,
        universe: list[str],
        schedule: dict | None = None,
        version: str | None = None,
    ) -> Strategy:
        p = cls.validate_params(params)
        if p["fast"] >= p["slow"]:
            raise ValueError(f"fast ({p['fast']}) must be < slow ({p['slow']})")

        def target_fn(self: Strategy, ctx: StrategyContext) -> list[TargetPosition]:
            symbols = self.universe()
            bars = fetch_daily_bars(symbols, lookback_days=p["lookback_days"])
            ma_func = ema if p["ma_type"] == "ema" else sma
            longs: list[TargetPosition] = []
            shorts: list[TargetPosition] = []

            min_required = max(p["slow"], 30)
            for symbol, df in bars.items():
                if df.empty or len(df) < min_required:
                    continue
                close = df["close"]
                fast_ma = ma_func(close, p["fast"])
                slow_ma = ma_func(close, p["slow"])
                last_fast = float(fast_ma.iloc[-1])
                last_slow = float(slow_ma.iloc[-1])

                if p["use_adx_filter"]:
                    last_adx = float(adx_fn(df).iloc[-1])
                    if last_adx < p["adx_threshold"]:
                        continue
                else:
                    last_adx = 0.0

                meta = {"fast_ma": last_fast, "slow_ma": last_slow, "adx": last_adx}
                if last_fast > last_slow:
                    longs.append(TargetPosition(symbol=symbol, weight=1.0, meta=meta))
                elif p["allow_short"] and last_fast < last_slow:
                    shorts.append(TargetPosition(symbol=symbol, weight=-1.0, meta=meta))

            picks = longs + shorts
            if not picks:
                return []
            cap = get_settings().per_position_pct
            slot = min(1.0 / len(picks), cap)
            for tp in picks:
                tp.weight = slot if tp.weight > 0 else -slot
            return picks

        return _FunctionalStrategy(
            bot_id=bot_id,
            name=f"{cls.name} ({bot_id})",
            version=version or cls.version,
            schedule=schedule or cls.default_schedule(),
            universe=universe or cls.default_universe(),
            params=p,
            target_fn=target_fn,
        )
