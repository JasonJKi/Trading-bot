"""Vectorbt-based backtest harness. Reuses the same Strategy classes as live trading.

Each strategy is asked for target_positions on every historical bar, and the resulting
weights are turned into vectorbt-compatible entry/exit signals. Output is a printable
performance report plus a saved CSV.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config import get_settings
from src.core import metrics
from src.core.strategy import Strategy, StrategyContext
from src.data.bars import fetch_daily_bars

log = logging.getLogger(__name__)


def _instantiate(strategy_id: str) -> Strategy:
    from src.bots.congress import CongressStrategy
    from src.bots.mean_reversion import MeanReversionStrategy
    from src.bots.momentum import MomentumStrategy
    from src.bots.sentiment import SentimentStrategy

    registry = {
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "congress": CongressStrategy,
        "sentiment": SentimentStrategy,
    }
    cls = registry.get(strategy_id)
    if cls is None:
        raise SystemExit(f"unknown strategy: {strategy_id}")
    return cls()


def run(strategy_id: str, start: str, end: str, capital: float = 25_000.0) -> pd.DataFrame:
    """Walk forward through history, calling target_positions() on each bar.

    Returns an equity DataFrame indexed by date with one column per symbol traded.
    """
    strategy = _instantiate(strategy_id)
    universe = strategy.universe()
    if not universe:
        log.warning("strategy %s has empty universe — nothing to test", strategy_id)
        return pd.DataFrame()

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    days = (end_dt - start_dt).days + 30
    bars = fetch_daily_bars(universe, lookback_days=days)

    # Build aligned closing-price frame.
    closes = pd.DataFrame({s: df["close"] for s, df in bars.items() if not df.empty})
    closes = closes.dropna(how="all").sort_index()
    closes = closes.loc[start_dt:end_dt]
    if closes.empty:
        log.warning("no bar data in range")
        return pd.DataFrame()

    weights = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    cash = capital
    positions: dict[str, float] = {}

    equity_curve = []
    for ts, row in closes.iterrows():
        # Mark-to-market.
        position_value = sum(qty * row.get(sym, 0.0) for sym, qty in positions.items())
        equity = cash + position_value

        ctx = StrategyContext(now=ts, cash=cash, positions=positions, bot_equity=capital)
        try:
            targets = strategy.target_positions(ctx)
        except Exception:
            log.exception("strategy %s failed at %s", strategy_id, ts)
            targets = []

        target_map = {t.symbol: t.weight for t in targets}
        # Rebalance to target weights using current row's close as fill price.
        for symbol in closes.columns:
            target_weight = target_map.get(symbol, 0.0)
            target_notional = target_weight * capital
            price = row.get(symbol, 0.0)
            if price <= 0:
                continue
            target_qty = target_notional / price
            current_qty = positions.get(symbol, 0.0)
            delta = target_qty - current_qty
            if abs(delta * price) < 1.0:
                continue
            cash -= delta * price
            positions[symbol] = current_qty + delta

        weights.loc[ts] = pd.Series({s: target_map.get(s, 0.0) for s in closes.columns})
        equity_curve.append((ts, cash, position_value, equity))

    df = pd.DataFrame(equity_curve, columns=["ts", "cash", "position_value", "total_equity"]).set_index("ts")
    return df


def main() -> None:  # pragma: no cover - CLI
    logging.basicConfig(level=get_settings().log_level)
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--capital", type=float, default=25_000.0)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    df = run(args.strategy, args.start, args.end, args.capital)
    if df.empty:
        print("no results")
        return

    rep = metrics.report(df["total_equity"])
    print(f"strategy: {args.strategy}")
    print(f"total return: {rep.total_return * 100:.2f}%")
    print(f"CAGR:         {rep.cagr * 100:.2f}%")
    print(f"Sharpe:       {rep.sharpe:.2f}")
    print(f"Sortino:      {rep.sortino:.2f}")
    print(f"max DD:       {rep.max_drawdown * 100:.2f}%")

    out = Path(args.out or f"data/backtest_{args.strategy}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out)
    print(f"saved {out}")


if __name__ == "__main__":  # pragma: no cover
    main()
