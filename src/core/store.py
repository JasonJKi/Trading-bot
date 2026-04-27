"""SQLAlchemy models + session factory. Single source of truth for all persisted state."""
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
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))  # buy / sell
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    order_id: Mapped[str] = mapped_column(String(64), default="")
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
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # long / short / flat
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    acted: Mapped[int] = mapped_column(Integer, default=0)  # 0/1 — kept simple for SQLite
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


Index("ix_trades_strategy_ts", Trade.strategy_id, Trade.ts)
Index("ix_equity_strategy_ts", EquitySnapshot.strategy_id, EquitySnapshot.ts)


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
