"""Tavily web-search adapter + lightweight HTML fetcher.

Tavily is the SOTA agent-grade search API: results pre-cleaned for LLM consumption,
1000 free queries/mo. We use `search(include_raw_content=True)` so most documents
arrive ready-to-use; for results without raw content we fall back to httpx + selectolax.

When TAVILY_API_KEY is unset we have NO general web-search backstop, so the agent
will be limited to the platform-specific adapters (Reddit, YouTube, HN, arXiv, GitHub).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

TAVILY_SEARCH = "https://api.tavily.com/search"


@register
class WebSearchAdapter(SourceAdapter):
    id = "web"
    name = "Web (Tavily)"

    def __init__(self) -> None:
        self._key = get_settings().tavily_api_key

    def is_available(self) -> bool:
        return bool(self._key)

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        if not self.is_available():
            return []
        rows: list[DocumentRow] = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    TAVILY_SEARCH,
                    json={
                        "api_key": self._key,
                        "query": query,
                        "search_depth": "advanced",   # 2 credits/query but better signal
                        "include_raw_content": True,  # cleaned page text in response
                        "max_results": limit,
                        "include_answer": False,
                        # Bias the search toward content useful for trading-bot research.
                        "exclude_domains": ["pinterest.com", "youtube.com"],  # YT handled by youtube adapter
                    },
                )
                r.raise_for_status()
                body = r.json()
            for hit in body.get("results", []):
                url = hit.get("url", "")
                if not url:
                    continue
                # `raw_content` is what Tavily returns when present; fall back to summary.
                content = hit.get("raw_content") or hit.get("content") or ""
                if not content:
                    content = await _fetch_clean(url)
                rows.append(
                    DocumentRow(
                        source=self.id,
                        external_id=url,            # URL is stable enough for dedup
                        url=url,
                        title=str(hit.get("title", ""))[:512],
                        content=content[:80_000],
                        author="",
                        published_at=_parse(hit.get("published_date")),
                        score=float(hit.get("score", 0.0)),
                        meta={"tavily_score": hit.get("score")},
                    )
                )
        except Exception:
            log.exception("web: Tavily search failed")
        log.info("web: %d docs for query=%r", len(rows), query)
        return rows


async def _fetch_clean(url: str) -> str:
    """Fallback: fetch URL and strip HTML to readable text."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 trading-bot-research"})
            r.raise_for_status()
            html = r.text
    except Exception:
        return ""
    return await asyncio.to_thread(_html_to_text, html)


def _html_to_text(html: str) -> str:
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        # No selectolax — strip tags with a crude regex (good enough for fallback).
        import re
        return re.sub(r"<[^>]+>", " ", html)[:80_000]
    tree = HTMLParser(html)
    for tag in ("script", "style", "nav", "header", "footer", "aside"):
        for n in tree.css(tag):
            n.decompose()
    body = tree.body
    return (body.text(separator=" ", strip=True) if body else "")[:80_000]


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
