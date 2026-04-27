"""Congressional trade disclosure adapter.

Primary backend: Quiver Quantitative (https://www.quiverquant.com/) —
their /beta/live/congresstrading endpoint returns a clean JSON list.

Without a Quiver key the adapter is a no-op (returns []) and the strategy
emits no signals. That's intentional: silently scraping Capitol Trades
HTML is brittle and breaks on layout changes; a real production bot should
pay the $10/mo for a stable feed.

Public surface:
  fetch_recent_disclosures(days=30) -> list[DisclosureRow]
  refresh_cache(days=30) -> int           # rows upserted
  recent_buys_for(politicians, days=30) -> list[CongressDisclosure]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config import get_settings
from src.core.store import CongressDisclosure, session_scope

log = logging.getLogger(__name__)

QUIVER_BASE = "https://api.quiverquant.com/beta/live"


@dataclass(slots=True)
class DisclosureRow:
    external_id: str
    politician: str
    chamber: str
    party: str
    symbol: str
    side: str
    amount_low: float
    amount_high: float
    transaction_date: datetime
    disclosure_date: datetime
    source: str
    meta: dict


def _parse_amount_band(s: str) -> tuple[float, float]:
    """Quiver returns ranges like '$1,001 - $15,000'."""
    if not s:
        return 0.0, 0.0
    s = s.replace("$", "").replace(",", "").strip()
    if "-" in s:
        lo, hi = s.split("-", 1)
        try:
            return float(lo.strip()), float(hi.strip())
        except ValueError:
            return 0.0, 0.0
    try:
        v = float(s)
        return v, v
    except ValueError:
        return 0.0, 0.0


def _parse_date(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)


def _normalize_side(s: str) -> str:
    s = (s or "").lower()
    if "purchase" in s or "buy" in s:
        return "buy"
    if "sale" in s or "sell" in s:
        return "sell"
    if "exchange" in s:
        return "exchange"
    return s or "unknown"


def fetch_recent_disclosures(
    days: int = 30, http: httpx.Client | None = None
) -> list[DisclosureRow]:
    """Fetch from Quiver. Returns [] if no API key configured."""
    settings = get_settings()
    if not settings.quiver_api_key:
        log.info("congress: no QUIVER_API_KEY — returning empty list")
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    headers = {"Authorization": f"Token {settings.quiver_api_key}"}
    client = http or httpx.Client(timeout=15)
    try:
        resp = client.get(f"{QUIVER_BASE}/congresstrading", headers=headers)
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        log.exception("congress: Quiver fetch failed")
        return []
    finally:
        if http is None:
            client.close()

    rows: list[DisclosureRow] = []
    for item in raw:
        # Field names per Quiver's docs: Representative, Transaction, Ticker, Range,
        # TransactionDate, ReportDate, House, Party.
        symbol = (item.get("Ticker") or "").upper().strip()
        if not symbol:
            continue
        tdate = _parse_date(item.get("TransactionDate") or item.get("Traded") or "")
        if tdate.date().isoformat() < cutoff:
            continue
        external_id = (
            f"{item.get('Representative', '')}|{symbol}|"
            f"{item.get('TransactionDate', '')}|{item.get('Transaction', '')}|"
            f"{item.get('Range', '')}"
        )
        amount_low, amount_high = _parse_amount_band(item.get("Range", ""))
        rows.append(
            DisclosureRow(
                external_id=external_id,
                politician=str(item.get("Representative", "")).strip(),
                chamber=str(item.get("House", "")).strip(),
                party=str(item.get("Party", "")).strip(),
                symbol=symbol,
                side=_normalize_side(str(item.get("Transaction", ""))),
                amount_low=amount_low,
                amount_high=amount_high,
                transaction_date=tdate,
                disclosure_date=_parse_date(item.get("ReportDate", "")),
                source="quiver",
                meta={"raw_transaction": item.get("Transaction", "")},
            )
        )
    log.info("congress: fetched %d rows from Quiver", len(rows))
    return rows


def refresh_cache(days: int = 30) -> int:
    """Upsert recent disclosures into the cache. Returns rows fetched."""
    rows = fetch_recent_disclosures(days=days)
    if not rows:
        return 0
    with session_scope() as sess:
        for r in rows:
            stmt = sqlite_insert(CongressDisclosure).values(
                external_id=r.external_id,
                politician=r.politician,
                chamber=r.chamber,
                party=r.party,
                symbol=r.symbol,
                side=r.side,
                amount_low=r.amount_low,
                amount_high=r.amount_high,
                transaction_date=r.transaction_date,
                disclosure_date=r.disclosure_date,
                source=r.source,
                meta=r.meta,
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["external_id"])
            sess.execute(stmt)
    return len(rows)


def recent_buys_for(
    politicians: list[str] | None = None,
    days: int = 30,
) -> list[CongressDisclosure]:
    """Read from the cache (no network). The bot calls this each cycle."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as sess:
        q = (
            select(CongressDisclosure)
            .where(CongressDisclosure.side == "buy")
            .where(CongressDisclosure.transaction_date >= cutoff)
        )
        if politicians:
            q = q.where(CongressDisclosure.politician.in_(politicians))
        rows = list(
            sess.execute(q.order_by(CongressDisclosure.transaction_date.desc())).scalars()
        )
        sess.expunge_all()
    return rows
