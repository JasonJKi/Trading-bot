"""SQLAlchemy models + session factory. Single source of truth for all persisted state.

Models:
  Trade            — every executed trade (filled or partially filled).
  EquitySnapshot   — per-bot equity at the end of a cycle.
  Signal           — every signal emitted (acted or not).
  Order            — every order we *submitted*, with reconciled fills.
  BotPosition      — per-bot sub-ledger of held positions (so two bots
                     trading the same symbol don't fight over attribution).
  BotStatus        — enabled / paused / disabled + reason.
  AuditEvent       — append-only log of "what happened and why" for replay
                     and post-mortem. Never overwritten.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from src.config import get_settings


class Base(DeclarativeBase):
    pass


# Default tenant id stamped on every row that doesn't specify one. Today the
# system is single-tenant, but every tenant-scoped table carries this column
# so that adding real auth + multi-tenancy later is a query-filter change,
# not a schema migration. Shared/cache tables (CongressDisclosure, NewsItem,
# ResearchDocument) intentionally do NOT carry user_id — they're global.
DEFAULT_USER_ID = "demo"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version: Mapped[str] = mapped_column(String(32), default="1")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    cash: Mapped[float] = mapped_column(Float)
    position_value: Mapped[float] = mapped_column(Float)
    total_equity: Mapped[float] = mapped_column(Float)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version: Mapped[str] = mapped_column(String(32), default="1")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    acted: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Order(Base):
    """Submitted order with fill reconciliation.

    Status values track Alpaca's vocabulary: new / accepted / partially_filled /
    filled / canceled / rejected / expired. We periodically refresh `status`,
    `filled_qty`, and `filled_avg_price` until the order is terminal.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_version: Mapped[str] = mapped_column(String(32), default="1")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float] = mapped_column(Float, default=0.0)

    client_order_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    broker_order_id: Mapped[str] = mapped_column(String(96), default="", index=True)
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(String(512), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class BotPosition(Base):
    """Per-bot position ledger.

    The broker reports ONE position per symbol regardless of which bot owns it,
    so we maintain our own attribution. Updated on every fill: positive qty for
    long, negative for short, zero means flat (we delete the row).
    """

    __tablename__ = "bot_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("strategy_id", "symbol", name="uq_botposition_strategy_symbol"),
    )


