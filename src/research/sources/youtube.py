"""YouTube source adapter — finds relevant videos and pulls their transcripts.

Strategy:
  - Use yt-dlp's metadata-only search (`ytsearchN:<query>`) to find candidate videos.
    This works without an API key.
  - For each video, fetch the transcript via youtube-transcript-api.
  - Skip videos with no transcript / no captions.

If the user provides a YOUTUBE_API_KEY, we use the official Data API v3 search
(higher quality results, sortable by relevance/views), otherwise fall back to yt-dlp.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/search"


@register
class YouTubeAdapter(SourceAdapter):
    id = "youtube"
    name = "YouTube"

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.youtube_api_key  # optional — works without

    def is_available(self) -> bool:
        # yt-dlp + youtube-transcript-api work without keys; report True if either is importable.
        try:
            import yt_dlp  # noqa: F401
            import youtube_transcript_api  # noqa: F401
        except ImportError:
            return False
        return True

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        if not self.is_available():
            return []

        video_ids = await self._find_videos(query, limit)
        if not video_ids:
            return []

        # Fetch transcripts concurrently. youtube-transcript-api is sync — run in threads.
        rows = await asyncio.gather(*(self._fetch_one(vid) for vid in video_ids))
        return [r for r in rows if r is not None]

    async def _find_videos(self, query: str, limit: int) -> list[str]:
        # Prefer official API if a key is configured (better ranking & metadata).
        if self._api_key:
            return await self._search_api(query, limit)
        return await self._search_ytdlp(query, limit)

    async def _search_api(self, query: str, limit: int) -> list[str]:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(limit, 25),
            "relevanceLanguage": "en",
            "order": "relevance",
            "key": self._api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(YOUTUBE_API, params=params)
                r.raise_for_status()
                body = r.json()
        except Exception:
            log.exception("youtube: API search failed; falling back to yt-dlp")
            return await self._search_ytdlp(query, limit)
        return [item["id"]["videoId"] for item in body.get("items", []) if item.get("id", {}).get("videoId")]

    async def _search_ytdlp(self, query: str, limit: int) -> list[str]:
        def _run() -> list[str]:
            try:
                from yt_dlp import YoutubeDL
            except ImportError:
                return []
            opts = {"quiet": True, "extract_flat": "in_playlist", "skip_download": True, "no_warnings": True}
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                    return [e["id"] for e in info.get("entries", []) if e and e.get("id")]
            except Exception:
                log.exception("youtube: yt-dlp search failed")
                return []

        return await asyncio.to_thread(_run)

    async def _fetch_one(self, video_id: str) -> DocumentRow | None:
        meta = await asyncio.to_thread(self._video_meta, video_id)
        transcript = await asyncio.to_thread(self._transcript, video_id)
        if not transcript:
            return None
        return DocumentRow(
            source=self.id,
            external_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=meta.get("title", "")[:512],
            content=transcript[:120_000],
            author=meta.get("uploader", ""),
            published_at=meta.get("upload_date"),
            score=float(meta.get("view_count", 0) or 0),
            meta={
                "duration": meta.get("duration"),
                "channel": meta.get("uploader"),
                "like_count": meta.get("like_count"),
            },
        )

    @staticmethod
    def _video_meta(video_id: str) -> dict:
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return {}
        opts = {"quiet": True, "skip_download": True, "no_warnings": True}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                upload_date = None
                if info.get("upload_date"):
                    try:
                        upload_date = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                return {
                    "title": info.get("title", ""),
                    "uploader": info.get("uploader", ""),
                    "duration": info.get("duration"),
                    "view_count": info.get("view_count", 0),
                    "like_count": info.get("like_count"),
                    "upload_date": upload_date,
                }
        except Exception:
            return {}

    @staticmethod
    def _transcript(video_id: str) -> str:
        """Pull the English transcript for `video_id`. Returns "" on any failure
        (no transcript, captions disabled, region-blocked, etc.).

        youtube-transcript-api 1.x changed the API: the old classmethod
        `YouTubeTranscriptApi.get_transcript(...)` was replaced by an instance
        method `YouTubeTranscriptApi().fetch(...)` returning a FetchedTranscript
        whose snippets each have a `.text` attribute.
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            return ""
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            return ""
        try:
            return " ".join(s.text for s in fetched if getattr(s, "text", ""))
        except Exception:
            return ""
