"""Public, read-only bot tear sheets — mounted at /api/public/*.

These endpoints are unauthenticated by design and intended for the public
per-bot pages on bot.67quant.com. The line we draw (per
docs/roadmap.md Phase 24): **show track records, hide playbooks.**

Public:                          | Private (auth-gated, in src/api/routes.py):
  - equity curve since inception | - current positions
  - aggregate stats              | - live signals (pre-execution)
    (return, Sharpe, DD, win %)  | - strategy parameters
  - 1-line description           | - specific tickers held right now
  - trades, T+`PUBLIC_TRADE_      | - allocation knobs / risk caps
    DELAY_DAYS` and older        | - audit log

The trade-delay is the load-bearing knob — a copyable real-time fill feed
would defeat the purpose; T+1 historical record is transparent without
leaking edge.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from src.api import schemas
from src.api.routes import _trade_pnls
from src.config import get_settings
from src.core import metrics
from src.core.store import (
    BotStatus,
    EquitySnapshot,
    Trade,
    init_db,
    session_scope,
)

public_bot_router = APIRouter(prefix="/api/public", tags=["public"])


def _delay_cutoff() -> datetime:
    """Trades with `ts >= this` are hidden from public endpoints."""
    return datetime.now(timezone.utc) - timedelta(
        days=get_settings().public_trade_delay_days
    )


def _enabled_bots():
    from src.core.orchestrator import load_enabled_bots

    return load_enabled_bots(get_settings())


def _bot_or_404(bot_id: str):
    """Resolve a bot id to its Strategy instance, or raise 404."""
    for bot in _enabled_bots():
        if bot.id == bot_id:
            return bot
    raise HTTPException(status_code=404, detail="bot not found")


def _perf_for(strategy_id: str) -> metrics.PerfReport | None:
    """Compute the same metrics report `/api/performance` returns, but scoped to
    one bot and with trades filtered to the public delay window."""
    cutoff = _delay_cutoff()
    init_db()
    with session_scope() as sess:
        eq_rows = (
            sess.execute(
                select(EquitySnapshot)
                .where(EquitySnapshot.strategy_id == strategy_id)
                .order_by(EquitySnapshot.ts)
            )
            .scalars()
            .all()
        )
        trade_rows = (
            sess.execute(
                select(Trade)
                .where(Trade.strategy_id == strategy_id, Trade.ts < cutoff)
                .order_by(Trade.ts)
            )
            .scalars()
            .all()
        )
    if not eq_rows:
        return None
    eq = pd.Series(
        [r.total_equity for r in eq_rows],
        index=pd.to_datetime([r.ts for r in eq_rows]),
    )
    if trade_rows:
        tr_df = pd.DataFrame(
            [
                {
                    "ts": r.ts,
                    "strategy_id": r.strategy_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "qty": r.qty,
                    "price": r.price,
                }
                for r in trade_rows
            ]
        )
        pnls = _trade_pnls(tr_df)
    else:
        pnls = pd.Series(dtype=float)
    return metrics.report(eq, pnls)


def _state_of(strategy_id: str) -> str:
    with session_scope() as sess:
        st = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == strategy_id)
        ).scalar_one_or_none()
    return st.state if st else "enabled"


def _public_n_trades(strategy_id: str) -> int:
    cutoff = _delay_cutoff()
    with session_scope() as sess:
        return int(
            sess.execute(
                select(func.count(Trade.id)).where(
                    Trade.strategy_id == strategy_id, Trade.ts < cutoff
                )
            ).scalar_one()
        )


@public_bot_router.get(
    "/bots", response_model=list[schemas.PublicBotInfo], operation_id="list_public_bots"
)
def list_public_bots() -> list[schemas.PublicBotInfo]:
    """All enabled bots' tear-sheet stats, ordered by id."""
    out: list[schemas.PublicBotInfo] = []
    for bot in sorted(_enabled_bots(), key=lambda b: b.id):
        rep = _perf_for(bot.id)
        out.append(
            schemas.PublicBotInfo(
                id=bot.id,
                name=bot.name,
                description=bot.description,
                version=str(bot.version),
                state=_state_of(bot.id),
                total_return=rep.total_return if rep else 0.0,
                sharpe=rep.sharpe if rep else 0.0,
                max_drawdown=rep.max_drawdown if rep else 0.0,
                win_rate=rep.win_rate if rep else 0.0,
                n_trades=_public_n_trades(bot.id),
            )
        )
    return out


@public_bot_router.get(
    "/bots/{bot_id}",
    response_model=schemas.PublicBotDetail,
    operation_id="get_public_bot",
)
def get_public_bot(bot_id: str) -> schemas.PublicBotDetail:
    """Single-bot detail page. Adds CAGR, Sortino, expectancy, inception."""
    bot = _bot_or_404(bot_id)
    rep = _perf_for(bot.id)
    init_db()
    with session_scope() as sess:
        first_eq = sess.execute(
            select(EquitySnapshot)
            .where(EquitySnapshot.strategy_id == bot.id)
            .order_by(EquitySnapshot.ts)
            .limit(1)
        ).scalar_one_or_none()
        st = sess.execute(
            select(BotStatus).where(BotStatus.strategy_id == bot.id)
        ).scalar_one_or_none()
    return schemas.PublicBotDetail(
        id=bot.id,
        name=bot.name,
        description=bot.description,
        version=str(bot.version),
        state=st.state if st else "enabled",
        total_return=rep.total_return if rep else 0.0,
        sharpe=rep.sharpe if rep else 0.0,
        max_drawdown=rep.max_drawdown if rep else 0.0,
        win_rate=rep.win_rate if rep else 0.0,
        n_trades=_public_n_trades(bot.id),
        cagr=rep.cagr if rep else 0.0,
        sortino=rep.sortino if rep else 0.0,
        expectancy=rep.expectancy if rep else 0.0,
        paper_validated_at=st.paper_validated_at if st else None,
        inception=first_eq.ts if first_eq else None,
    )


@public_bot_router.get(
    "/bots/{bot_id}/equity",
    response_model=list[schemas.PublicEquityPoint],
    operation_id="get_public_bot_equity",
)
def get_public_bot_equity(bot_id: str) -> list[schemas.PublicEquityPoint]:
    """Equity curve for one bot. Not delay-filtered — only trades are."""
    _bot_or_404(bot_id)
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(
                select(EquitySnapshot)
                .where(EquitySnapshot.strategy_id == bot_id)
                .order_by(EquitySnapshot.ts)
            )
            .scalars()
            .all()
        )
    return [
        schemas.PublicEquityPoint(ts=r.ts, total_equity=r.total_equity) for r in rows
    ]


@public_bot_router.get(
    "/bots/{bot_id}/trades",
    response_model=list[schemas.PublicTradeRow],
    operation_id="get_public_bot_trades",
)
def get_public_bot_trades(
    bot_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[schemas.PublicTradeRow]:
    """Trades for one bot, T+`PUBLIC_TRADE_DELAY_DAYS` and older.

    Newest-first. Hard cap of 2000 to avoid unbounded responses; the public
    surface isn't meant to be a bulk-export channel.
    """
    _bot_or_404(bot_id)
    cutoff = _delay_cutoff()
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(
                select(Trade)
                .where(Trade.strategy_id == bot_id, Trade.ts < cutoff)
                .order_by(desc(Trade.ts))
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [
        schemas.PublicTradeRow(
            ts=r.ts,
            symbol=r.symbol,
            side=r.side,
            qty=r.qty,
            price=r.price,
            notional=r.qty * r.price,
        )
        for r in rows
    ]
