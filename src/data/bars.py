"""Historical OHLCV fetcher used by both backtests and the live signal generation step.

Primary: yfinance (free, no API key). Alpaca is the broker; we keep data fetching
provider-agnostic here so backtests can run without Alpaca credentials.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)


def fetch_daily_bars(symbols: list[str], lookback_days: int = 250) -> dict[str, pd.DataFrame]:
    """Return {symbol: DataFrame[open, high, low, close, volume]} indexed by date.

    Falls back to an empty DataFrame for any symbol that fails so a single bad
    ticker doesn't take down a whole strategy cycle.
    """
    import yfinance as yf

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days * 2)  # buffer for weekends/holidays

    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            df = yf.download(
                symbol,
                start=start.date(),
                end=end.date() + timedelta(days=1),
                progress=False,
                auto_adjust=True,
                threads=False,
            )
        except Exception as exc:  # pragma: no cover - network-dependent
            log.warning("bar fetch failed for %s: %s", symbol, exc)
            out[symbol] = pd.DataFrame()
            continue

        if df is None or df.empty:
            out[symbol] = pd.DataFrame()
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        out[symbol] = df[keep].tail(lookback_days)
    return out
