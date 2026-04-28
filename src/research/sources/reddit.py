"""Reddit source adapter — searches subreddits via async PRAW.

Quant/algo communities have years of strategy discussion. We search a default set
focused on AI trading bots and pull post body + top comment thread (1-level deep).

Free: requires a Reddit "script" app (client_id + secret).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

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

    def is_available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        if not self.is_available():
            return []
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

        log.info("reddit: %d docs for query=%r", len(rows), query)
        return rows
