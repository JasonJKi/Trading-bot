"""Risk-adjusted performance metrics, used by both the dashboard and the backtest harness."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(slots=True)
class PerfReport:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float


def _to_series(equity: pd.Series) -> pd.Series:
    if not isinstance(equity, pd.Series):
        equity = pd.Series(equity)
    return equity.astype(float).dropna()


def returns(equity: pd.Series) -> pd.Series:
    s = _to_series(equity)
    return s.pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    s = _to_series(equity)
    if len(s) < 2 or s.iloc[0] == 0:
        return 0.0
    return float(s.iloc[-1] / s.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    s = _to_series(equity)
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    days = max((s.index[-1] - s.index[0]).days, 1) if isinstance(s.index, pd.DatetimeIndex) else len(s)
    years = days / 365.25 if isinstance(s.index, pd.DatetimeIndex) else len(s) / TRADING_DAYS
    if years <= 0:
        return 0.0
    return float((s.iloc[-1] / s.iloc[0]) ** (1.0 / years) - 1.0)


def sharpe(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    r = returns(equity)
    if r.std() == 0 or len(r) < 2:
        return 0.0
    return float(r.mean() / r.std() * math.sqrt(periods_per_year))


def sortino(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    r = returns(equity)
    downside = r[r < 0]
    if downside.std() == 0 or len(r) < 2:
        return 0.0
    return float(r.mean() / downside.std() * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    s = _to_series(equity)
    if s.empty:
        return 0.0
    peak = s.cummax()
    dd = (s - peak) / peak
    return float(dd.min())


def drawdown_curve(equity: pd.Series) -> pd.Series:
    s = _to_series(equity)
    if s.empty:
        return s
    peak = s.cummax()
    return (s - peak) / peak


def trade_stats(pnls: pd.Series) -> tuple[float, float, float, float]:
    """Return win_rate, avg_win, avg_loss, expectancy from a series of trade PnLs."""
    p = pnls.dropna()
    if p.empty:
        return 0.0, 0.0, 0.0, 0.0
    wins = p[p > 0]
    losses = p[p < 0]
    win_rate = len(wins) / len(p)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return win_rate, avg_win, avg_loss, expectancy


def report(equity: pd.Series, pnls: pd.Series | None = None) -> PerfReport:
    if pnls is None:
        pnls = pd.Series(dtype=float)
    win_rate, avg_win, avg_loss, expectancy = trade_stats(pnls)
    return PerfReport(
        total_return=total_return(equity),
        cagr=cagr(equity),
        sharpe=sharpe(equity),
        sortino=sortino(equity),
        max_drawdown=max_drawdown(equity),
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
    )


def correlation_matrix(equity_by_strategy: dict[str, pd.Series]) -> pd.DataFrame:
    df = pd.DataFrame({k: returns(v) for k, v in equity_by_strategy.items()})
    if df.empty:
        return df
    return df.corr().fillna(0.0)
