"""Short-window mean-reversion template — RSI extremes + optional Bollinger touch.

Buys oversold conditions and exits when momentum reverts. Optional Bollinger
band filter requires the close to also touch (or undercut) the lower band,
reducing false signals. Holdings are exited when RSI normalizes above
`rsi_exit` or the close re-crosses the mid-band.

Strict generalization of `src/bots/mean_reversion.py`. With rsi_period=2,
rsi_buy=10, rsi_exit=60, use_bbands=True, bbands_window=20, bbands_k=2.0
the signals match.
"""
from __future__ import annotations

from typing import ClassVar

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.bars import fetch_daily_bars
from src.templates._indicators import bollinger, rsi as rsi_fn
from src.templates.base import ParamSpec, StrategyTemplate, _FunctionalStrategy, register


@register
class MeanReversionRSITemplate(StrategyTemplate):
    id: ClassVar[str] = "mean_reversion_rsi"
    name: ClassVar[str] = "RSI Mean Reversion"
    description: ClassVar[str] = (
        "Buys short-window oversold conditions on liquid ETFs/equities and "
        "exits when RSI normalizes or price reverts to the mid-band. Best used "
        "on broad-market index ETFs where mean reversion has a structural basis "
        "(SPY, QQQ, IWM). Fails badly on trending single names — pair with a "
        "regime filter or restrict the universe."
    )
    version: ClassVar[str] = "1"
    category: ClassVar[str] = "mean_reversion"
    asset_classes: ClassVar[list[str]] = ["equity", "etf"]

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                "rsi_period", "int",
                "RSI lookback. 2-3 for short-window mean reversion (Larry Connors style); "
                "14 is the textbook default.",
                default=2, low=2, high=30,
            ),
            ParamSpec(
                "rsi_buy", "float",
                "RSI threshold to enter long. Lower = more selective. Typical 5-25.",
                default=10.0, low=1.0, high=40.0,
            ),
            ParamSpec(
                "rsi_exit", "float",
                "RSI threshold to exit. Must be > rsi_buy.",
                default=60.0, low=40.0, high=90.0,
            ),
            ParamSpec(
                "use_bbands", "bool",
                "Require close <= lower Bollinger band as an entry confirmation.",
                default=True,
            ),
            ParamSpec(
                "bbands_window", "int",
                "Bollinger window in trading days.",
                default=20, low=5, high=60,
            ),
            ParamSpec(
                "bbands_k", "float",
                "Bollinger band stddev multiplier (typical 1.5-2.5).",
                default=2.0, low=1.0, high=3.0,
            ),
            ParamSpec(
                "lookback_days", "int",
                "Historical bars to fetch each cycle.",
                default=120, low=30, high=400,
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
        if p["rsi_exit"] <= p["rsi_buy"]:
            raise ValueError(
                f"rsi_exit ({p['rsi_exit']}) must be > rsi_buy ({p['rsi_buy']})"
            )

        def target_fn(self: Strategy, ctx: StrategyContext) -> list[TargetPosition]:
            symbols = self.universe()
            bars = fetch_daily_bars(symbols, lookback_days=p["lookback_days"])
            held = set(ctx.positions.keys())
            picks: list[TargetPosition] = []

            min_required = max(p["bbands_window"] + 5, p["rsi_period"] + 5, 25)
            for symbol, df in bars.items():
                if df.empty or len(df) < min_required:
                    continue
                close = df["close"]
                last_close = float(close.iloc[-1])
                last_rsi = float(rsi_fn(close, p["rsi_period"]).iloc[-1])

                lower, mid, _ = bollinger(close, p["bbands_window"], p["bbands_k"])
                last_lower = float(lower.iloc[-1])
                last_mid = float(mid.iloc[-1])

                bbands_ok = (not p["use_bbands"]) or (last_close <= last_lower)
                entry = last_rsi <= p["rsi_buy"] and bbands_ok
                exit_signal = last_rsi >= p["rsi_exit"] or last_close >= last_mid

                meta = {"rsi": last_rsi, "lower_band": last_lower, "mid_band": last_mid}
                if symbol in held:
                    if not exit_signal:
                        picks.append(TargetPosition(symbol=symbol, weight=1.0, meta={**meta, "hold": True}))
                    # else: drop -> orchestrator will close
                elif entry:
                    picks.append(TargetPosition(symbol=symbol, weight=1.0, meta=meta))

            if not picks:
                return []
            cap = get_settings().per_position_pct
            slot = min(1.0 / len(picks), cap)
            for tp in picks:
                tp.weight = slot
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
