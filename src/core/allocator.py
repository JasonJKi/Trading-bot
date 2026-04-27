"""Dynamic per-bot capital allocator.

The static ``settings.per_bot_cap`` says "every bot gets $25k forever". That
ignores recent performance. A self-learning allocator reweights capital
toward bots with strong rolling Sharpe.

Algorithm: softmax(rolling_30d_sharpe / temperature), normalized to the
total bot allocation. Floor + ceiling caps prevent any single bot from
hogging or starving.

Why softmax-Sharpe and not full Bayesian Thompson sampling:
  - Sharpe captures risk-adjusted return, which is what we actually care
    about, not raw return.
  - Softmax has one knob (temperature) and is interpretable.
  - At our cycle frequency (daily) the differential alpha across bots is
    too small for full posterior tracking to matter.

Refresh cadence: once per day, not every cycle. Daily reallocation of a
$100k account at zero commission is fine; intraday churn would eat any
attribution edge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select

from src.config import get_settings
from src.core import metrics
from src.core.store import EquitySnapshot, session_scope

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Allocation:
    strategy_id: str
    weight: float        # 0..1, normalized across the active set
    capital: float       # absolute dollars
    sharpe_30d: float
    rationale: str


def _equity_window(strategy_id: str, days: int) -> pd.Series:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as sess:
        rows = sess.execute(
            select(EquitySnapshot.ts, EquitySnapshot.total_equity)
            .where(EquitySnapshot.strategy_id == strategy_id)
            .where(EquitySnapshot.ts >= cutoff)
            .order_by(EquitySnapshot.ts)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [r.total_equity for r in rows],
        index=pd.to_datetime([r.ts for r in rows]),
    )


def allocate(
    strategy_ids: list[str],
    *,
    total_capital: float | None = None,
    temperature: float = 0.5,
    floor_pct: float = 0.05,
    ceiling_pct: float = 0.5,
    lookback_days: int = 30,
    min_observations: int = 10,
) -> list[Allocation]:
    """Compute current allocation across `strategy_ids`.

    Bots without enough history get the equal-weight share. Once they've
    accumulated `min_observations` snapshots, they participate in the
    softmax reweighting.
    """
    settings = get_settings()
    if total_capital is None:
        total_capital = settings.per_bot_cap * max(len(strategy_ids), 1)

    sharpes: dict[str, float] = {}
    inactive: list[str] = []
    for sid in strategy_ids:
        eq = _equity_window(sid, lookback_days)
        if len(eq) < min_observations:
            inactive.append(sid)
            continue
        sharpes[sid] = metrics.sharpe(eq)

    n = len(strategy_ids)
    if not sharpes:
        # No history yet — equal weight.
        equal = 1.0 / n
        return [
            Allocation(
                strategy_id=sid,
                weight=equal,
                capital=total_capital * equal,
                sharpe_30d=0.0,
                rationale="bootstrap (no history)",
            )
            for sid in strategy_ids
        ]

    # Softmax over Sharpe for active bots.
    arr = np.array(list(sharpes.values()))
    scaled = arr / max(temperature, 1e-9)
    # Stable softmax.
    weights = np.exp(scaled - scaled.max())
    weights = weights / weights.sum()
    active_weights = dict(zip(sharpes.keys(), weights))

    # Inactive bots get a small bootstrap share each; active bots scale to fill the rest.
    bootstrap_share = floor_pct
    active_share = 1.0 - bootstrap_share * len(inactive)
    active_share = max(active_share, 0.0)

    final: dict[str, float] = {}
    for sid, w in active_weights.items():
        final[sid] = w * active_share
    for sid in inactive:
        final[sid] = bootstrap_share

    # Apply floor + ceiling.
    final = _apply_floor_ceiling(final, floor_pct, ceiling_pct)

    return [
        Allocation(
            strategy_id=sid,
            weight=final[sid],
            capital=total_capital * final[sid],
            sharpe_30d=sharpes.get(sid, 0.0),
            rationale=("inactive bootstrap" if sid in inactive else "softmax(sharpe)"),
        )
        for sid in strategy_ids
    ]


def _apply_floor_ceiling(
    weights: dict[str, float], floor: float, ceiling: float
) -> dict[str, float]:
    """Enforce floor + ceiling with iterative redistribution.

    Approach: at each step (a) cap above-ceiling values and spread the excess
    pro-rata to under-ceiling values, (b) raise below-floor values and take
    pro-rata from above-floor values. Repeat until stable. Sum is preserved.
    """
    keys = list(weights)
    n = len(keys)
    if n == 0:
        return weights
    floor = min(floor, 1.0 / n)        # ensure feasibility
    if ceiling * n < 1.0:
        ceiling = 1.0 / n

    s = sum(weights.values()) or 1.0
    w = {k: weights[k] / s for k in keys}

    for _ in range(50):
        # 1) cap to ceiling
        excess = 0.0
        for k in keys:
            if w[k] > ceiling:
                excess += w[k] - ceiling
                w[k] = ceiling
        free = [k for k in keys if w[k] < ceiling - 1e-9]
        if excess > 1e-9 and free:
            share = excess / len(free)
            for k in free:
                w[k] = min(ceiling, w[k] + share)
        # 2) raise to floor
        deficit = 0.0
        for k in keys:
            if w[k] < floor:
                deficit += floor - w[k]
                w[k] = floor
        free = [k for k in keys if w[k] > floor + 1e-9]
        if deficit > 1e-9 and free:
            share = deficit / len(free)
            for k in free:
                w[k] = max(floor, w[k] - share)
        # 3) check convergence
        viol = max(
            max((w[k] - ceiling for k in keys), default=0.0),
            max((floor - w[k] for k in keys), default=0.0),
        )
        if viol < 1e-9:
            break
    return w
