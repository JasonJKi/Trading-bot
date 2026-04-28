"""Volatility breakout template — Donchian channel + ATR sizing.

Long when the close breaks above the N-bar high (Donchian upper); short or
flat below the N-bar low. Position sizing is volatility-targeted using ATR so
high-vol names don't dominate the book.

This is the canonical "turtle trader" structure, generalized. Works on
trending markets (commodities, FX, crypto, single-stock breakouts) but takes
many small losses in chop — make sure the universe and the per-trade risk
budget are sized for that.
"""
from __future__ import annotations

from typing import ClassVar

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars
from src.templates._indicators import atr as atr_fn
from src.templates._indicators import donchian
from src.templates.base import ParamSpec, StrategyTemplate, _FunctionalStrategy, register


@register
class VolBreakoutTemplate(StrategyTemplate):
    id: ClassVar[str] = "vol_breakout"
    name: ClassVar[str] = "Donchian / ATR Volatility Breakout"
    description: ClassVar[str] = (
        "Goes long on a break above the N-bar high (Donchian upper channel); "
        "exits on a break below the M-bar low or via ATR-based trailing stop. "
        "Position sized so each entry risks a fixed fraction of equity (ATR-"
        "scaled). Strongest on trending crypto, commodities, and momentum "
        "single names. Expect a low win rate (30-40%) compensated by big "
        "winners — psychologically difficult to run by hand."
    )
    version: ClassVar[str] = "1"
    category: ClassVar[str] = "breakout"
    asset_classes: ClassVar[list[str]] = ["equity", "etf", "crypto"]

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                "entry_lookback", "int",
                "Donchian channel period for entries. Classic Turtle: 20 or 55.",
                default=20, low=10, high=120,
            ),
            ParamSpec(
                "exit_lookback", "int",
                "Donchian channel period for exits (typically half of entry).",
                default=10, low=5, high=60,
            ),
            ParamSpec(
                "atr_period", "int",
                "ATR window for volatility sizing.",
                default=14, low=5, high=30,
            ),
            ParamSpec(
                "atr_risk_multiple", "float",
                "Stop distance in ATR multiples (also drives position size).",
                default=2.0, low=0.5, high=5.0,
            ),
            ParamSpec(
                "risk_per_trade_pct", "float",
                "Fraction of bot equity risked per trade. 1% is a reasonable cap.",
                default=0.01, low=0.001, high=0.03,
            ),
            ParamSpec(
                "allow_short", "bool",
                "Take short breakouts on N-bar lows.",
                default=False,
            ),
            ParamSpec(
                "lookback_days", "int",
                "Historical bars to fetch each cycle.",
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
        if p["exit_lookback"] >= p["entry_lookback"]:
            raise ValueError(
                f"exit_lookback ({p['exit_lookback']}) should be < "
                f"entry_lookback ({p['entry_lookback']}) for the breakout structure"
            )

        def target_fn(self: Strategy, ctx: StrategyContext) -> list[TargetPosition]:
            symbols = self.universe()
            bars = fetch_daily_bars(symbols, lookback_days=p["lookback_days"])
            held = set(ctx.positions.keys())
            picks: list[TargetPosition] = []

            min_required = max(p["entry_lookback"], p["atr_period"]) + 5
            for symbol, df in bars.items():
                if df.empty or len(df) < min_required:
                    continue
                close = df["close"]
                last_close = float(close.iloc[-1])

                # Use yesterday's channel so the close-bar itself can break out.
                entry_low, entry_high = donchian(df, p["entry_lookback"])
                exit_low, exit_high = donchian(df, p["exit_lookback"])
                ref_high = float(entry_high.iloc[-2])
                ref_low = float(entry_low.iloc[-2])
                exit_ref_low = float(exit_low.iloc[-2])
                exit_ref_high = float(exit_high.iloc[-2])

                last_atr = float(atr_fn(df, p["atr_period"]).iloc[-1])
                if last_atr <= 0:
                    continue

                # Position sizing: dollars at risk = bot_equity * risk_per_trade_pct
                # Stop distance in $ = atr * atr_risk_multiple
                # Position notional = (dollars_at_risk / stop_distance_$) * last_close
                stop_dist = last_atr * p["atr_risk_multiple"]
                if stop_dist <= 0:
                    continue
                dollars_at_risk = max(ctx.bot_equity, 0.0) * p["risk_per_trade_pct"]
                shares = dollars_at_risk / stop_dist
                position_notional = shares * last_close
                weight = (
                    position_notional / ctx.bot_equity if ctx.bot_equity > 0 else 0.0
                )
                weight = min(max(weight, 0.0), get_settings().per_position_pct)

                meta = {
                    "atr": last_atr,
                    "ref_high": ref_high,
                    "ref_low": ref_low,
                    "stop_dist": stop_dist,
                }

                if symbol in held:
                    # Exit if we breach the exit channel low
                    if last_close < exit_ref_low:
                        continue  # drop -> orchestrator closes
                    picks.append(TargetPosition(symbol=symbol, weight=weight, meta={**meta, "hold": True}))
                elif last_close > ref_high and weight > 0:
                    picks.append(TargetPosition(symbol=symbol, weight=weight, meta=meta))
                elif p["allow_short"] and last_close < ref_low and weight > 0:
                    picks.append(TargetPosition(symbol=symbol, weight=-weight, meta=meta))
                elif p["allow_short"] and symbol in held and last_close > exit_ref_high:
                    continue  # short exit

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
