"""Broker adapter — single point that talks to Alpaca, with risk caps enforced before any order."""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float


class BrokerClient(Protocol):  # pragma: no cover - interface only
    def get_account_equity(self) -> float: ...
    def get_positions(self) -> list[Position]: ...
    def get_latest_price(self, symbol: str) -> float: ...
    def submit_market_order(self, symbol: str, side: str, qty: float) -> OrderResult: ...


class BrokerAdapter:
    """Wraps a BrokerClient and enforces risk limits.

    The actual Alpaca client is constructed lazily so unit tests can pass a fake.
    """

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

    # Order placement ------------------------------------------------------
    def submit(
        self,
        symbol: str,
        side: str,
        qty: float,
        bot_allocation: float,
    ) -> OrderResult | None:
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported side: {side}")
        if qty <= 0:
            return None

        # Per-position cap check (only applies to opening exposure on a buy).
        if side == "buy":
            price = self.client.get_latest_price(symbol)
            notional = qty * price
            cap = bot_allocation * self.settings.per_position_pct
            if notional > cap:
                qty = cap / price
                log.warning(
                    "trimmed %s buy %.2f -> %.2f to honor per-position cap %.2f",
                    symbol, qty, cap / price, cap,
                )
            if qty <= 0:
                return None

        if self.settings.is_live:
            log.warning("LIVE order: %s %s qty=%s", side, symbol, qty)
        return self.client.submit_market_order(symbol, side, qty)


def _build_alpaca_client(settings: Settings) -> BrokerClient:
    """Lazy import so the rest of the system runs without alpaca-py installed."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    trading = TradingClient(
        settings.alpaca_api_key,
        settings.alpaca_api_secret,
        paper=settings.alpaca_paper,
    )
    data = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)

    class _Alpaca:
        def get_account_equity(self) -> float:
            return float(trading.get_account().equity)

        def get_positions(self) -> list[Position]:
            out = []
            for p in trading.get_all_positions():
                out.append(
                    Position(
                        symbol=p.symbol,
                        qty=float(p.qty),
                        avg_price=float(p.avg_entry_price),
                        market_value=float(p.market_value),
                    )
                )
            return out

        def get_latest_price(self, symbol: str) -> float:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = data.get_stock_latest_quote(req)[symbol]
            # Use ask for buys, bid for sells — but adapter doesn't know side here, so midpoint.
            return float((quote.ask_price + quote.bid_price) / 2.0)

        def submit_market_order(self, symbol: str, side: str, qty: float) -> OrderResult:
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order = trading.submit_order(req)
            filled_price = float(order.filled_avg_price or 0.0)
            return OrderResult(
                order_id=str(order.id),
                symbol=symbol,
                side=side,
                qty=qty,
                price=filled_price,
            )

    return _Alpaca()
