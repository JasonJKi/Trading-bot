"""X / TikTok / Instagram via Apify actors.

These platforms aggressively block direct scraping; the realistic-cost path is
Apify ($49/mo basic, pay-per-result). We expose three adapters that share an
Apify client. Without APIFY_TOKEN they degrade to no-op — by design.

Actor choices (community-maintained, swap if needed):
  X / Twitter — `apidojo/twitter-scraper-lite`        (search posts by keyword)
  TikTok      — `clockworks/tiktok-scraper`           (search hashtag/keyword)
  Instagram   — `apify/instagram-hashtag-scraper`     (hashtag-based)

For TikTok/Instagram we get caption text only (no transcripts) — the synthesizer
should treat these as low-content, high-signal-of-interest hits rather than the
primary research substrate. YouTube is where the real teaching content lives.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


class _ApifyMixin:
    actor: str = ""

    def __init__(self) -> None:
        self._token = get_settings().apify_token

    def is_available(self) -> bool:
        return bool(self._token)

    async def _run_actor(self, payload: dict, *, wait_secs: int = 90) -> list[dict]:
        if not self._token:
            return []
        url = f"{APIFY_BASE}/acts/{self.actor.replace('/', '~')}/run-sync-get-dataset-items"
        try:
            async with httpx.AsyncClient(timeout=wait_secs + 10) as c:
                r = await c.post(
                    url,
                    params={"token": self._token, "timeout": wait_secs},
                    json=payload,
                )
                r.raise_for_status()
                return r.json() or []
        except Exception:
            log.exception("apify: actor %s run failed", self.actor)
            return []


@register
class XAdapter(_ApifyMixin, SourceAdapter):
    id = "x"
    name = "X (Twitter) via Apify"
    free = False
    actor = "apidojo/twitter-scraper-lite"

    async def search(self, query: str, limit: int = 20) -> list[DocumentRow]:
        items = await self._run_actor({"searchTerms": [query], "maxItems": limit, "lang": "en"})
        return [_x_to_doc(self.id, it) for it in items if it]


@register
class TikTokAdapter(_ApifyMixin, SourceAdapter):
    id = "tiktok"
    name = "TikTok via Apify"
    free = False
    actor = "clockworks/tiktok-scraper"

    async def search(self, query: str, limit: int = 20) -> list[DocumentRow]:
        items = await self._run_actor({"searchQueries": [query], "resultsPerPage": limit})
        return [_tiktok_to_doc(self.id, it) for it in items if it]


@register
class InstagramAdapter(_ApifyMixin, SourceAdapter):
    id = "instagram"
    name = "Instagram via Apify"
    free = False
    actor = "apify/instagram-hashtag-scraper"

    async def search(self, query: str, limit: int = 20) -> list[DocumentRow]:
        # IG search is hashtag-based; strip non-alphanumerics and use the first 2 keywords.
        tag = "".join(c for c in query if c.isalnum())[:30]
        items = await self._run_actor({"hashtags": [tag], "resultsLimit": limit})
        return [_ig_to_doc(self.id, it) for it in items if it]


def _x_to_doc(source: str, it: dict[str, Any]) -> DocumentRow:
    return DocumentRow(
        source=source,
        external_id=str(it.get("id") or it.get("url") or ""),
        url=str(it.get("url", "")),
        title=str(it.get("text", ""))[:200],
        content=str(it.get("text", ""))[:8000],
        author=str(it.get("author", {}).get("userName", "")),
        published_at=_parse(it.get("createdAt")),
        score=float(it.get("likeCount", 0) or 0),
        meta={
            "retweet_count": it.get("retweetCount"),
            "reply_count": it.get("replyCount"),
            "view_count": it.get("viewCount"),
        },
    )


def _tiktok_to_doc(source: str, it: dict[str, Any]) -> DocumentRow:
    return DocumentRow(
        source=source,
        external_id=str(it.get("id") or it.get("webVideoUrl") or ""),
        url=str(it.get("webVideoUrl", "")),
        title=str(it.get("text", ""))[:200],
        content=str(it.get("text", ""))[:4000],
        author=str(it.get("authorMeta", {}).get("name", "")),
        published_at=_parse(it.get("createTimeISO")),
        score=float(it.get("playCount", 0) or 0),
        meta={
            "diggCount": it.get("diggCount"),
            "shareCount": it.get("shareCount"),
            "musicMeta": it.get("musicMeta"),
        },
    )


def _ig_to_doc(source: str, it: dict[str, Any]) -> DocumentRow:
    return DocumentRow(
        source=source,
        external_id=str(it.get("id") or it.get("url") or ""),
        url=str(it.get("url", "")),
        title=str(it.get("caption", ""))[:200],
        content=str(it.get("caption", ""))[:4000],
        author=str(it.get("ownerUsername", "")),
        published_at=_parse(it.get("timestamp")),
        score=float(it.get("likesCount", 0) or 0),
        meta={"commentsCount": it.get("commentsCount"), "type": it.get("type")},
    )


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
