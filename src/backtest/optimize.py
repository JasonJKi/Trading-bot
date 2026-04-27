"""Walk-forward parameter optimization.

The right way to tune trading-strategy parameters:
  1. Slice history into rolling (train, test) windows.
  2. For each train window, search the param space for the best Sharpe.
  3. Apply those params to the *next* (test) window. Record OOS performance.
  4. Aggregate OOS results across windows. The strategy is "robust" only if
     OOS Sharpe holds up across windows — not just on one.

We use Optuna (TPE sampler) for the inner search. Random search would also
be fine; TPE just samples more efficiently.

Robustness check: the report includes both
  - best_sharpe_in_sample  (the brittle number; what overfit looks like)
  - median_oos_sharpe      (the honest number)
A large gap means the params are overfit and shouldn't be deployed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.core import metrics
from src.core.strategy import Strategy, StrategyContext
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Window:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(slots=True)
class WalkForwardResult:
    strategy_id: str
    best_params: dict[str, Any]
    median_oos_sharpe: float
    best_in_sample_sharpe: float
    overfit_gap: float
    per_window: list[dict] = field(default_factory=list)

    @property
    def robust(self) -> bool:
        """True if OOS Sharpe is healthy and not far from in-sample."""
        return self.median_oos_sharpe >= 0.5 and self.overfit_gap <= 1.0


def make_windows(
    start: datetime,
    end: datetime,
    train_days: int = 180,
    test_days: int = 30,
    step_days: int | None = None,
) -> list[Window]:
    """Roll the (train, test) pair forward in `step_days` increments."""
    step = step_days or test_days
    windows = []
    cursor = start
    while cursor + timedelta(days=train_days + test_days) <= end:
        windows.append(
            Window(
                train_start=cursor,
                train_end=cursor + timedelta(days=train_days),
                test_start=cursor + timedelta(days=train_days),
                test_end=cursor + timedelta(days=train_days + test_days),
            )
        )
        cursor += timedelta(days=step)
    return windows


def _equity_from_strategy(
    strategy: Strategy,
    bars: dict[str, pd.DataFrame],
    start: datetime,
    end: datetime,
    capital: float,
) -> pd.Series:
    """Run the strategy walk-forward through closes in [start, end] and return equity."""
    closes = pd.DataFrame({s: df["close"] for s, df in bars.items() if not df.empty})
    if closes.empty:
        return pd.Series(dtype=float)
    closes = closes.dropna(how="all").sort_index().loc[start:end]
    if closes.empty:
        return pd.Series(dtype=float)

    cash = capital
    positions: dict[str, float] = {}
    equity = []
    for ts, row in closes.iterrows():
        position_value = sum(qty * row.get(sym, 0.0) for sym, qty in positions.items())
        ctx = StrategyContext(
            now=ts.to_pydatetime(),
            cash=cash,
            positions=positions,
            bot_equity=capital,
            regime="chop",
        )
        try:
            targets = strategy.target_positions(ctx)
        except Exception:
            targets = []
        target_map = {t.symbol: t.weight for t in targets}
        for symbol in closes.columns:
            target_w = target_map.get(symbol, 0.0)
            target_notional = target_w * capital
            price = row.get(symbol, 0.0)
            if price <= 0:
                continue
            target_qty = target_notional / price
            current = positions.get(symbol, 0.0)
            delta = target_qty - current
            if abs(delta * price) < 1.0:
                continue
            cash -= delta * price
            positions[symbol] = current + delta
        equity.append((ts, cash + position_value))
    if not equity:
        return pd.Series(dtype=float)
    idx, vals = zip(*equity)
    return pd.Series(vals, index=pd.to_datetime(idx))


def walk_forward(
    strategy_factory: Callable[[dict[str, Any]], Strategy],
    *,
    universe: list[str],
    start: datetime,
    end: datetime,
    param_space: Callable[["optuna.Trial"], dict[str, Any]],  # noqa: F821 - lazy import
    n_trials: int = 30,
    train_days: int = 180,
    test_days: int = 30,
    capital: float = 25_000.0,
) -> WalkForwardResult:
    """Walk-forward optimize a strategy's params.

    `strategy_factory(params) -> Strategy` is your hook to construct the
    strategy with a candidate parameter set. `param_space(trial)` is an
    Optuna `suggest_*` block.
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    windows = make_windows(start, end, train_days, test_days)
    if not windows:
        raise ValueError(
            f"date range too short for train_days={train_days} test_days={test_days}"
        )

    log.info("walk-forward: %d windows, %d trials each", len(windows), n_trials)

    # Pull bars once for the whole range.
    span_days = (end - start).days + train_days + test_days
    bars = fetch_daily_bars(universe, lookback_days=span_days)

    per_window = []
    best_in_sample_sharpes = []
    oos_sharpes = []

    for w in windows:
        def _objective(trial):
            params = param_space(trial)
            strat = strategy_factory(params)
            eq = _equity_from_strategy(
                strat, bars, w.train_start, w.train_end, capital
            )
            return metrics.sharpe(eq) if not eq.empty else -1.0

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0))
        study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)
        best_params = study.best_params
        best_in_sample = float(study.best_value)

        # Apply best params to the OOS test window.
        oos_strat = strategy_factory(best_params)
        oos_eq = _equity_from_strategy(
            oos_strat, bars, w.test_start, w.test_end, capital
        )
        oos_sharpe = metrics.sharpe(oos_eq) if not oos_eq.empty else 0.0

        per_window.append(
            {
                "train_start": w.train_start.date().isoformat(),
                "train_end": w.train_end.date().isoformat(),
                "test_start": w.test_start.date().isoformat(),
                "test_end": w.test_end.date().isoformat(),
                "best_params": best_params,
                "in_sample_sharpe": best_in_sample,
                "oos_sharpe": oos_sharpe,
            }
        )
        best_in_sample_sharpes.append(best_in_sample)
        oos_sharpes.append(oos_sharpe)
        log.info(
            "window %s..%s  is=%.2f  oos=%.2f  params=%s",
            w.train_end.date(), w.test_end.date(),
            best_in_sample, oos_sharpe, best_params,
        )

    median_oos = float(np.median(oos_sharpes)) if oos_sharpes else 0.0
    best_is = float(np.median(best_in_sample_sharpes)) if best_in_sample_sharpes else 0.0
    overfit_gap = best_is - median_oos

    # The "best" params are the median across windows for stability — not the
    # highest-Sharpe single window (that's the brittle answer).
    final_params = _consensus_params(per_window)
    strategy_id = strategy_factory({}).id

    return WalkForwardResult(
        strategy_id=strategy_id,
        best_params=final_params,
        median_oos_sharpe=median_oos,
        best_in_sample_sharpe=best_is,
        overfit_gap=overfit_gap,
        per_window=per_window,
    )


def _consensus_params(per_window: list[dict]) -> dict[str, Any]:
    """For numeric params take the median across windows; for non-numeric, mode."""
    if not per_window:
        return {}
    keys = per_window[0]["best_params"].keys()
    out: dict[str, Any] = {}
    for k in keys:
        vals = [w["best_params"][k] for w in per_window]
        try:
            out[k] = float(np.median([float(v) for v in vals]))
        except (TypeError, ValueError):
            # Categorical — pick the most common.
            out[k] = max(set(vals), key=vals.count)
    return out
