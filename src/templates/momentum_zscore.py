"""Cross-sectional momentum template — rank a universe by Z-scored returns.

Each cycle: compute each symbol's `lookback`-day return, Z-score across the
universe, take the top-N as longs (and optionally bottom-N as shorts).
Equal-weight or vol-targeted within picks.

This is the textbook small-fund momentum factor: it works because of
documented cross-sectional momentum effects but degrades when the whole market
correlates (regime crashes). Pair with a regime filter when going live.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars
from src.templates.base import ParamSpec, StrategyTemplate, _FunctionalStrategy, register


@register
class MomentumZScoreTemplate(StrategyTemplate):
    id: ClassVar[str] = "momentum_zscore"
    name: ClassVar[str] = "Cross-Sectional Momentum (Z-score)"
    description: ClassVar[str] = (
        "Ranks the universe by Z-scored N-day return; longs the top decile, "
        "optionally shorts the bottom. Captures the well-documented cross-"
        "sectional momentum factor. Strongest on equity universes of 20+ "
        "names; degrades in correlation crashes (March 2020, 2022 inflation "
        "shock). Skip the most-recent-month return (`skip_recent`) is a "
        "standard adjustment that improves out-of-sample stability."
    )
    version: ClassVar[str] = "1"
    category: ClassVar[str] = "momentum"
    asset_classes: ClassVar[list[str]] = ["equity", "etf", "crypto"]

    @classmethod
    def default_schedule(cls) -> dict:
        # Weekly rebalance is the academic default; daily is also fine.
        return {"day_of_week": "fri", "hour": "20", "minute": "30"}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                "lookback_days", "int",
                "Lookback window in trading days. 126 (~6mo) is the academic standard.",
                default=126, low=21, high=252,
            ),
            ParamSpec(
                "skip_recent_days", "int",
                "Skip the most recent N days from the return calc to avoid the "
                "short-term reversal effect. Set to 0 to disable; 21 (~1mo) is standard.",
                default=21, low=0, high=42,
            ),
            ParamSpec(
                "top_n", "int",
                "Number of longs each cycle. Cap at universe size.",
                default=5, low=1, high=20,
            ),
            ParamSpec(
                "long_short", "bool",
                "Also short the bottom-N. Most retail should leave off.",
                default=False,
            ),
            ParamSpec(
                "min_zscore", "float",
                "Skip picks with abs(z) below this threshold (no signal).",
                default=0.5, low=0.0, high=2.0,
            ),
            ParamSpec(
                "fetch_lookback_days", "int",
                "Bars to fetch each cycle. Must >= lookback + skip_recent + buffer.",
                default=200, low=60, high=500,
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
        required = p["lookback_days"] + p["skip_recent_days"] + 5
        if p["fetch_lookback_days"] < required:
            raise ValueError(
                f"fetch_lookback_days ({p['fetch_lookback_days']}) must be "
                f">= lookback_days + skip_recent_days + 5 ({required})"
            )

        def target_fn(self: Strategy, ctx: StrategyContext) -> list[TargetPosition]:
            symbols = self.universe()
            if len(symbols) < 4:
                # Cross-sectional needs a real universe — refuse to trade
                # rather than emit garbage rankings on 2 names.
                return []
            bars = fetch_daily_bars(symbols, lookback_days=p["fetch_lookback_days"])

            returns: dict[str, float] = {}
            for symbol in symbols:
                df = bars.get(symbol)
                if df is None or df.empty:
                    continue
                close = df["close"]
                if len(close) < required:
                    continue
                # Past return = (price_{-skip-1} / price_{-skip-lookback-1}) - 1
                end_idx = -1 - p["skip_recent_days"]
                start_idx = end_idx - p["lookback_days"]
                if abs(start_idx) > len(close):
                    continue
                start_price = float(close.iloc[start_idx])
                end_price = float(close.iloc[end_idx])
                if start_price <= 0:
                    continue
                returns[symbol] = (end_price / start_price) - 1.0

            if len(returns) < 2:
                return []

            ser = pd.Series(returns)
            mean = ser.mean()
            std = ser.std()
            if std == 0 or pd.isna(std):
                return []
            zs = (ser - mean) / std

            # Long the top-N (z > min_zscore); optionally short the bottom-N (z < -min_zscore).
            longs = zs.sort_values(ascending=False)
            longs = longs[longs > p["min_zscore"]].head(p["top_n"])
            picks: list[TargetPosition] = [
                TargetPosition(symbol=sym, weight=1.0, meta={"zscore": float(z), "side": "long"})
                for sym, z in longs.items()
            ]
            if p["long_short"]:
                shorts = zs.sort_values(ascending=True)
                shorts = shorts[shorts < -p["min_zscore"]].head(p["top_n"])
                for sym, z in shorts.items():
                    picks.append(
                        TargetPosition(
                            symbol=sym, weight=-1.0, meta={"zscore": float(z), "side": "short"}
                        )
                    )

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
