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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    cash: Mapped[float] = mapped_column(Float)
    position_value: Mapped[float] = mapped_column(Float)
    total_equity: Mapped[float] = mapped_column(Float)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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


Index("ix_trades_strategy_ts", Trade.strategy_id, Trade.ts)
Index("ix_equity_strategy_ts", EquitySnapshot.strategy_id, EquitySnapshot.ts)
Index("ix_orders_strategy_ts", Order.strategy_id, Order.ts)
Index("ix_research_doc_query", ResearchDocument.query_id, ResearchDocument.fetched_at)
Index("ix_research_finding_query", ResearchFinding.query_id, ResearchFinding.category)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = Path(url.replace("sqlite:///", "", 1))
        path.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    global _engine, _SessionLocal
    settings = get_settings()
    _ensure_sqlite_dir(settings.database_url)
    _engine = create_engine(settings.database_url, future=True, json_serializer=json.dumps)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(_engine)


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
