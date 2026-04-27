"""Market regime classifier.

Output is one of: bull / bear / chop / crisis. Strategies can read the
current regime via the orchestrator's StrategyContext or by calling
`detect()` directly.

Why rule-based and not an HMM:
  - We only have hundreds of datapoints (daily bars over a few years).
  - Rule-based regimes are interpretable; HMM states need post-hoc
    labeling and tend to flip-flop on noisy days.
  - This is a baseline. Plug an HMM in later if you have a reason.

Inputs (all pulled via yfinance, cached):
  - SPY trend slope (50d MA vs 200d MA)
  - VIX level + 1m vs 3m term structure (^VIX vs ^VIX3M)
  - Realized correlation across a basket (proxy for systemic stress)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.data import features as F
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)

REGIMES = ("bull", "bear", "chop", "crisis")


@dataclass(slots=True, frozen=True)
class Regime:
    label: str
    spy_trend: float           # 50d MA / 200d MA - 1; positive = uptrend
    vix_level: float
    vix_term_ratio: float      # VIX / VIX3M; > 1 means front-month elevated -> stress
    breadth: float             # fraction above 200d MA across the basket
    avg_correlation: float
    ts: datetime


_BASKET = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "JPM"]
_CACHE: dict[str, tuple[datetime, Regime]] = {}
_CACHE_TTL = timedelta(hours=1)


def _classify(spy_trend: float, vix: float, term_ratio: float, breadth: float, corr: float) -> str:
    """The actual rule. Tunable later via the optimizer."""
    if vix > 35 or (term_ratio > 1.05 and corr > 0.65):
        return "crisis"
    if spy_trend < -0.02 and breadth < 0.4:
        return "bear"
    if spy_trend > 0.02 and breadth > 0.55 and vix < 22:
        return "bull"
    return "chop"


def detect(force_refresh: bool = False) -> Regime:
    """Compute the current regime. Cached for an hour."""
    cached = _CACHE.get("regime")
    if cached and not force_refresh and datetime.now(timezone.utc) - cached[0] < _CACHE_TTL:
        return cached[1]

    bars = fetch_daily_bars(_BASKET + ["^VIX", "^VIX3M"], lookback_days=260)

    spy = bars.get("SPY", pd.DataFrame())
    if spy.empty or len(spy) < 200:
        log.warning("regime detect: not enough SPY history; returning chop")
        regime = Regime("chop", 0, 0, 0, 0, 0, datetime.now(timezone.utc))
        _CACHE["regime"] = (datetime.now(timezone.utc), regime)
        return regime

    spy_close = spy["close"]
    spy_50 = spy_close.rolling(50).mean().iloc[-1]
    spy_200 = spy_close.rolling(200).mean().iloc[-1]
    spy_trend = float(spy_50 / spy_200 - 1.0) if spy_200 else 0.0

    vix_df = bars.get("^VIX", pd.DataFrame())
    vix3m_df = bars.get("^VIX3M", pd.DataFrame())
    vix_level = float(vix_df["close"].iloc[-1]) if not vix_df.empty else 18.0
    vix3m_level = float(vix3m_df["close"].iloc[-1]) if not vix3m_df.empty else 20.0
    term_ratio = vix_level / vix3m_level if vix3m_level else 1.0

    closes = pd.DataFrame({s: bars[s]["close"] for s in _BASKET if s in bars and not bars[s].empty})
    if closes.empty:
        breadth = 0.5
        avg_corr = 0.5
    else:
        breadth = float(F.breadth(closes, ma_window=200).iloc[-1])
        avg_corr_series = F.average_correlation(closes, window=60)
        avg_corr = float(avg_corr_series.dropna().iloc[-1]) if not avg_corr_series.dropna().empty else 0.5

    label = _classify(spy_trend, vix_level, term_ratio, breadth, avg_corr)
    regime = Regime(
        label=label,
        spy_trend=spy_trend,
        vix_level=vix_level,
        vix_term_ratio=term_ratio,
        breadth=breadth,
        avg_correlation=avg_corr,
        ts=datetime.now(timezone.utc),
    )
    _CACHE["regime"] = (datetime.now(timezone.utc), regime)
    log.info(
        "regime=%s spy_trend=%.2f%% vix=%.1f term=%.2f breadth=%.2f corr=%.2f",
        label, spy_trend * 100, vix_level, term_ratio, breadth, avg_corr,
    )
    return regime


def reset_cache() -> None:
    """For tests."""
    _CACHE.clear()
