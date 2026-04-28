"""Cross-sectional momentum.

Different from the existing time-series momentum bot:
  - TS momentum: hold a name when it has been going up.
  - XS momentum: rank the universe each day, hold the top decile and
    skip / short the bottom decile. Position sizes are vol-targeted so
    every name contributes equal risk.

Regime-aware: stands down in 'crisis' regime (correlations explode,
single-stock momentum stops working). Reduced exposure in 'bear'.

This bot showcases the full feature stack: cross_section_zscore +
regime + vol-targeted sizing.
"""
from __future__ import annotations

import logging

import pandas as pd

from src.config import get_settings
from src.core import sizing
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data import features as F
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)


class CrossSectionalMomentum(Strategy):
    id = "xs_momentum"
    name = "Cross-Sectional Momentum"
    description = "Long top-decile / short bottom-decile ranks on a US universe."
    version = "1.0"
    schedule = {"day_of_week": "mon-fri", "hour": "20", "minute": "15"}

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.lookback = int(self.params.get("lookback", 60))
        self.skip = int(self.params.get("skip", 5))
        self.top_n_buckets = int(self.params.get("top_n_buckets", 5))  # top 1/5 = 20%
        self.target_vol = float(self.params.get("target_vol", 0.15))
        self.short_bottom = bool(self.params.get("short_bottom", False))

    def universe(self) -> list[str]:
        return get_settings().momentum_symbols()

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        # Skip in crisis regime — single-name momentum collapses when correlations approach 1.
        if ctx.regime == "crisis":
            log.info("xs_momentum: standing down (regime=crisis)")
            return []

        symbols = self.universe()
        if not symbols:
            return []
        bars = fetch_daily_bars(symbols, lookback_days=max(self.lookback + 30, 120))

        # Compute time-series momentum per symbol, take the latest value.
        momentum_now: dict[str, float] = {}
        for sym, df in bars.items():
            if df.empty or len(df) < self.lookback + self.skip + 5:
                continue
            mom = F.momentum(df["close"], lookback=self.lookback, skip=self.skip)
            mom = mom.dropna()
            if mom.empty:
                continue
            momentum_now[sym] = float(mom.iloc[-1])

        if len(momentum_now) < self.top_n_buckets:
            log.info("xs_momentum: not enough names with usable history (%d)", len(momentum_now))
            return []

        # Cross-sectional rank: take the top bucket, optionally short the bottom.
        scores = pd.Series(momentum_now)
        bucket_size = max(1, len(scores) // self.top_n_buckets)
        longs = list(scores.sort_values(ascending=False).head(bucket_size).index)
        shorts: list[str] = []
        if self.short_bottom:
            shorts = list(scores.sort_values(ascending=True).head(bucket_size).index)

        # Vol-target each leg, normalize so weights sum into a sensible band.
        long_bars = {s: bars[s] for s in longs if s in bars}
        long_weights = sizing.equal_risk_weights(long_bars, target_annual_vol=self.target_vol)
        if not long_weights:
            return []

        # Cap the gross book at the per-position cap times the count.
        cap = get_settings().per_position_pct
        long_weights = sizing.normalize_to(long_weights, total=cap * len(long_weights))

        # Reduce gross exposure in bear regime.
        if ctx.regime == "bear":
            long_weights = {k: v * 0.5 for k, v in long_weights.items()}

        targets = [
            TargetPosition(
                symbol=s,
                weight=w,
                meta={
                    "regime": ctx.regime,
                    "momentum": momentum_now.get(s, 0.0),
                    "leg": "long",
                },
            )
            for s, w in long_weights.items()
        ]

        if shorts:
            short_bars = {s: bars[s] for s in shorts if s in bars}
            short_weights = sizing.equal_risk_weights(short_bars, target_annual_vol=self.target_vol)
            short_weights = sizing.normalize_to(short_weights, total=cap * len(short_weights) * 0.5)
            for s, w in short_weights.items():
                targets.append(
                    TargetPosition(
                        symbol=s,
                        weight=-w,
                        meta={
                            "regime": ctx.regime,
                            "momentum": momentum_now.get(s, 0.0),
                            "leg": "short",
                        },
                    )
                )

        log.info(
            "xs_momentum: %d longs %d shorts (regime=%s)",
            len(longs), len(shorts), ctx.regime,
        )
        return targets
