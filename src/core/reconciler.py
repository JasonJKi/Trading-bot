"""Order reconciliation.

For every Order row that isn't in a terminal state, fetch its current state from
the broker, update fill info, and apply the fill to the per-bot position ledger.

Run periodically (every ~30s) by the orchestrator.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.core.broker import BrokerAdapter
from src.core.store import (
    AuditEvent,
    BotPosition,
    Order,
    Trade,
    record_audit,
    session_scope,
)

log = logging.getLogger(__name__)

TERMINAL = {"filled", "canceled", "rejected", "expired"}


def _apply_fill_to_ledger(
    sess, strategy_id: str, symbol: str, side: str, qty_delta: float, price: float
) -> None:
    """Update BotPosition for a fill of `qty_delta` shares (always positive) at `price`.

    Sign convention: buy adds qty, sell subtracts. Average price is recomputed on
    increases; cost basis is preserved on decreases until the position closes.
    """
    if qty_delta <= 0:
        return
    pos = sess.execute(
        select(BotPosition).where(
            BotPosition.strategy_id == strategy_id, BotPosition.symbol == symbol
        )
    ).scalar_one_or_none()
    if pos is None:
        if side == "sell":
            # Selling something the ledger doesn't know about — short.
            sess.add(
                BotPosition(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    qty=-qty_delta,
                    avg_price=price,
                    cost_basis=-qty_delta * price,
                )
            )
        else:
            sess.add(
                BotPosition(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    qty=qty_delta,
                    avg_price=price,
                    cost_basis=qty_delta * price,
                )
            )
        return

    if side == "buy":
        new_qty = pos.qty + qty_delta
        if pos.qty < 0:
            # Covering a short.
            covered = min(qty_delta, -pos.qty)
            pos.qty += covered
            remaining = qty_delta - covered
            if remaining > 0:
                # Flipped to long.
                pos.qty = remaining
                pos.avg_price = price
                pos.cost_basis = remaining * price
        else:
            pos.cost_basis += qty_delta * price
            pos.avg_price = pos.cost_basis / new_qty if new_qty else 0.0
            pos.qty = new_qty
    else:  # sell
        new_qty = pos.qty - qty_delta
        if pos.qty > 0:
            sold = min(qty_delta, pos.qty)
            pos.cost_basis -= sold * pos.avg_price
            pos.qty -= sold
            remaining = qty_delta - sold
            if remaining > 0:
                # Flipped to short.
                pos.qty = -remaining
                pos.avg_price = price
                pos.cost_basis = -remaining * price
        else:
            pos.qty = new_qty
            pos.avg_price = price

    pos.updated_at = datetime.now(timezone.utc)
    if pos.qty == 0:
        sess.delete(pos)


def reconcile_open_orders(broker: BrokerAdapter | None = None) -> int:
    """Update non-terminal Order rows. Returns the number of orders touched."""
    broker = broker or BrokerAdapter()
    touched = 0
    with session_scope() as sess:
        open_orders = sess.execute(
            select(Order).where(Order.status.notin_(TERMINAL))
        ).scalars().all()
        for o in open_orders:
            try:
                latest = broker.order_by_client_id(o.client_order_id)
            except Exception:
                log.exception("broker lookup failed for %s", o.client_order_id)
                continue
            if latest is None:
                continue

            prev_filled = o.filled_qty
            o.broker_order_id = latest.order_id or o.broker_order_id
            o.status = latest.status
            o.last_reconciled_at = datetime.now(timezone.utc)
            o.filled_qty = max(o.filled_qty, latest.filled_qty)
            if latest.price > 0:
                o.filled_avg_price = latest.price

            new_filled = o.filled_qty - prev_filled
            if new_filled > 0 and o.filled_avg_price > 0:
                _apply_fill_to_ledger(
                    sess, o.strategy_id, o.symbol, o.side, new_filled, o.filled_avg_price
                )
                sess.add(
                    Trade(
                        strategy_id=o.strategy_id,
                        strategy_version=o.strategy_version,
                        symbol=o.symbol,
                        side=o.side,
                        qty=new_filled,
                        price=o.filled_avg_price,
                        notional=new_filled * o.filled_avg_price,
                        order_id=o.broker_order_id,
                        meta={"client_order_id": o.client_order_id},
                    )
                )
                sess.add(
                    AuditEvent(
                        kind="fill",
                        strategy_id=o.strategy_id,
                        severity="info",
                        message=f"{o.side} {new_filled} {o.symbol} @ {o.filled_avg_price:.2f}",
                        meta={"order_id": o.broker_order_id, "client_order_id": o.client_order_id},
                    )
                )
            elif o.status in {"canceled", "rejected", "expired"}:
                record_audit(
                    "order_terminal",
                    f"order {o.client_order_id} ended in status={o.status}",
                    strategy_id=o.strategy_id,
                    severity="warning" if o.status == "rejected" else "info",
                    order_id=o.broker_order_id,
                )
            touched += 1
    return touched


def open_orders_for(strategy_id: str, symbol: str) -> list[Order]:
    """Return non-terminal orders for a (strategy, symbol). Use as an in-flight guard."""
    with session_scope() as sess:
        return list(
            sess.execute(
                select(Order).where(
                    Order.strategy_id == strategy_id,
                    Order.symbol == symbol,
                    Order.status.notin_(TERMINAL),
                )
            ).scalars()
        )
