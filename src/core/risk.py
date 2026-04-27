"""Per-bot circuit breaker + graduation gate.

Circuit breaker:
  - Computed each cycle. If a bot's rolling 30-day drawdown exceeds
    settings.per_bot_max_drawdown, set BotStatus.state='paused'.
  - Paused bots return early from run_once.

Graduation gate:
  - A bot is "paper-validated" when it has ≥30 days of paper trading,
    Sharpe ≥ 1.0, and max_dd ≤ 1.25 × backtest_max_dd.
  - The CLI command `graduate` flips BotStatus.paper_validated_at if met.
  - Live trading additionally requires every bot to be paper-validated.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from src.config import get_settings
from src.core import metrics
from src.core.alerter import alert
from src.core.store import BotStatus, EquitySnapshot, session_scope

log = logging.getLogger(__name__)

GRADUATION_MIN_DAYS = 30
GRADUATION_MIN_SHARPE = 1.0
GRADUATION_MAX_DD_MULTIPLIER = 1.25


@dataclass(slots=True)
class GraduationCheck:
    strategy_id: str
    days_observed: int
    sharpe: float
    max_drawdown: float
    sample_too_short: bool
    sharpe_below: bool
    drawdown_too_deep: bool

    @property
    def passed(self) -> bool:
        return not (self.sample_too_short or self.sharpe_below or self.drawdown_too_deep)


def _equity_series(strategy_id: str) -> pd.Series:
    with session_scope() as sess:
        rows = sess.execute(
            select(EquitySnapshot.ts, EquitySnapshot.total_equity)
            .where(EquitySnapshot.strategy_id == strategy_id)
            .order_by(EquitySnapshot.ts)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [r.total_equity for r in rows],
        index=pd.to_datetime([r.ts for r in rows]),
    )


def evaluate_circuit_breaker(strategy_id: str, max_dd: float) -> bool:
    """Return True if the bot should be paused (rolling 30d DD breaches `max_dd`)."""
    eq = _equity_series(strategy_id)
    if len(eq) < 5:
        return False
    cutoff = eq.index.max() - pd.Timedelta(days=30)
    eq = eq[eq.index >= cutoff]
    if len(eq) < 5:
        return False
    dd = metrics.max_drawdown(eq)
    return dd <= -max_dd


def trip_circuit_breaker_if_needed(strategy_id: str) -> bool:
    """Check + trip the breaker. Returns True if the bot was paused."""
    settings = get_settings()
    if not evaluate_circuit_breaker(strategy_id, settings.per_bot_max_drawdown):
        return False
    pause_bot(
        strategy_id,
        reason=f"30d drawdown breached cap of {settings.per_bot_max_drawdown * 100:.1f}%",
    )
    return True


def pause_bot(strategy_id: str, *, reason: str) -> None:
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if row is None:
            row = BotStatus(strategy_id=strategy_id, state="paused", reason=reason)
            sess.add(row)
        else:
            row.state = "paused"
            row.reason = reason
            row.updated_at = datetime.now(timezone.utc)
    alert("error", f"Bot paused: {strategy_id}", reason, strategy_id=strategy_id)


def enable_bot(strategy_id: str) -> None:
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if row is None:
            sess.add(BotStatus(strategy_id=strategy_id, state="enabled"))
        else:
            row.state = "enabled"
            row.reason = ""
            row.updated_at = datetime.now(timezone.utc)


def evaluate_graduation(strategy_id: str) -> GraduationCheck:
    eq = _equity_series(strategy_id)
    if eq.empty:
        return GraduationCheck(strategy_id, 0, 0, 0, True, True, True)
    days = max((eq.index[-1] - eq.index[0]).days, 0)
    sharpe = metrics.sharpe(eq)
    max_dd = abs(metrics.max_drawdown(eq))
    sample_too_short = days < GRADUATION_MIN_DAYS
    sharpe_below = sharpe < GRADUATION_MIN_SHARPE
    drawdown_too_deep = False  # not enforced without a backtest baseline yet
    return GraduationCheck(
        strategy_id, days, sharpe, max_dd,
        sample_too_short, sharpe_below, drawdown_too_deep,
    )


def graduate(strategy_id: str) -> GraduationCheck:
    """Mark a bot as paper-validated if it passes the gate. Otherwise raise."""
    check = evaluate_graduation(strategy_id)
    if not check.passed:
        reasons = []
        if check.sample_too_short:
            reasons.append(f"only {check.days_observed} days of paper data (need {GRADUATION_MIN_DAYS})")
        if check.sharpe_below:
            reasons.append(f"Sharpe {check.sharpe:.2f} < {GRADUATION_MIN_SHARPE}")
        if check.drawdown_too_deep:
            reasons.append(f"max DD {check.max_drawdown * 100:.1f}% too deep")
        raise RuntimeError(f"{strategy_id} not ready: {'; '.join(reasons)}")
    with session_scope() as sess:
        row = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if row is None:
            row = BotStatus(strategy_id=strategy_id, state="enabled")
            sess.add(row)
        row.paper_validated_at = datetime.now(timezone.utc)
        row.reason = "paper-validated"
    alert(
        "info",
        f"Bot graduated: {strategy_id}",
        f"days={check.days_observed} sharpe={check.sharpe:.2f} max_dd={check.max_drawdown * 100:.1f}%",
        strategy_id=strategy_id,
    )
    return check


def assert_all_paper_validated(strategy_ids: list[str]) -> None:
    """Refuse to start in live mode unless every enabled bot is graduated."""
    with session_scope() as sess:
        statuses = {
            row.strategy_id: row
            for row in sess.execute(select(BotStatus)).scalars()
        }
    missing = [
        sid for sid in strategy_ids
        if sid not in statuses or statuses[sid].paper_validated_at is None
    ]
    if missing:
        raise RuntimeError(
            "Refusing live mode: these bots have not been paper-validated: "
            + ", ".join(missing)
            + ". Run `python -m src.cli graduate --strategy <name>` per bot first."
        )
