"""Run all enabled bots, reconcile target positions to actual orders, persist state.

Order pipeline (v2):
  1. Strategy emits target_positions(ctx).
  2. We compute deltas vs the per-bot ledger (BotPosition).
  3. For each delta we (a) skip if an in-flight Order already covers it,
     (b) generate a deterministic client_order_id,
     (c) write an Order row in `new` state,
     (d) submit to the broker and stash the broker_order_id.
  4. Reconciler runs every ~30s, polls open orders, applies fills to the
     ledger, and writes Trade + AuditEvent rows.

This separates "intent" (Order) from "fill" (Trade) so partial fills, rejects,
and crashes mid-submit are all observable.
"""
# Populate os.environ from .env BEFORE any module reads it. pydantic-settings
# loads .env into Settings on its own, but a few modules (auth.py, alerts)
# read os.environ directly — under launchd the plist only injects PATH +
# PYTHONUNBUFFERED, so those reads otherwise come back empty.
# ruff: noqa: E402
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from src.config import Settings, get_settings
from src.core.alerter import alert
from src.core.allocator import allocate as compute_allocations
from src.core.broker import BrokerAdapter
from src.core.reconciler import open_orders_for, reconcile_open_orders
from src.core.risk import assert_all_paper_validated, trip_circuit_breaker_if_needed
from src.core.store import (
    BotPosition,
    EquitySnapshot,
    Order,
    Signal as SignalRow,
    init_db,
    record_audit,
    session_scope,
)
from src.core.strategy import Strategy, StrategyContext, TargetPosition, utc_now

log = logging.getLogger(__name__)

RECONCILE_INTERVAL_SEC = 30


@dataclass(slots=True)
class BotRunResult:
    strategy_id: str
    submitted: int
    skipped: int