class BotStatus(Base):
    """Operational state of each bot.

    state: enabled | paused | disabled
    reason: human-readable why; e.g. "drawdown 18% > cap 15%"
    Updated by the circuit breaker and the graduation gate.
    """

    __tablename__ = "bot_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="enabled")
    reason: Mapped[str] = mapped_column(String(256), default="")
    paper_validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditEvent(Base):
    """Append-only log of decisions, alerts, and operational events.

    NEVER updated, NEVER deleted. Use this when you ask "what was the bot
    thinking on Tuesday at 3 PM and why did it skip that signal?"
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(String(512))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class CongressDisclosure(Base):
    """Cache of congressional trade disclosures pulled from external APIs.

    `external_id` is a stable id the upstream provider gives us — we treat
    inserts as upsert-by-external-id so re-fetches are idempotent.
    """

    __tablename__ = "congress_disclosures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    external_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    politician: Mapped[str] = mapped_column(String(96), index=True)
    chamber: Mapped[str] = mapped_column(String(16), default="")  # House / Senate
    party: Mapped[str] = mapped_column(String(16), default="")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(16))  # buy / sell / exchange
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    transaction_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    disclosure_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(24), default="quiver")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class NewsItem(Base):
    """Cache of news headlines from a broker / vendor news feed."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    headline: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(String(2048), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)  # -1..+1
    sentiment_label: Mapped[str] = mapped_column(String(16), default="")  # neutral/positive/negative
    sentiment_model: Mapped[str] = mapped_column(String(48), default="")  # e.g. ProsusAI/finbert
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ResearchQuery(Base):
    """One run of the research agent — the user-facing topic + lifecycle metadata.

    The agent decomposes `topic` into sub-queries, fans them out to source adapters,
    collects ResearchDocument rows, and writes ResearchFinding rows that summarize
    distinct strategies/ideas/techniques.
    """

    __tablename__ = "research_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    topic: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)  # pending|running|done|failed
    error: Mapped[str] = mapped_column(String(1024), default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)         # planner output
    stats: Mapped[dict] = mapped_column(JSON, default=dict)        # docs_fetched, tokens, cost, etc.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ResearchDocument(Base):
    """A single piece of source content fetched during research.

    `source` ∈ {reddit, youtube, hackernews, arxiv, github, web, x, tiktok, ...}.
    `external_id` makes re-fetches idempotent across runs (so popular content is shared).
    `content` is the cleaned/extracted text; raw payloads live in `meta`.
    """

    __tablename__ = "research_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    query_id: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(256), index=True)
    url: Mapped[str] = mapped_column(String(1024), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    author: Mapped[str] = mapped_column(String(128), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content: Mapped[str] = mapped_column(String, default="")  # SQLite TEXT — unbounded
    score: Mapped[float] = mapped_column(Float, default=0.0)  # popularity / relevance / upvotes
    quality: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 — synthesizer's quality estimate
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_research_doc_source_extid"),
    )


class ResearchFinding(Base):
    """A structured insight extracted by the synthesizer — one strategy/idea/technique.

    The synthesizer reads N ResearchDocuments and emits M ResearchFindings, each
    citing the documents it derived from (`citations` is list[ResearchDocument.id]).

    `category` ∈ {strategy, indicator, framework, risk, data_source, infra, anti_pattern, other}
    """

    __tablename__ = "research_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    query_id: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(String, default="")     # 1-paragraph
    detail: Mapped[str] = mapped_column(String, default="")      # full markdown
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    novelty: Mapped[float] = mapped_column(Float, default=0.0)     # 0..1 (vs already-seen)
    actionable: Mapped[int] = mapped_column(Integer, default=0)    # 0/1: implementable in this repo
    citations: Mapped[list] = mapped_column(JSON, default=list)    # [doc_id, ...]
    tags: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategySpec(Base):
    """A filled-in StrategyTemplate — the bridge between a research finding and a
    deployed Strategy.

    Lifecycle:  pending → backtested → approved → deployed → retired
    (status field; transitions are append-only via AuditEvent).

    `template_id` references the runtime template registry (templates live in
    code, not DB — see src/templates/). `params`, `universe`, and `schedule`
    must validate against the template's ParamSpec; the synthesis agent and
    deterministic Python both check this.

    `finding_id` is optional (manual specs are valid). `bot_id` is the
    `Strategy.id` once promoted — null until then.
    """

    __tablename__ = "strategy_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    template_id: Mapped[str] = mapped_column(String(64), index=True)
    template_version: Mapped[str] = mapped_column(String(32), default="1")

    name: Mapped[str] = mapped_column(String(128))                  # human-friendly label
    params: Mapped[dict] = mapped_column(JSON, default=dict)        # validated against ParamSpec
    universe: Mapped[list] = mapped_column(JSON, default=list)
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)      # APScheduler cron fields

    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    bot_id: Mapped[str] = mapped_column(String(64), default="", index=True)  # set on deploy

    finding_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="manual")  # synthesis_agent | manual

    error: Mapped[str] = mapped_column(String(1024), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class BacktestReport(Base):
    """Result of evaluating a StrategySpec on historical data.

    A spec can have multiple reports across history (e.g., when re-run with new
    data, walk-forward variants, or after the spec's params change). The most
    recent report by `created_at` is the canonical one for gating decisions.
    """

    __tablename__ = "backtest_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_USER_ID, server_default=DEFAULT_USER_ID, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    spec_id: Mapped[int] = mapped_column(Integer, index=True)

    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)
    capital: Mapped[float] = mapped_column(Float, default=0.0)

    total_return: Mapped[float] = mapped_column(Float, default=0.0)
    cagr: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    sortino: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Walk-forward fields (null if a single-period backtest)
    median_oos_sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    overfit_gap: Mapped[float] = mapped_column(Float, default=0.0)

    n_trades: Mapped[int] = mapped_column(Integer, default=0)
    passed_gate: Mapped[int] = mapped_column(Integer, default=0)  # 0/1
    gate_reason: Mapped[str] = mapped_column(String(256), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


Index("ix_trades_strategy_ts", Trade.strategy_id, Trade.ts)
Index("ix_equity_strategy_ts", EquitySnapshot.strategy_id, EquitySnapshot.ts)
Index("ix_orders_strategy_ts", Order.strategy_id, Order.ts)
Index("ix_research_doc_query", ResearchDocument.query_id, ResearchDocument.fetched_at)
Index("ix_research_finding_query", ResearchFinding.query_id, ResearchFinding.category)
Index("ix_backtest_spec", BacktestReport.spec_id, BacktestReport.created_at)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = Path(url.replace("sqlite:///", "", 1))
        path.parent.mkdir(parents=True, exist_ok=True)


_current_db_url: str | None = None
_migrations_bootstrapped = False


def init_db() -> None:
    """Idempotent. Routes call this on every hit; we cache the engine + skip
    repeat migrations so two concurrent requests don't race alembic's global
    EnvironmentContext (which raised KeyError: 'config' in production).

    Recreates the engine + reruns migrations when DATABASE_URL changes,
    which happens in tests that monkeypatch the env between cases.
    """
    global _engine, _SessionLocal, _current_db_url, _migrations_bootstrapped
    settings = get_settings()
    _ensure_sqlite_dir(settings.database_url)
    if _engine is None or _current_db_url != settings.database_url:
        _engine = create_engine(settings.database_url, future=True, json_serializer=json.dumps)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
        _current_db_url = settings.database_url
        _migrations_bootstrapped = False  # fresh DB → re-bootstrap
    if _migrations_bootstrapped:
        return
    _bootstrap_migrations(settings.database_url)
    _migrations_bootstrapped = True


def _bootstrap_migrations(database_url: str) -> None:
    """Bring the schema up to head. Handles three database states uniformly:

      1. Fresh DB                — alembic creates everything from zero.
      2. Pre-alembic existing DB — stamp at the initial revision (the schema
         already matches it), then upgrade applies any later migrations.
      3. Already alembic-managed — upgrade applies any unapplied revisions.
    """
    from sqlalchemy import inspect as _inspect
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    repo_root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)

    insp = _inspect(_engine)
    table_names = set(insp.get_table_names())
    has_version = "alembic_version" in table_names
    has_other_tables = bool(table_names - {"alembic_version"})

    if has_other_tables and not has_version:
        # Pre-alembic: tables exist but no version row. Stamp at the initial
        # revision so we don't try to re-create existing tables on upgrade.
        script = ScriptDirectory.from_config(cfg)
        revisions = list(script.walk_revisions())  # head first, base last
        if revisions:
            initial = revisions[-1].revision
            command.stamp(cfg, initial)

    command.upgrade(cfg, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    sess = _SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def record_audit(kind: str, message: str, *, strategy_id: str = "", severity: str = "info", **meta) -> None:
    """Convenience helper: append a row to audit_events."""
    with session_scope() as sess:
        sess.add(
            AuditEvent(
                kind=kind, strategy_id=strategy_id, severity=severity, message=message, meta=meta
            )
        )
