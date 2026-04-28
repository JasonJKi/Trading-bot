"""Reddit source adapter — searches subreddits via async PRAW, with a Tavily fallback.

Reddit's API got progressively harder to obtain after 2023 (phone verification,
account-age gate, occasional manual review). To avoid that being a hard block on
the research agent we have a two-backend setup:

  1. If REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET are set, use async PRAW.
     This is the better backend — it pulls post body + top-level comments
     directly from Reddit's API, structured.
  2. Otherwise, if TAVILY_API_KEY is set, fall back to Tavily search restricted
     to reddit.com domains. We get post titles + cleaned page text but no
     structured comments. Good enough for the synthesizer.
  3. With neither, the adapter degrades to []. As designed.

Subreddits searched (PRAW path) are tuned for AI/algo-trading research.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

TAVILY_SEARCH = "https://api.tavily.com/search"
_REDDIT_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)")

# Subreddits relevant to algorithmic / AI trading bot research.
DEFAULT_SUBS = [
    "algotrading",
    "MachineLearning",
    "quant",
    "quantfinance",
    "wallstreetbets",
    "stocks",
    "options",
    "Daytrading",
    "FinancialIndependence",
    "investing",
    "Bogleheads",
]


@register
class RedditAdapter(SourceAdapter):
    id = "reddit"
    name = "Reddit"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.reddit_client_id
        self._client_secret = s.reddit_client_secret
        self._user_agent = s.reddit_user_agent
        self._tavily_key = s.tavily_api_key  # fallback backend

    def _has_praw(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def is_available(self) -> bool:
        return self._has_praw() or bool(self._tavily_key)

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        if self._has_praw():
            return await self._search_praw(query, limit)
        if self._tavily_key:
            return await self._search_via_tavily(query, limit)
        return []

    async def _search_praw(self, query: str, limit: int) -> list[DocumentRow]:
        # Lazy import — asyncpraw is a heavy optional dep.
        try:
            import asyncpraw
        except ImportError:
            log.warning("reddit: asyncpraw not installed (pip install -e '.[research]')")
            return []

        rows: list[DocumentRow] = []
        try:
            reddit = asyncpraw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
        except Exception:
            log.exception("reddit: failed to init client")
            return []

        try:
            # Search across our default sub set joined with `+`. Reddit's multi-sub syntax.
            multi_sub = "+".join(DEFAULT_SUBS)
            sub = await reddit.subreddit(multi_sub)
            async for submission in sub.search(query, sort="relevance", time_filter="year", limit=limit):
                try:
                    await submission.load()
                    body = submission.selftext or ""
                    comments_text: list[str] = []
                    try:
                        # Top-level comments only — depth 0 — to keep tokens reasonable.
                        await submission.comments.replace_more(limit=0)
                        for c in submission.comments[:8]:
                            if hasattr(c, "body") and c.body:
                                comments_text.append(f"[{c.author or '?'} +{c.score}] {c.body}")
                    except Exception:
                        pass

                    content_parts = [body] + comments_text
                    content = "\n\n---\n\n".join(p for p in content_parts if p)

                    rows.append(
                        DocumentRow(
                            source=self.id,
                            external_id=str(submission.id),
                            url=f"https://reddit.com{submission.permalink}",
                            title=str(submission.title or "")[:512],
                            content=content[:60_000],  # hard cap per doc
                            author=str(submission.author or "deleted"),
                            published_at=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
                            score=float(submission.score or 0),
                            meta={
                                "subreddit": str(submission.subreddit),
                                "num_comments": int(submission.num_comments or 0),
                                "upvote_ratio": float(getattr(submission, "upvote_ratio", 0.0)),
                            },
                        )
                    )
                except Exception:
                    log.warning("reddit: skipping one submission due to error", exc_info=True)
                    continue
        except Exception:
            log.exception("reddit: search failed")
        finally:
            try:
                await reddit.close()
            except Exception:
                pass

        log.info("reddit[praw]: %d docs for query=%r", len(rows), query)
        return rows

    async def _search_via_tavily(self, query: str, limit: int) -> list[DocumentRow]:
        """Fallback: Tavily web search restricted to reddit.com.

        We get the post page's cleaned text (title, OP body, often the top
        comments) but lose PRAW's structured comment tree + upvote scores.
        """
        rows: list[DocumentRow] = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    TAVILY_SEARCH,
                    json={
                        "api_key": self._tavily_key,
                        "query": query,
                        "search_depth": "advanced",
                        "include_raw_content": True,
                        "include_domains": ["reddit.com", "old.reddit.com"],
                        "max_results": limit,
                    },
                )
                r.raise_for_status()
                body = r.json()
            for hit in body.get("results", []):
                url = hit.get("url", "")
                if not url:
                    continue
                # Use Reddit's post id as external_id when we can extract it,
                # so PRAW + Tavily results dedupe across runs.
                m = _REDDIT_POST_ID_RE.search(url)
                ext_id = m.group(1) if m else url
                content = hit.get("raw_content") or hit.get("content") or ""
                rows.append(
                    DocumentRow(
                        source=self.id,
                        external_id=ext_id,
                        url=url,
                        title=str(hit.get("title", ""))[:512],
                        content=content[:60_000],
                        author="",
                        published_at=_parse_iso(hit.get("published_date")),
                        score=float(hit.get("score", 0.0)),
                        meta={"backend": "tavily", "tavily_score": hit.get("score")},
                    )
                )
        except Exception:
            log.exception("reddit[tavily]: search failed")
        log.info("reddit[tavily]: %d docs for query=%r", len(rows), query)
        return rows


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
