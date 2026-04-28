"""Persistence helpers — kept separate from pipeline.py so they're importable
without dragging the pydantic-ai dep tree along (tests + the dashboard reader
shouldn't need the LLM stack).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.core.store import (
    ResearchDocument,
    ResearchFinding,
    ResearchQuery,
    session_scope,
)
from src.research.sources.base import DocumentRow


def existing_doc_keys() -> set[tuple[str, str]]:
    """Pre-load (source, external_id) pairs already in the DB so we don't
    re-fetch documents shared with prior runs."""
    with session_scope() as sess:
        rows = sess.execute(
            select(ResearchDocument.source, ResearchDocument.external_id)
        ).all()
    return {(r[0], r[1]) for r in rows}


def persist_documents(query_id: int, docs: list[DocumentRow]) -> list[int]:
    """Upsert docs by (source, external_id). Returns the ids of NEW rows only."""
    new_ids: list[int] = []
    if not docs:
        return new_ids
    with session_scope() as sess:
        for d in docs:
            stmt = (
                sqlite_insert(ResearchDocument)
                .values(
                    query_id=query_id,
                    source=d.source,
                    external_id=d.external_id,
                    url=d.url,
                    title=d.title,
                    author=d.author,
                    published_at=d.published_at,
                    content=d.content,
                    score=d.score,
                    quality=0.0,
                    meta=d.meta,
                )
                .on_conflict_do_nothing(index_elements=["source", "external_id"])
                .returning(ResearchDocument.id)
            )
            result = sess.execute(stmt).first()
            if result is not None:
                new_ids.append(result[0])
    return new_ids


def persist_findings(query_id: int, findings, docs: list[DocumentRow]) -> int:
    """Map citation indices (positions in `docs`) to actual ResearchDocument.ids
    and write ResearchFinding rows. Returns the count written."""
    if not findings:
        return 0
    keys = [(d.source, d.external_id) for d in docs]
    id_map: dict[tuple[str, str], int] = {}
    with session_scope() as sess:
        for src, ext in keys:
            row = sess.execute(
                select(ResearchDocument.id).where(
                    ResearchDocument.source == src,
                    ResearchDocument.external_id == ext,
                )
            ).first()
            if row is not None:
                id_map[(src, ext)] = row[0]

        for f in findings:
            cited_ids = []
            for idx in f.citations:
                if 0 <= idx < len(keys):
                    db_id = id_map.get(keys[idx])
                    if db_id is not None:
                        cited_ids.append(db_id)
            sess.add(
                ResearchFinding(
                    query_id=query_id,
                    category=f.category,
                    title=f.title,
                    summary=f.summary,
                    detail=f.detail,
                    confidence=f.confidence,
                    novelty=f.novelty,
                    actionable=int(f.actionable),
                    citations=cited_ids,
                    tags=f.tags,
                )
            )
    return len(findings)


def mark_done(query_id: int, stats: dict) -> None:
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.status = "done"
        row.completed_at = datetime.now(timezone.utc)
        row.stats = stats


def mark_failed(query_id: int, message: str) -> None:
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.status = "failed"
        row.error = message[:1024]
        row.completed_at = datetime.now(timezone.utc)


def annotate(query_id: int, **meta) -> None:
    """Merge fields into the row's meta dict — used for warnings during a run."""
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.meta = {**(row.meta or {}), **meta}
