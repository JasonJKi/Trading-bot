"""All HTTP routes. Read-only except for /auth/* and /bots/{id}/{pause,enable}."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import desc, func, select

from src.api import schemas
from src.api.auth import (
    auth_disabled,
    check_login_rate_limit,
    clear_cookie,
    issue_cookie,
    require_auth,
    reset_login_rate_limit,
    verify_password,
)
from src.config import get_settings
from src.core import metrics
from src.core.store import (
    AuditEvent,
    BotPosition,
    BotStatus,
    EquitySnapshot,
    Order,
    Signal,
    Trade,
    init_db,
    session_scope,
)

log = logging.getLogger(__name__)


# ---- public ---------------------------------------------------------------
public_router = APIRouter()


@public_router.get("/api/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    settings = get_settings()
    return schemas.HealthResponse(
        status="ok",
        ts=datetime.now(timezone.utc),
        mode="PAPER" if settings.alpaca_paper else "LIVE",
    )


@public_router.get("/api/auth/status")
def auth_status() -> dict:
    return {"required": not auth_disabled()}


@public_router.post("/api/auth/login", response_model=schemas.LoginResponse)
def login(
    body: schemas.LoginRequest, request: Request, response: Response
) -> schemas.LoginResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not check_login_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many attempts, try again later",
        )
    if not verify_password(body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad password")
    reset_login_rate_limit(client_ip)
    issue_cookie(response)
    return schemas.LoginResponse(ok=True)


@public_router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    clear_cookie(response)
    return {"ok": True}


# ---- protected ------------------------------------------------------------
router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/api/account", response_model=schemas.AccountResponse | None)
def account() -> schemas.AccountResponse | None:
    """Live account from Alpaca. Returns null if no creds or fetch fails."""
    settings = get_settings()
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        return None
    try:
        from alpaca.trading.client import TradingClient

        c = TradingClient(
            settings.alpaca_api_key, settings.alpaca_api_secret, paper=settings.alpaca_paper
        )
        a = c.get_account()
        equity = float(a.equity)
        last = float(a.last_equity)
        delta = equity - last
        delta_pct = (delta / last * 100) if last else 0.0
        return schemas.AccountResponse(
            equity=equity,
            last_equity=last,
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            portfolio_value=float(a.portfolio_value),
            status=str(a.status),
            delta=delta,
            delta_pct=delta_pct,
        )
    except Exception:
        log.exception("account fetch failed")
        return None


@router.get("/api/risk-caps", response_model=schemas.RiskCaps)
def risk_caps() -> schemas.RiskCaps:
    s = get_settings()
    return schemas.RiskCaps(
        per_bot_cap=s.per_bot_cap,
        per_position_pct=s.per_position_pct,
        global_max_drawdown=s.global_max_drawdown,
        per_bot_max_drawdown=s.per_bot_max_drawdown,
        starting_equity=s.account_starting_equity,
    )


def _next_run_for(schedule: dict) -> datetime | None:
    try:
        from apscheduler.triggers.cron import CronTrigger

        return CronTrigger(**schedule, timezone="UTC").get_next_fire_time(
            None, datetime.now(timezone.utc)
        )
    except Exception:
        return None


@router.get("/api/bots", response_model=list[schemas.BotInfo])
def bots() -> list[schemas.BotInfo]:
    from src.core.orchestrator import load_enabled_bots

    init_db()
    settings = get_settings()
    enabled = load_enabled_bots(settings)

    with session_scope() as sess:
        statuses = {
            row.strategy_id: row
            for row in sess.execute(select(BotStatus)).scalars().all()
        }
        sig_counts = dict(
            sess.execute(
                select(Signal.strategy_id, func.count(Signal.id)).group_by(Signal.strategy_id)
            ).all()
        )
        trade_counts = dict(
            sess.execute(
                select(Trade.strategy_id, func.count(Trade.id)).group_by(Trade.strategy_id)
            ).all()
        )

        out: list[schemas.BotInfo] = []
        for bot in enabled:
            try:
                universe = list(bot.universe())
            except Exception:
                universe = []
            st = statuses.get(bot.id)
            out.append(
                schemas.BotInfo(
                    id=bot.id,
                    name=bot.name,
                    version=str(bot.version),
                    schedule=dict(bot.schedule),
                    universe=universe,
                    state=st.state if st else "enabled",
                    reason=st.reason if st else "",
                    paper_validated_at=st.paper_validated_at if st else None,
                    next_run=_next_run_for(bot.schedule),
                    n_signals=int(sig_counts.get(bot.id, 0)),
                    n_trades=int(trade_counts.get(bot.id, 0)),
                )
            )
    return out


@router.get("/api/regime", response_model=schemas.RegimeResponse)
def regime() -> schemas.RegimeResponse:
    from src.core.regime import detect

    r = detect()
    return schemas.RegimeResponse(
        regime=r.label,
        spy_trend_pct=r.spy_trend * 100,
        vix=r.vix_level,
        term_structure=r.vix_term_ratio,
        breadth=r.breadth,
        correlation=r.avg_correlation,
        ts=r.ts,
    )


@router.get("/api/positions", response_model=list[schemas.PositionRow])
def positions() -> list[schemas.PositionRow]:
    """Live broker positions from Alpaca."""
    settings = get_settings()
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        return []
    try:
        from alpaca.trading.client import TradingClient

        c = TradingClient(
            settings.alpaca_api_key, settings.alpaca_api_secret, paper=settings.alpaca_paper
        )
        return [
            schemas.PositionRow(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_plpc=float(p.unrealized_plpc) * 100,
                side=str(p.side),
            )
            for p in c.get_all_positions()
        ]
    except Exception:
        log.exception("positions fetch failed")
        return []


@router.get("/api/bot-positions", response_model=list[schemas.BotPositionRow])
def bot_positions() -> list[schemas.BotPositionRow]:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(select(BotPosition)).scalars().all()
        return [
            schemas.BotPositionRow(
                strategy_id=r.strategy_id,
                symbol=r.symbol,
                qty=r.qty,
                avg_price=r.avg_price,
                cost_basis=r.cost_basis,
                opened_at=r.opened_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


@router.get("/api/orders", response_model=list[schemas.OrderRow])
def orders(limit: int = 200) -> list[schemas.OrderRow]:
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(select(Order).order_by(desc(Order.ts)).limit(limit)).scalars().all()
        )
        return [
            schemas.OrderRow(
                id=r.id,
                ts=r.ts,
                strategy_id=r.strategy_id,
                symbol=r.symbol,
                side=r.side,
                qty=r.qty,
                status=r.status,
                filled_qty=r.filled_qty,
                filled_avg_price=r.filled_avg_price,
                client_order_id=r.client_order_id,
                broker_order_id=r.broker_order_id,
                error=r.error or "",
            )
            for r in rows
        ]


@router.get("/api/trades", response_model=list[schemas.TradeRow])
def trades(limit: int = 200) -> list[schemas.TradeRow]:
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(select(Trade).order_by(desc(Trade.ts)).limit(limit)).scalars().all()
        )
        return [
            schemas.TradeRow(
                id=r.id,
                ts=r.ts,
                strategy_id=r.strategy_id,
                symbol=r.symbol,
                side=r.side,
                qty=r.qty,
                price=r.price,
                notional=r.notional,
                order_id=r.order_id or "",
            )
            for r in rows
        ]


@router.get("/api/signals", response_model=list[schemas.SignalRow])
def signals(limit: int = 200) -> list[schemas.SignalRow]:
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(select(Signal).order_by(desc(Signal.ts)).limit(limit)).scalars().all()
        )
        return [
            schemas.SignalRow(
                id=r.id,
                ts=r.ts,
                strategy_id=r.strategy_id,
                symbol=r.symbol,
                direction=r.direction,
                strength=r.strength,
                acted=r.acted,
            )
            for r in rows
        ]


@router.get("/api/equity", response_model=list[schemas.EquityPoint])
def equity() -> list[schemas.EquityPoint]:
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(select(EquitySnapshot).order_by(EquitySnapshot.ts)).scalars().all()
        )
        return [
            schemas.EquityPoint(
                ts=r.ts,
                strategy_id=r.strategy_id,
                cash=r.cash,
                position_value=r.position_value,
                total_equity=r.total_equity,
            )
            for r in rows
        ]


@router.get("/api/performance", response_model=list[schemas.PerformanceRow])
def performance() -> list[schemas.PerformanceRow]:
    """Per-bot risk-adjusted performance, computed from EquitySnapshot + Trade."""
    init_db()
    with session_scope() as sess:
        eq_rows = sess.execute(select(EquitySnapshot)).scalars().all()
        trade_rows = sess.execute(select(Trade)).scalars().all()

    if not eq_rows:
        return []

    eq_df = pd.DataFrame(
        [{"ts": r.ts, "strategy_id": r.strategy_id, "total_equity": r.total_equity} for r in eq_rows]
    )
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

    out: list[schemas.PerformanceRow] = []
    for sid in sorted(eq_df["strategy_id"].unique()):
        sub = eq_df[eq_df["strategy_id"] == sid].sort_values("ts")
        eq = pd.Series(sub["total_equity"].values, index=pd.to_datetime(sub["ts"]))
        pnls = _trade_pnls(tr_df[tr_df["strategy_id"] == sid]) if not tr_df.empty else pd.Series(dtype=float)
        r = metrics.report(eq, pnls)
        out.append(
            schemas.PerformanceRow(
                strategy_id=sid,
                total_return=r.total_return,
                cagr=r.cagr,
                sharpe=r.sharpe,
                sortino=r.sortino,
                max_drawdown=r.max_drawdown,
                win_rate=r.win_rate,
                expectancy=r.expectancy,
            )
        )
    return out


def _trade_pnls(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    pnls: list[float] = []
    for (_, _), grp in trades.groupby(["strategy_id", "symbol"]):
        grp = grp.sort_values("ts")
        position = 0.0
        cost = 0.0
        for _, row in grp.iterrows():
            if row["side"] == "buy":
                cost += row["qty"] * row["price"]
                position += row["qty"]
            else:
                if position > 0:
                    avg = cost / position
                    pnls.append(row["qty"] * (row["price"] - avg))
                    position -= row["qty"]
                    cost -= row["qty"] * avg
    return pd.Series(pnls)


@router.get("/api/audit", response_model=list[schemas.AuditRow])
def audit(limit: int = 200) -> list[schemas.AuditRow]:
    init_db()
    with session_scope() as sess:
        rows = (
            sess.execute(select(AuditEvent).order_by(desc(AuditEvent.ts)).limit(limit))
            .scalars()
            .all()
        )
        return [
            schemas.AuditRow(
                id=r.id,
                ts=r.ts,
                kind=r.kind,
                severity=r.severity,
                strategy_id=r.strategy_id or "",
                message=r.message,
            )
            for r in rows
        ]
