"""Top-level CLI."""
from __future__ import annotations

import logging

import click

from src.config import get_settings


@click.group()
def main() -> None:  # pragma: no cover
    from src.core.logging_setup import setup_logging

    setup_logging(get_settings().log_level)


@main.command()
@click.option("--once", is_flag=True, help="Run all enabled bots once and exit.")
def run(once: bool) -> None:  # pragma: no cover
    """Run the orchestrator (paper by default)."""
    from src.core.orchestrator import RECONCILE_INTERVAL_SEC, Orchestrator
    from src.core.reconciler import reconcile_open_orders

    orch = Orchestrator()
    orch.setup()
    if once:
        reconcile_open_orders(orch.broker)
        for r in orch.run_once():
            click.echo(f"bot={r.strategy_id} submitted={r.submitted} skipped={r.skipped}")
        reconcile_open_orders(orch.broker)
        return
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sched = BlockingScheduler(timezone="UTC")
    for bot in orch.bots:
        sched.add_job(lambda b=bot: orch._run_bot(b), CronTrigger(**bot.schedule), id=bot.id)
    sched.add_job(
        lambda: reconcile_open_orders(orch.broker),
        IntervalTrigger(seconds=RECONCILE_INTERVAL_SEC),
        id="reconciler",
    )
    sched.start()


@main.command()
@click.option("--strategy", required=True)
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--capital", default=25_000.0, type=float)
def backtest(strategy: str, start: str, end: str, capital: float) -> None:  # pragma: no cover
    """Backtest a strategy over a historical date range."""
    from src.backtest.runner import run as run_bt
    from src.core import metrics

    df = run_bt(strategy, start, end, capital)
    if df.empty:
        click.echo("no results")
        return
    rep = metrics.report(df["total_equity"])
    click.echo(f"sharpe={rep.sharpe:.2f} cagr={rep.cagr * 100:.2f}% max_dd={rep.max_drawdown * 100:.2f}%")


@main.command()
@click.option("--strategy", required=True, help="Bot id, e.g. momentum")
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--trials", default=30, type=int)
@click.option("--train-days", default=180, type=int)
@click.option("--test-days", default=30, type=int)
def optimize(strategy: str, start: str, end: str, trials: int, train_days: int, test_days: int) -> None:  # pragma: no cover
    """Walk-forward optimize a strategy's params over a date range.

    Reports median OOS Sharpe + overfit gap. Refuse to deploy params if
    overfit_gap > 1.0 (in-sample beat OOS by >1.0 Sharpe).
    """
    import json
    from datetime import datetime as dt

    from src.backtest.optimize import walk_forward
    from src.bots.momentum import MomentumStrategy
    from src.bots.mean_reversion import MeanReversionStrategy

    if strategy == "momentum":
        def factory(p):
            return MomentumStrategy({
                "fast": int(p.get("fast", 20)),
                "slow": int(p.get("slow", 50)),
                "adx_threshold": float(p.get("adx_threshold", 25.0)),
            })

        def space(trial):
            return {
                "fast": trial.suggest_int("fast", 5, 30),
                "slow": trial.suggest_int("slow", 35, 120),
                "adx_threshold": trial.suggest_float("adx_threshold", 15.0, 35.0),
            }
        universe = factory({}).universe()
    elif strategy == "mean_reversion":
        def factory(p):
            return MeanReversionStrategy({
                "rsi_buy": float(p.get("rsi_buy", 10.0)),
                "rsi_exit": float(p.get("rsi_exit", 60.0)),
            })

        def space(trial):
            return {
                "rsi_buy": trial.suggest_float("rsi_buy", 5.0, 25.0),
                "rsi_exit": trial.suggest_float("rsi_exit", 50.0, 80.0),
            }
        universe = factory({}).universe()
    else:
        raise SystemExit(f"unknown strategy: {strategy}")

    res = walk_forward(
        factory,
        universe=universe,
        start=dt.fromisoformat(start),
        end=dt.fromisoformat(end),
        param_space=space,
        n_trials=trials,
        train_days=train_days,
        test_days=test_days,
    )
    click.echo(json.dumps(
        {
            "strategy": res.strategy_id,
            "best_params": res.best_params,
            "median_oos_sharpe": round(res.median_oos_sharpe, 3),
            "best_in_sample_sharpe": round(res.best_in_sample_sharpe, 3),
            "overfit_gap": round(res.overfit_gap, 3),
            "robust": res.robust,
            "windows": len(res.per_window),
        },
        indent=2,
    ))
    if not res.robust:
        click.echo("\nWARNING: not robust — refuse to deploy these params.", err=True)


@main.command()
def dashboard() -> None:  # pragma: no cover
    """Launch the Streamlit dashboard locally."""
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "dashboard/app.py"],
        check=False,
    )


@main.command()
@click.option("--strategy", required=True, help="Bot id, e.g. momentum")
def graduate(strategy: str) -> None:  # pragma: no cover
    """Mark a bot as paper-validated. Refuses if metrics don't meet the gate."""
    from src.core.risk import graduate as do_graduate

    try:
        check = do_graduate(strategy)
        click.echo(
            f"OK — {strategy} graduated. days={check.days_observed} "
            f"sharpe={check.sharpe:.2f} max_dd={check.max_drawdown * 100:.1f}%"
        )
    except RuntimeError as exc:
        click.echo(f"DENIED — {exc}", err=True)
        raise SystemExit(1)


@main.command()
@click.option("--strategy", required=True)
@click.option("--reason", default="manual pause")
def pause(strategy: str, reason: str) -> None:  # pragma: no cover
    """Pause a bot manually."""
    from src.core.risk import pause_bot

    pause_bot(strategy, reason=reason)
    click.echo(f"paused {strategy}: {reason}")


@main.command()
@click.option("--strategy", required=True)
def enable(strategy: str) -> None:  # pragma: no cover
    """Re-enable a paused bot."""
    from src.core.risk import enable_bot

    enable_bot(strategy)
    click.echo(f"enabled {strategy}")


@main.command()
def status() -> None:  # pragma: no cover
    """Show the operational state of every known bot."""
    from sqlalchemy import select

    from src.core.risk import evaluate_graduation
    from src.core.store import BotStatus, init_db, session_scope

    init_db()
    with session_scope() as sess:
        rows = sess.execute(select(BotStatus)).scalars().all()
    if not rows:
        click.echo("(no bot status rows yet — run the orchestrator at least once)")
        return
    for r in rows:
        check = evaluate_graduation(r.strategy_id)
        validated = (
            r.paper_validated_at.isoformat() if r.paper_validated_at else "no"
        )
        click.echo(
            f"{r.strategy_id:16s} state={r.state:8s} validated={validated} "
            f"days={check.days_observed:3d} sharpe={check.sharpe:5.2f}  {r.reason}"
        )


if __name__ == "__main__":  # pragma: no cover
    main()
