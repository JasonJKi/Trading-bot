"""Source adapters. Each module exposes a `SourceAdapter` subclass that
self-registers on import. Importing this package triggers registration of all
built-in adapters.

Each adapter implements the same `adapter → cache → consumer` shape as the
trading-side `src/data/*` modules — just async, with a richer DocumentRow that
already includes cleaned content."""

from src.research.sources.base import DocumentRow, SourceAdapter, all_registered, available_sources

# Import for side-effect (registration). Individual modules should be cheap to
# import — they lazy-import their heavy SDK deps (asyncpraw, yt-dlp, etc.) inside
# their `search()` method, so importing here doesn't pull in the world.
from src.research.sources import (  # noqa: F401
    arxiv,
    github,
    hackernews,
    reddit,
    social_apify,
    web,
    youtube,
)

__all__ = ["DocumentRow", "SourceAdapter", "all_registered", "available_sources"]