def load_enabled_bots(settings: Settings) -> list[Strategy]:
    from src.bots.momentum import MomentumStrategy
    from src.bots.mean_reversion import MeanReversionStrategy
    from src.bots.congress import CongressStrategy
    from src.bots.sentiment import SentimentStrategy
    from src.bots.cross_momentum import CrossSectionalMomentum

    registry: dict[str, type[Strategy]] = {
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "congress": CongressStrategy,
        "sentiment": SentimentStrategy,
        "xs_momentum": CrossSectionalMomentum,
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
        # Fail loud on misconfiguration before we touch the broker or DB.
        self.settings.validate_for_runtime()
        init_db()
        self.bots = load_enabled_bots(self.settings)
        # In live mode, refuse to start unless every bot has been paper-validated.
        if self.settings.is_live:
            assert_all_paper_validated([b.id for b in self.bots])
        for bot in self.bots:
            bot.on_start()
        self._log_startup_banner()
        alert(
            "info",
            "trading-bot started",
            f"mode={'PAPER' if self.settings.alpaca_paper else 'LIVE'} "
            f"bots={[b.id for b in self.bots]}",
        )

    def _log_startup_banner(self) -> None:
        mode = "PAPER" if self.settings.alpaca_paper else "LIVE"
        log.info("=" * 60)
        log.info("trading-bot starting — mode=%s", mode)
        log.info("db=%s", self.settings.database_url)
        log.info(
            "caps: per_bot=$%s per_position=%.1f%% global_dd=%.1f%% per_bot_dd=%.1f%%",
            f"{self.settings.per_bot_cap:,.0f}",
            self.settings.per_position_pct * 100,
            self.settings.global_max_drawdown * 100,
            self.settings.per_bot_max_drawdown * 100,
        )
        if not self.bots:
            log.warning("no bots enabled — set ENABLED_BOTS in env")
        for bot in self.bots:
            log.info("  bot=%-16s v=%-5s schedule=%s", bot.id, bot.version, bot.schedule)
        if not (self.settings.alpaca_api_key and self.settings.alpaca_api_secret):
            log.warning("ALPACA_API_KEY / ALPACA_API_SECRET not set — bot will fail on first tick")
        log.info("=" * 60)

    # --- core cycle -----------------------------------------------------
    def run_once(self) -> list[BotRunResult]:
        if not self.bots:
            self.setup()
        if self._global_drawdown_breached():
            alert(
                "critical",
                "Global drawdown breached",
                f"Account drawdown exceeded {self.settings.global_max_drawdown * 100:.0f}% — "
                f"halting all bots.",
            )
            log.error("global drawdown breached — halting all bots")
            return []
        # Dynamic per-bot allocation: softmax over rolling Sharpe.
        active = [b for b in self.bots if self._bot_is_enabled(b.id)]
        allocations = {
            a.strategy_id: a
            for a in compute_allocations(
                [b.id for b in active],
                total_capital=self.settings.per_bot_cap * len(active),
            )
        }
        for sid, alloc in allocations.items():
            log.info(
                "alloc %s = $%.0f (w=%.2f, sharpe30d=%.2f, %s)",
                sid, alloc.capital, alloc.weight, alloc.sharpe_30d, alloc.rationale,
            )

        results = []
        for bot in self.bots:
            if not self._bot_is_enabled(bot.id):
                log.info("bot %s is paused — skipping", bot.id)
                continue
            if trip_circuit_breaker_if_needed(bot.id):
                continue
            cap = allocations[bot.id].capital if bot.id in allocations else self.settings.per_bot_cap
            results.append(self._run_bot(bot, cap))
        return results

    def _run_bot(self, bot: Strategy, bot_alloc: float | None = None) -> BotRunResult:
        if bot_alloc is None:
            bot_alloc = self.settings.per_bot_cap
        try:
            self.broker.equity()  # connectivity probe
        except Exception as exc:  # pragma: no cover - network-dependent
            log.exception("could not fetch account equity: %s", exc)
            record_audit(
                "broker_error", str(exc), strategy_id=bot.id, severity="error"
            )
            return BotRunResult(bot.id, 0, 0)

        bot_positions = self._ledger_positions(bot.id)
        try:
            from src.core.regime import detect as detect_regime
            regime_label = detect_regime().label
        except Exception:
            regime_label = "chop"
        ctx = StrategyContext(
            now=utc_now(),
            cash=bot_alloc - sum(p.qty * p.avg_price for p in bot_positions.values()),
            positions={s: p.qty for s, p in bot_positions.items()},
            bot_equity=bot_alloc,
            regime=regime_label,
        )

        try:
            targets = bot.target_positions(ctx)
        except Exception as exc:
            log.exception("bot %s failed during target_positions", bot.id)
            record_audit(
                "bot_error", f"target_positions raised: {exc}",
                strategy_id=bot.id, severity="error",
            )
            return BotRunResult(bot.id, 0, 0)

        submitted = 0
        skipped = 0
        target_by_symbol = {t.symbol: t for t in targets}

        with session_scope() as sess:
            for t in targets:
                sess.add(
                    SignalRow(
                        strategy_id=bot.id,
                        strategy_version=bot.version,
                        symbol=t.symbol,
                        direction="long" if t.weight > 0 else "short" if t.weight < 0 else "flat",
                        strength=abs(t.weight),
                        meta=t.meta,
                    )
                )

        # Close anything held by this bot but absent from current targets.
        for symbol, pos in bot_positions.items():
            if symbol not in target_by_symbol and pos.qty != 0:
                ok = self._submit_intent(bot, symbol, "sell" if pos.qty > 0 else "buy", abs(pos.qty), bot_alloc)
                submitted += int(ok)
                skipped += int(not ok)

        # Open or resize toward target weights.
        for t in targets:
            current_qty = bot_positions[t.symbol].qty if t.symbol in bot_positions else 0.0
            try:
                price = self.broker.price(t.symbol)
            except Exception:  # pragma: no cover - network-dependent
                log.exception("price fetch failed for %s", t.symbol)
                skipped += 1
                continue
            if price <= 0:
                skipped += 1
                continue
            target_qty = (t.weight * bot_alloc) / price
            delta = target_qty - current_qty
            if abs(delta * price) < 1.0:
                continue
            side = "buy" if delta > 0 else "sell"
            ok = self._submit_intent(bot, t.symbol, side, abs(delta), bot_alloc)
            submitted += int(ok)
            skipped += int(not ok)

        # Snapshot bot equity (mark-to-market against the ledger).
        position_value = 0.0
        for sym, pos in bot_positions.items():
            try:
                position_value += pos.qty * self.broker.price(sym)
            except Exception:
                position_value += pos.qty * pos.avg_price
        with session_scope() as sess:
            cash = bot_alloc - sum(p.qty * p.avg_price for p in bot_positions.values())
            sess.add(
                EquitySnapshot(
                    strategy_id=bot.id,
                    cash=cash,
                    position_value=position_value,
                    total_equity=cash + position_value,
                )
            )

        return BotRunResult(bot.id, submitted, skipped)

    # --- order submission with idempotency + in-flight guard ------------
    def _submit_intent(
        self, bot: Strategy, symbol: str, side: str, qty: float, alloc: float
    ) -> bool:
        in_flight = open_orders_for(bot.id, symbol)
        if in_flight:
            log.info(
                "skipping %s %s %s — in-flight order %s status=%s",
                bot.id, side, symbol, in_flight[0].client_order_id, in_flight[0].status,
            )
            return False

        client_order_id = BrokerAdapter.make_client_order_id(bot.id, symbol)
        # Persist intent BEFORE talking to the broker.
        with session_scope() as sess:
            sess.add(
                Order(
                    strategy_id=bot.id,
                    strategy_version=bot.version,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    client_order_id=client_order_id,
                    status="new",
                    submitted_at=datetime.now(timezone.utc),
                    meta={"alloc": alloc},
                )
            )

        try:
            res = self.broker.submit(symbol, side, qty, alloc, client_order_id)
        except Exception as exc:
            log.exception("broker submit failed for %s %s %s", bot.id, side, symbol)
            with session_scope() as sess:
                row = sess.execute(
                    select(Order).where(Order.client_order_id == client_order_id)
                ).scalar_one()
                row.status = "rejected"
                row.error = str(exc)[:500]
            record_audit(
                "order_submit_failed", str(exc),
                strategy_id=bot.id, severity="error",
                client_order_id=client_order_id,
            )
            return False

        if res is None:
            with session_scope() as sess:
                row = sess.execute(
                    select(Order).where(Order.client_order_id == client_order_id)
                ).scalar_one()
                row.status = "canceled"
                row.error = "trimmed to zero by risk caps"
            return False

        with session_scope() as sess:
            row = sess.execute(
                select(Order).where(Order.client_order_id == client_order_id)
            ).scalar_one()
            row.broker_order_id = res.order_id
            # The reconciler is the single source of truth for fills. We hold our
            # local status at 'accepted' until reconcile picks up the broker's
            # filled/canceled/etc state and applies the fill to the ledger.
            row.status = "accepted"
        record_audit(
            "order_submitted",
            f"{side} {qty} {symbol} @ market",
            strategy_id=bot.id,
            client_order_id=client_order_id,
            broker_order_id=res.order_id,
        )
        return True

    # --- helpers --------------------------------------------------------
    def _ledger_positions(self, strategy_id: str) -> dict[str, BotPosition]:
        with session_scope() as sess:
            rows = sess.execute(
                select(BotPosition).where(BotPosition.strategy_id == strategy_id)
            ).scalars().all()
            sess.expunge_all()
        return {r.symbol: r for r in rows}

    def _bot_is_enabled(self, strategy_id: str) -> bool:
        from src.core.store import BotStatus
        with session_scope() as sess:
            row = sess.execute(
                select(BotStatus).where(BotStatus.strategy_id == strategy_id)
            ).scalar_one_or_none()
        return row is None or row.state == "enabled"

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
    from src.core.logging_setup import setup_logging

    setup_logging(get_settings().log_level)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run all bots once and exit.")
    args = parser.parse_args()

    orch = Orchestrator()
    orch.setup()

    from src.core.healthz import start_in_background as start_healthz
    start_healthz()

    if args.once:
        reconcile_open_orders(orch.broker)
        for r in orch.run_once():
            log.info("bot=%s submitted=%d skipped=%d", r.strategy_id, r.submitted, r.skipped)
        reconcile_open_orders(orch.broker)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sched = BlockingScheduler(timezone="UTC")
    for bot in orch.bots:
        trigger = CronTrigger(**bot.schedule)
        sched.add_job(lambda b=bot: orch._run_bot(b), trigger, id=bot.id, replace_existing=True)
        log.info("scheduled %s: %s", bot.id, bot.schedule)
    sched.add_job(
        lambda: reconcile_open_orders(orch.broker),
        IntervalTrigger(seconds=RECONCILE_INTERVAL_SEC),
        id="reconciler",
        replace_existing=True,
    )
    log.info("scheduled reconciler: every %ss", RECONCILE_INTERVAL_SEC)

    # Nightly DB backup at 04:00 UTC (after US close, before Asia open).
    from src.core.backup import backup_database

    sched.add_job(
        backup_database,
        CronTrigger(hour=4, minute=0),
        id="db_backup",
        replace_existing=True,
    )
    log.info("scheduled db_backup: 04:00 UTC daily")

    # Refresh data-source caches independently of the bots that consume them.
    bot_ids = [b.id for b in orch.bots]

    if "congress" in bot_ids:
        from src.data.congress import refresh_cache as refresh_congress

        sched.add_job(
            refresh_congress,
            IntervalTrigger(hours=1),
            id="refresh_congress",
            replace_existing=True,
        )
        log.info("scheduled refresh_congress: every 1h")

    if "sentiment" in bot_ids:
        # Pull news every 5 min; score every 5 min (offset to avoid lock contention).
        from src.bots.sentiment import _universe as sentiment_universe
        from src.data.news import refresh_cache as refresh_news

        sym_list = sentiment_universe()
        sched.add_job(
            lambda: refresh_news(sym_list, hours=4),
            IntervalTrigger(minutes=5),
            id="refresh_news",
            replace_existing=True,
        )
        log.info("scheduled refresh_news: every 5m for %d symbols", len(sym_list))

        try:
            from src.data.sentiment import score_unscored
            sched.add_job(
                score_unscored,
                IntervalTrigger(minutes=5, jitter=30),
                id="score_sentiment",
                replace_existing=True,
            )
            log.info("scheduled score_sentiment: every 5m (FinBERT)")
        except ImportError:
            log.warning(
                "sentiment scoring unavailable: install '.[sentiment]' for FinBERT"
            )
    sched.start()


if __name__ == "__main__":  # pragma: no cover
    main()
