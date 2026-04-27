"""Position sizing helpers.

Strategies don't have to use these — but if you want positions that
contribute roughly equal risk to a portfolio, this is the standard formula.

vol_target_weight(symbol_vol, target_annual_vol)
  weight scaled so that target_annual_vol / symbol_vol = relative size.

Example: target = 15% annual vol, AAPL recent realized = 30%, weight = 0.5.
A name with 60% vol would get 0.25. So the noisy stuff gets less capital,
sleepy stuff gets more — and each contributes the same dollar variance.

vol_target_qty()
  same idea but returns shares given a price + dollar budget per name.

These are pure functions; strategies call them inside target_positions().
"""
from __future__ import annotations

import logging

import pandas as pd

from src.data import features as F

log = logging.getLogger(__name__)

DEFAULT_TARGET_VOL = 0.15
DEFAULT_VOL_WINDOW = 20
MIN_VOL = 0.02   # 2% — guards against zero-vol division blow-ups
MAX_VOL = 1.50   # 150% — guards against weird thin tickers


def realized_vol(close: pd.Series, window: int = DEFAULT_VOL_WINDOW) -> float:
    """Annualized realized volatility — most recent value."""
    series = F.realized_vol(close, window=window).dropna()
    if series.empty:
        return DEFAULT_TARGET_VOL  # safe default if not enough data
    return float(min(max(series.iloc[-1], MIN_VOL), MAX_VOL))


def vol_target_weight(symbol_vol: float, target_annual_vol: float = DEFAULT_TARGET_VOL) -> float:
    """Risk-parity weight: target / symbol_vol, clamped to [0, 1]."""
    if symbol_vol <= 0:
        return 0.0
    return max(0.0, min(target_annual_vol / symbol_vol, 1.0))


def vol_target_qty(
    price: float,
    symbol_vol: float,
    dollar_budget: float,
    target_annual_vol: float = DEFAULT_TARGET_VOL,
) -> float:
    """Return shares to buy/sell to give this name vol-targeted exposure within a dollar budget."""
    if price <= 0 or dollar_budget <= 0:
        return 0.0
    notional = dollar_budget * vol_target_weight(symbol_vol, target_annual_vol)
    return notional / price


def equal_risk_weights(
    bars: dict[str, pd.DataFrame],
    target_annual_vol: float = DEFAULT_TARGET_VOL,
    vol_window: int = DEFAULT_VOL_WINDOW,
) -> dict[str, float]:
    """Compute vol-targeted weights for a basket of names. Sums to <= 1; renormalize if you need exact."""
    weights: dict[str, float] = {}
    for sym, df in bars.items():
        if df.empty or "close" not in df.columns or len(df) < vol_window + 1:
            continue
        v = realized_vol(df["close"], window=vol_window)
        weights[sym] = vol_target_weight(v, target_annual_vol)
    return weights


def normalize_to(weights: dict[str, float], total: float = 1.0) -> dict[str, float]:
    """Renormalize a weight dict so values sum to `total`."""
    s = sum(weights.values())
    if s <= 0:
        return weights
    return {k: v / s * total for k, v in weights.items()}
