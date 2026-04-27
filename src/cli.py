"""Top-level CLI. Thin wrapper over the orchestrator and backtest runner."""
from __future__ import annotations

import logging

import click

from src.config import get_settings


@click.group()
def main() -> None:  # pragma: no cover
    """Multi-strategy trading bot."""
    logging.basicConfig(level=get_settings().log_level)


@main.command()
@click.option("--once", is_flag=True, help="Run all enabled bots once and exit.")
def run(once: bool) -> None:  # pragma: no cover
    """Run the orchestrator (paper by default)."""
    from src.core.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.setup()
    if once:
        for r in orch.run_once():
            click.echo(f"bot={r.strategy_id} submitted={r.submitted} skipped={r.skipped}")
        return
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone="UTC")
    for bot in orch.bots:
        sched.add_job(lambda b=bot: orch._run_bot(b), CronTrigger(**bot.schedule), id=bot.id)
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
def dashboard() -> None:  # pragma: no cover
    """Launch the Streamlit dashboard."""
    import subprocess
    import sys

    subprocess.run([sys.executable, "-m", "streamlit", "run", "dashboard/app.py"], check=False)


if __name__ == "__main__":  # pragma: no cover
    main()
