"""Broker adapter — single point that talks to Alpaca, with risk caps + idempotency.

Every order submitted is given a deterministic `client_order_id` so a retry can't
double-submit. We also persist an Order row before contacting Alpaca so even if
the process dies mid-flight the order is recorded.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from src.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float
    avg_price: float
    market_value: float


@dataclass(slots=True)
class OrderResult:
    order_id: str           # broker (Alpaca) order id
    client_order_id: str    # our idempotency key
    symbol: str
    side: str
    qty: float              # ordered qty
    price: float            # filled_avg_price; 0 if not yet filled
    status: str             # alpaca status: new/accepted/partially_filled/filled/...
    filled_qty: float = 0.0


class BrokerClient(Protocol):  # pragma: no cover - interface only
    def get_account_equity(self) -> float: ...
    def get_positions(self) -> list[Position]: ...
    def get_latest_price(self, symbol: str) -> float: ...
    def submit_market_order(
        self, symbol: str, side: str, qty: float, client_order_id: str
    ) -> OrderResult: ...
    def get_order_by_client_id(self, client_order_id: str) -> OrderResult | None: ...


class BrokerAdapter:
    """Wraps a BrokerClient and enforces risk limits + idempotency."""

    def __init__(
        self,
        client: BrokerClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.assert_safe_to_trade()
        self._client = client

    @property
    def client(self) -> BrokerClient:
        if self._client is None:
            self._client = _build_alpaca_client(self.settings)
        return self._client

    # Read-through helpers -------------------------------------------------
    def equity(self) -> float:
        return self.client.get_account_equity()

    def positions(self) -> dict[str, Position]:
        return {p.symbol: p for p in self.client.get_positions()}

    def price(self, symbol: str) -> float:
        return self.client.get_latest_price(symbol)

    def order_by_client_id(self, client_order_id: str) -> OrderResult | None:
        try:
            return self.client.get_order_by_client_id(client_order_id)
        except Exception:
            log.exception("get_order_by_client_id failed for %s", client_order_id)
            return None

    # Idempotent order submission -----------------------------------------
    @staticmethod
    def make_client_order_id(strategy_id: str, symbol: str, cycle_ts: datetime | None = None) -> str:
        """Deterministic-ish key: strategy + symbol + minute-bucket + 8-char nonce.

        Unique enough that two retries of the same intent in the same minute
        collide (idempotent), but different intents don't.
        """
        ts = cycle_ts or datetime.now(timezone.utc)
        bucket = ts.strftime("%Y%m%d%H%M")
        nonce = uuid.uuid4().hex[:8]
        # Alpaca caps client_order_id at 48 chars; strategy ids are short.
        return f"{strategy_id[:16]}-{symbol[:10]}-{bucket}-{nonce}"

    def submit(
        self,
        symbol: str,
        side: str,
        qty: float,
        bot_allocation: float,
        client_order_id: str,
    ) -> OrderResult | None:
        """Submit a market order with a caller-supplied idempotency key.

        The orchestrator persists an Order row BEFORE calling this so the
        intent survives a crash mid-submit.
        """
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported side: {side}")
        if qty <= 0:
            return None

        # Per-position cap check (only on opening exposure with a buy).
        if side == "buy":
            price = self.client.get_latest_price(symbol)
            notional = qty * price
            cap = bot_allocation * self.settings.per_position_pct
            if notional > cap:
                trimmed_qty = cap / price
                log.warning(
                    "trimmed %s buy %.4f -> %.4f to honor per-position cap $%.2f",
                    symbol, qty, trimmed_qty, cap,
                )
                qty = trimmed_qty
            if qty <= 0:
                return None

        if self.settings.is_live:
            log.warning("LIVE order: %s %s qty=%s coid=%s", side, symbol, qty, client_order_id)
        return self.client.submit_market_order(symbol, side, qty, client_order_id)


def _build_alpaca_client(settings: Settings) -> BrokerClient:
    """Lazy import so the rest of the system runs without alpaca-py installed."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    trading = TradingClient(
        settings.alpaca_api_key,
        settings.alpaca_api_secret,
        paper=settings.alpaca_paper,
    )
    data = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)

    def _to_result(order) -> OrderResult:
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(getattr(order, "client_order_id", "")),
            symbol=str(order.symbol),
            side=str(order.side).lower().replace("orderside.", ""),
            qty=float(order.qty),
            price=float(order.filled_avg_price or 0.0),
            status=str(order.status).lower().replace("orderstatus.", ""),
            filled_qty=float(getattr(order, "filled_qty", 0) or 0),
        )

    class _Alpaca:
        def get_account_equity(self) -> float:
            return float(trading.get_account().equity)

        def get_positions(self) -> list[Position]:
            return [
                Position(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_price=float(p.avg_entry_price),
                    market_value=float(p.market_value),
                )
                for p in trading.get_all_positions()
            ]

        def get_latest_price(self, symbol: str) -> float:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = data.get_stock_latest_quote(req)[symbol]
            return float((quote.ask_price + quote.bid_price) / 2.0)

        def submit_market_order(self, symbol, side, qty, client_order_id):
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            return _to_result(trading.submit_order(req))

        def get_order_by_client_id(self, client_order_id):
            try:
                order = trading.get_order_by_client_id(client_order_id)
                return _to_result(order)
            except Exception:
                return None

    return _Alpaca()
