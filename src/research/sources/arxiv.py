"""arXiv adapter — academic ML/finance papers.

Uses arXiv's free Atom-feed query API. We pull abstract + authors; full PDFs are
left to the web-fetcher if the synthesizer wants them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"

# Categories most relevant to AI trading bot research.
RELEVANT_CATS = ["q-fin.TR", "q-fin.PM", "q-fin.CP", "q-fin.ST", "cs.LG", "stat.ML"]


@register
class ArxivAdapter(SourceAdapter):
    id = "arxiv"
    name = "arXiv"

    def is_available(self) -> bool:
        return True  # public

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        # Bias the query toward finance/ML categories.
        cat_filter = " OR ".join(f"cat:{c}" for c in RELEVANT_CATS)
        q = f"({cat_filter}) AND all:{query}"
        params = {
            "search_query": q,
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        # arXiv asks API consumers to identify themselves via User-Agent and
        # rate-limit to ~1 req per 3s. We retry on 429 with exponential backoff.
        headers = {"User-Agent": "trading-bot-research/0.1 (https://github.com)"}
        rows: list[DocumentRow] = []
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers=headers
            ) as c:
                for attempt in range(3):
                    r = await c.get(f"{ARXIV_API}?{_qs(params)}")
                    if r.status_code == 429 and attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    rows = _parse_atom(r.text, source_id=self.id)
                    break
        except Exception:
            log.exception("arxiv: search failed")
        log.info("arxiv: %d docs for query=%r", len(rows), query)
        return rows


def _qs(params: dict) -> str:
    # arXiv requires `search_query` to remain unencoded for boolean logic.
    parts: list[str] = []
    for k, v in params.items():
        if k == "search_query":
            parts.append(f"{k}={quote_plus(str(v), safe=':()')}")
        else:
            parts.append(f"{k}={quote_plus(str(v))}")
    return "&".join(parts)


def _parse_atom(xml: str, source_id: str) -> list[DocumentRow]:
    # Tiny stdlib XML parse — avoids feedparser dep for this hot path.
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows: list[DocumentRow] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    for entry in root.findall("a:entry", ns):
        eid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        if not eid:
            continue
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        published = entry.findtext("a:published", default="", namespaces=ns)
        authors = [
            (a.findtext("a:name", default="", namespaces=ns) or "").strip()
            for a in entry.findall("a:author", ns)
        ]
        try:
            pub = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None
            if pub and pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except Exception:
            pub = None
        rows.append(
            DocumentRow(
                source=source_id,
                external_id=eid,
                url=eid,
                title=title[:512],
                content=summary[:60_000],
                author=", ".join(authors[:4]),
                published_at=pub,
                score=0.0,
                meta={"all_authors": authors},
            )
        )
    return rows
