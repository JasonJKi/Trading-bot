"""Source adapter contract.

Every source (reddit, youtube, web, ...) is a subclass of `SourceAdapter` that:
  - Reports `is_available()` — false if creds are missing (degrades to no-op).
  - Implements `async search(query, limit) -> list[DocumentRow]`.

The base contract mirrors src/data/* adapters but adds:
  - async (sources can be slow; researcher fans out concurrently)
  - cleaned `content` field already extracted (no separate fetch step in v1)
  - source-specific `meta` for raw payloads.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


@dataclass(slots=True)
class DocumentRow:
    """One piece of source content. Maps 1:1 to `ResearchDocument` rows."""

    source: str                     # adapter id, e.g. "reddit"
    external_id: str                # stable upstream id — used for upsert dedup
    url: str
    title: str
    content: str                    # cleaned text — comments concatenated, transcript joined, etc.
    author: str = ""
    published_at: datetime | None = None
    score: float = 0.0              # popularity proxy (upvotes / views / stars)
    meta: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    """Stateless source adapter. Subclasses are instantiated once per pipeline run."""

    id: ClassVar[str]               # short identifier, e.g. "reddit"
    name: ClassVar[str]             # display name
    free: ClassVar[bool] = True     # True if usable without paying

    @abstractmethod
    def is_available(self) -> bool:
        """Return False if required credentials are missing. Researcher will skip."""

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        """Return up to `limit` documents matching `query`. Empty list on any failure."""


_REGISTRY: dict[str, type[SourceAdapter]] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    """Decorator: add an adapter class to the registry by its `id`."""
    if not getattr(cls, "id", None):
        raise ValueError(f"{cls.__name__} must define a class-level `id`")
    _REGISTRY[cls.id] = cls
    return cls


def available_sources() -> dict[str, SourceAdapter]:
    """Instantiate every registered adapter and return only the ones with creds."""
    out: dict[str, SourceAdapter] = {}
    for sid, cls in _REGISTRY.items():
        try:
            inst = cls()
        except Exception:
            continue
        if inst.is_available():
            out[sid] = inst
    return out


def all_registered() -> dict[str, type[SourceAdapter]]:
    """Every registered adapter class, regardless of creds. For introspection / tests."""
    return dict(_REGISTRY)
