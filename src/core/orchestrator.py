"""Run all enabled bots, reconcile target positions to actual orders, persist state."""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from src.config import Settings, get_settings
from src.core.broker import BrokerAdapter, Position
from src.core.store import EquitySnapshot, Signal as SignalRow, Trade, init_db, session_scope
from src.core.strategy import Strategy, StrategyContext, TargetPosition, utc_now

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRunResult:
    strategy_id: str
    submitted: int
    skipped: int


def load_enabled_bots(settings: Settings) -> list[Strategy]:
    """Instantiate enabled strategies. Imported lazily to avoid hard deps for unused bots."""
    from src.bots.momentum import MomentumStrategy
    from src.bots.mean_reversion import MeanReversionStrategy
    from src.bots.congress import CongressStrategy
    from src.bots.sentiment import SentimentStrategy

    registry: dict[str, type[Strategy]] = {
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "congress": CongressStrategy,
        "sentiment": SentimentStrategy,
    }
    bots: list[Strategy] = []
    for name in settings.enabled_bot_list:
        cls = registry.get(name)
        if cls is None:
            log.warning("unknown bot %r in ENABLED_BOTS — skipping", name)
            continue
        bots.append(cls())
    return bots


class Orchestrator:
    def __init__(self, broker: BrokerAdapter | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.broker = broker or BrokerAdapter(settings=self.settings)
        self.bots: list[Strategy] = []

    def setup(self) -> None:
        init_db()
        self.bots = load_enabled_bots(self.settings)
        for bot in self.bots:
            bot.on_start()
        log.info("orchestrator ready: %s", [b.id for b in self.bots])

    # --- core cycle -----------------------------------------------------
    def run_once(self) -> list[BotRunResult]:
        if not self.bots:
            self.setup()
        if self._global_drawdown_breached():
            log.error("global drawdown breached — halting all bots")
            return []
        results = []
        for bot in self.bots:
            results.append(self._run_bot(bot))
        return results

    def _run_bot(self, bot: Strategy) -> BotRunResult:
        bot_alloc = self.settings.per_bot_cap
        try:
            account_equity = self.broker.equity()
        except Exception as exc:  # pragma: no cover - network-dependent
            log.exception("could not fetch account equity: %s", exc)
            return BotRunResult(bot.id, 0, 0)

        all_positions = self.broker.positions()
        bot_positions = self._positions_for_bot(bot.id, all_positions)
        ctx = StrategyContext(
            now=utc_now(),
            cash=max(account_equity - sum(p.market_value for p in bot_positions.values()), 0.0),
            positions={s: p.qty for s, p in bot_positions.items()},
            bot_equity=bot_alloc,
        )

        try:
            targets = bot.target_positions(ctx)
        except Exception:
            log.exception("bot %s failed during target_positions", bot.id)
            return BotRunResult(bot.id, 0, 0)

        submitted = 0
        skipped = 0
        target_by_symbol = {t.symbol: t for t in targets}

        # Persist signals (even non-acted ones — useful for audit).
        with session_scope() as sess:
            for t in targets:
                sess.add(
                    SignalRow(
                        strategy_id=bot.id,
                        symbol=t.symbol,
                        direction="long" if t.weight > 0 else "short" if t.weight < 0 else "flat",
                        strength=abs(t.weight),
                        meta=t.meta,
                    )
                )

        # Reconcile: close anything in current positions but absent from target.
        for symbol, pos in bot_positions.items():
            if symbol not in target_by_symbol and pos.qty != 0:
                ok = self._submit_and_record(bot, symbol, "sell", abs(pos.qty), bot_alloc)
                submitted += int(ok)
                skipped += int(not ok)

        # Open or resize toward target weights.
        for t in targets:
            current_qty = bot_positions[t.symbol].qty if t.symbol in bot_positions else 0.0
            target_notional = t.weight * bot_alloc
            try:
                price = self.broker.price(t.symbol)
            except Exception:  # pragma: no cover - network-dependent
                log.exception("price fetch failed for %s", t.symbol)
                skipped += 1
                continue
            if price <= 0:
                skipped += 1
                continue
            target_qty = target_notional / price
            delta = target_qty - current_qty
            if abs(delta * price) < 1.0:  # ignore micro-rebalances
                continue
            side = "buy" if delta > 0 else "sell"
            ok = self._submit_and_record(bot, t.symbol, side, abs(delta), bot_alloc)
            submitted += int(ok)
            skipped += int(not ok)

        # Snapshot equity for this bot.
        with session_scope() as sess:
            position_value = sum(p.market_value for p in bot_positions.values())
            sess.add(
                EquitySnapshot(
                    strategy_id=bot.id,
                    cash=ctx.cash,
                    position_value=position_value,
                    total_equity=ctx.cash + position_value,
                )
            )

        return BotRunResult(bot.id, submitted, skipped)

    # --- helpers --------------------------------------------------------
    def _submit_and_record(
        self, bot: Strategy, symbol: str, side: str, qty: float, alloc: float
    ) -> bool:
        try:
            res = self.broker.submit(symbol, side, qty, alloc)
        except Exception:
            log.exception("order submit failed for %s %s %s", bot.id, side, symbol)
            return False
        if res is None:
            return False
        with session_scope() as sess:
            sess.add(
                Trade(
                    strategy_id=bot.id,
                    symbol=symbol,
                    side=side,
                    qty=res.qty,
                    price=res.price,
                    notional=res.qty * res.price,
                    order_id=res.order_id,
                    meta={},
                )
            )
        return True

    def _positions_for_bot(
        self, strategy_id: str, all_positions: dict[str, Position]
    ) -> dict[str, Position]:
        """Best-effort allocation of broker positions to a bot.

        Alpaca returns one position per symbol regardless of which bot opened it. Without
        a sub-account model, we attribute a position to the most recent bot that traded
        the symbol. This is a known limitation — documented for the user.
        """
        with session_scope() as sess:
            from sqlalchemy import select

            stmt = (
                select(Trade.symbol, Trade.strategy_id)
                .order_by(Trade.ts.desc())
            )
            seen: dict[str, str] = {}
            for sym, sid in sess.execute(stmt).all():
                seen.setdefault(sym, sid)
        return {s: p for s, p in all_positions.items() if seen.get(s) == strategy_id}

    def _global_drawdown_breached(self) -> bool:
        try:
            equity = self.broker.equity()
        except Exception:  # pragma: no cover - network-dependent
            return False
        start = self.settings.account_starting_equity
        if start <= 0:
            return False
        dd = (equity - start) / start
        return dd <= -self.settings.global_max_drawdown


def main() -> None:  # pragma: no cover - CLI entrypoint
    logging.basicConfig(level=get_settings().log_level)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run all bots once and exit.")
    args = parser.parse_args()

    orch = Orchestrator()
    orch.setup()
    if args.once:
        for r in orch.run_once():
            log.info("bot=%s submitted=%d skipped=%d", r.strategy_id, r.submitted, r.skipped)
        return

    # Long-running mode: APScheduler with each bot's own cron.
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone="UTC")
    for bot in orch.bots:
        trigger = CronTrigger(**bot.schedule)
        sched.add_job(lambda b=bot: orch._run_bot(b), trigger, id=bot.id, replace_existing=True)
        log.info("scheduled %s: %s", bot.id, bot.schedule)
    sched.start()


if __name__ == "__main__":  # pragma: no cover
    main()
