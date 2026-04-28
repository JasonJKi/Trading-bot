"""Source adapters. Each module exposes `search(query, limit) -> list[DocumentRow]`
following the same shape as the trading data adapters in src/data/."""

from src.research.sources.base import DocumentRow, SourceAdapter, available_sources

__all__ = ["DocumentRow", "SourceAdapter", "available_sources"]
