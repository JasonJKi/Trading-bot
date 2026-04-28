"""Response models — keep these stable; the Next.js client is generated from them."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    ts: datetime
    mode: str  # "PAPER" or "LIVE"


class AccountResponse(BaseModel):
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    status: str
    delta: float
    delta_pct: float


class RiskCaps(BaseModel):
    per_bot_cap: float
    per_position_pct: float
    global_max_drawdown: float
    per_bot_max_drawdown: float
    starting_equity: float


class BotInfo(BaseModel):
    id: str
    name: str
    version: str
    schedule: dict
    universe: list[str]
    state: str  # enabled / paused / disabled
    reason: str
    paper_validated_at: datetime | None
    next_run: datetime | None
    n_signals: int
    n_trades: int


class RegimeResponse(BaseModel):
    regime: str
    spy_trend_pct: float
    vix: float
    term_structure: float
    breadth: float
    correlation: float
    ts: datetime


class PositionRow(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str


class BotPositionRow(BaseModel):
    strategy_id: str
    symbol: str
    qty: float
    avg_price: float
    cost_basis: float
    opened_at: datetime
    updated_at: datetime


class OrderRow(BaseModel):
    id: int
    ts: datetime
    strategy_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float
    filled_avg_price: float
    client_order_id: str
    broker_order_id: str
    error: str


class TradeRow(BaseModel):
    id: int
    ts: datetime
    strategy_id: str
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    order_id: str


class SignalRow(BaseModel):
    id: int
    ts: datetime
    strategy_id: str
    symbol: str
    direction: str
    strength: float
    acted: int


class EquityPoint(BaseModel):
    ts: datetime
    strategy_id: str
    cash: float
    position_value: float
    total_equity: float


class PerformanceRow(BaseModel):
    strategy_id: str
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float
    expectancy: float


class AuditRow(BaseModel):
    id: int
    ts: datetime
    kind: str
    severity: str
    strategy_id: str
    message: str


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    ok: bool


# === Public bot tear sheets (Phase 24) ===
# Trimmed, redacted views safe to expose without auth. Aligns with the
# public/private boundary documented in docs/roadmap.md Phase 24:
# show track records, hide playbooks.

class PublicBotInfo(BaseModel):
    """Index-page entry. Aggregate stats; no parameters, positions, or signals."""
    id: str
    name: str
    description: str
    version: str
    state: str  # enabled / paused / disabled
    total_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    n_trades: int  # public count (after PUBLIC_TRADE_DELAY_DAYS filter)


class PublicBotDetail(PublicBotInfo):
    """Detail view — adds CAGR, Sortino, expectancy. Same redactions."""
    cagr: float
    sortino: float
    expectancy: float
    paper_validated_at: datetime | None
    inception: datetime | None  # ts of the earliest equity snapshot


class PublicEquityPoint(BaseModel):
    """Single equity-curve sample. No cash/position-value split — those leak
    intra-day mechanics; only the headline number is public."""
    ts: datetime
    total_equity: float


class PublicTradeRow(BaseModel):
    """Trade row, redacted to public-safe fields. Strategy/order metadata
    omitted; only surfaces after the PUBLIC_TRADE_DELAY_DAYS window."""
    ts: datetime
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
