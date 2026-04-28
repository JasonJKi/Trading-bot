"""Hacker News adapter — uses Algolia HN search (free, no key).

HN threads on `algotrading`, `quant`, ML-for-finance show up here regularly with
high-signal comment threads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

ALGOLIA = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item"


@register
class HackerNewsAdapter(SourceAdapter):
    id = "hackernews"
    name = "Hacker News"

    def is_available(self) -> bool:
        return True  # public API

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        rows: list[DocumentRow] = []
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                # Stories only; rank by relevance.
                r = await c.get(
                    ALGOLIA,
                    params={"query": query, "tags": "story", "hitsPerPage": limit},
                )
                r.raise_for_status()
                hits = r.json().get("hits", [])
                for h in hits:
                    obj_id = h.get("objectID")
                    if not obj_id:
                        continue
                    # Pull top-level comments via the firebase API for context.
                    comments = await self._top_comments(c, obj_id, n=10)
                    body_parts = [h.get("story_text") or "", h.get("url") or ""] + comments
                    content = "\n\n---\n\n".join(p for p in body_parts if p)
                    rows.append(
                        DocumentRow(
                            source=self.id,
                            external_id=str(obj_id),
                            url=h.get("url") or f"https://news.ycombinator.com/item?id={obj_id}",
                            title=str(h.get("title", ""))[:512],
                            content=content[:60_000],
                            author=str(h.get("author", "")),
                            published_at=_parse_ts(h.get("created_at_i")),
                            score=float(h.get("points", 0) or 0),
                            meta={
                                "num_comments": h.get("num_comments"),
                                "hn_url": f"https://news.ycombinator.com/item?id={obj_id}",
                            },
                        )
                    )
        except Exception:
            log.exception("hackernews: search failed")
        log.info("hackernews: %d docs for query=%r", len(rows), query)
        return rows

    async def _top_comments(self, client: httpx.AsyncClient, story_id: str, n: int = 10) -> list[str]:
        try:
            r = await client.get(f"{HN_ITEM}/{story_id}.json")
            r.raise_for_status()
            kids = (r.json() or {}).get("kids", [])[:n]
            out: list[str] = []
            for kid in kids:
                rr = await client.get(f"{HN_ITEM}/{kid}.json")
                rr.raise_for_status()
                obj = rr.json() or {}
                txt = obj.get("text") or ""
                if txt:
                    out.append(f"[{obj.get('by', '?')}] {txt}")
            return out
        except Exception:
            return []


def _parse_ts(epoch: int | None) -> datetime | None:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except Exception:
        return None
