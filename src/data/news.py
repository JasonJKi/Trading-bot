"""News adapter — pulls headlines from the Alpaca News API.

Free with any Alpaca account. Uses the same key/secret as the broker.
Endpoint: https://data.alpaca.markets/v1beta1/news

The adapter degrades to no-op without credentials.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config import get_settings
from src.core.store import NewsItem, session_scope

log = logging.getLogger(__name__)

ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1/news"


@dataclass(slots=True)
class NewsRow:
    external_id: str
    published_at: datetime
    symbol: str
    headline: str
    summary: str
    source: str
    url: str
    meta: dict


def _parse_dt(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_recent_news(
    symbols: list[str],
    *,
    hours: int = 24,
    limit: int = 50,
    http: httpx.Client | None = None,
) -> list[NewsRow]:
    """Pull last `hours` of news for the given symbols. [] if no creds."""
    settings = get_settings()
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        log.info("news: no Alpaca credentials — returning empty")
        return []
    if not symbols:
        return []

    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    params = {
        "symbols": ",".join(symbols),
        "start": start,
        "limit": min(limit, 50),
        "sort": "desc",
    }

    client = http or httpx.Client(timeout=15)
    try:
        resp = client.get(ALPACA_NEWS_BASE, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        log.exception("news: Alpaca fetch failed")
        return []
    finally:
        if http is None:
            client.close()

    rows: list[NewsRow] = []
    for item in body.get("news", []):
        item_id = str(item.get("id", ""))
        if not item_id:
            continue
        sym_list = item.get("symbols") or []
        for sym in sym_list:
            if sym not in symbols:
                continue
            rows.append(
                NewsRow(
                    external_id=f"{item_id}:{sym}",
                    published_at=_parse_dt(item.get("created_at") or item.get("updated_at") or ""),
                    symbol=str(sym).upper(),
                    headline=str(item.get("headline", ""))[:512],
                    summary=str(item.get("summary", ""))[:2048],
                    source=str(item.get("source", "alpaca")),
                    url=str(item.get("url", "")),
                    meta={"author": item.get("author", "")},
                )
            )
    log.info("news: fetched %d rows for %d symbols", len(rows), len(symbols))
    return rows


def refresh_cache(symbols: list[str], hours: int = 24) -> int:
    """Upsert recent news into the cache. Returns rows fetched."""
    rows = fetch_recent_news(symbols, hours=hours)
    if not rows:
        return 0
    with session_scope() as sess:
        for r in rows:
            stmt = sqlite_insert(NewsItem).values(
                external_id=r.external_id,
                published_at=r.published_at,
                symbol=r.symbol,
                headline=r.headline,
                summary=r.summary,
                source=r.source,
                url=r.url,
                meta=r.meta,
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["external_id"])
            sess.execute(stmt)
    return len(rows)


def unscored_items(limit: int = 200) -> list[NewsItem]:
    """News rows that haven't been sentiment-scored yet (label is empty)."""
    with session_scope() as sess:
        rows = list(
            sess.execute(
                select(NewsItem)
                .where(NewsItem.sentiment_label == "")
                .order_by(NewsItem.published_at.desc())
                .limit(limit)
            ).scalars()
        )
        sess.expunge_all()
    return rows
