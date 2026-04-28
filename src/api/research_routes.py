"""HTTP routes for the research agent — separate file so changes here don't risk
the trading-critical routes in routes.py.

Endpoints:
  GET  /api/research/sources         which adapters are wired & enabled
  GET  /api/research/queries         list runs (most recent first)
  POST /api/research/queries         start a run; returns query_id (background task)
  GET  /api/research/queries/{id}    one run + its findings
  GET  /api/research/queries/{id}/documents  raw docs collected for that run

All write endpoints are gated by the same auth as the rest of the API.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from src.api.auth import require_auth
from src.core.store import (
    ResearchDocument,
    ResearchFinding,
    ResearchQuery,
    init_db,
    session_scope,
)
from src.research.sources.base import all_registered, available_sources

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", dependencies=[Depends(require_auth)])


# ---- schemas --------------------------------------------------------------

class SourceInfo(BaseModel):
    id: str
    name: str
    free: bool
    available: bool


class QuerySummary(BaseModel):
    id: int
    topic: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    docs_collected: int = 0
    findings: int = 0
    error: str = ""


class FindingOut(BaseModel):
    id: int
    category: str
    title: str
    summary: str
    detail: str
    confidence: float
    novelty: float
    actionable: bool
    citations: list[int]
    tags: list[str]


class QueryDetail(BaseModel):
    id: int
    topic: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    plan: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)
    error: str = ""
    findings: list[FindingOut] = Field(default_factory=list)


class DocumentOut(BaseModel):
    id: int
    source: str
    title: str
    url: str
    author: str
    published_at: datetime | None
    score: float


class StartRequest(BaseModel):
    topic: str = Field(min_length=4, max_length=512)


class StartResponse(BaseModel):
    query_id: int


# ---- routes ---------------------------------------------------------------

@router.get("/sources", response_model=list[SourceInfo])
def list_sources() -> list[SourceInfo]:
    avail = available_sources()
    out: list[SourceInfo] = []
    for sid, cls in sorted(all_registered().items()):
        out.append(
            SourceInfo(id=sid, name=cls.name, free=getattr(cls, "free", True), available=sid in avail)
        )
    return out


@router.get("/queries", response_model=list[QuerySummary])
def list_queries(limit: int = 50) -> list[QuerySummary]:
    init_db()
    with session_scope() as sess:
        rows = list(
            sess.execute(
                select(ResearchQuery).order_by(desc(ResearchQuery.id)).limit(limit)
            ).scalars()
        )
        sess.expunge_all()
    return [
        QuerySummary(
            id=r.id,
            topic=r.topic,
            status=r.status,
            created_at=r.created_at,
            completed_at=r.completed_at,
            docs_collected=int((r.stats or {}).get("docs_collected", 0)),
            findings=int((r.stats or {}).get("findings", 0)),
            error=r.error or "",
        )
        for r in rows
    ]


@router.post("/queries", response_model=StartResponse, status_code=202)
def start_query(body: StartRequest, background: BackgroundTasks) -> StartResponse:
    """Start a research run in the background. Returns the query_id immediately
    (status begins as 'pending' and transitions running → done/failed)."""
    init_db()
    # Pre-create the row so the caller has an id to poll right away.
    with session_scope() as sess:
        q = ResearchQuery(topic=body.topic.strip(), status="pending")
        sess.add(q)
        sess.flush()
        qid = q.id
    background.add_task(_run_in_background, body.topic.strip(), qid)
    return StartResponse(query_id=qid)


@router.get("/queries/{query_id}", response_model=QueryDetail)
def get_query(query_id: int) -> QueryDetail:
    init_db()
    with session_scope() as sess:
        q = sess.get(ResearchQuery, query_id)
        if not q:
            raise HTTPException(404, "no such query")
        findings = list(
            sess.execute(
                select(ResearchFinding)
                .where(ResearchFinding.query_id == query_id)
                .order_by(desc(ResearchFinding.confidence))
            ).scalars()
        )
        sess.expunge_all()
    return QueryDetail(
        id=q.id,
        topic=q.topic,
        status=q.status,
        created_at=q.created_at,
        completed_at=q.completed_at,
        plan=q.plan or {},
        stats=q.stats or {},
        error=q.error or "",
        findings=[
            FindingOut(
                id=f.id,
                category=f.category,
                title=f.title,
                summary=f.summary,
                detail=f.detail,
                confidence=f.confidence,
                novelty=f.novelty,
                actionable=bool(f.actionable),
                citations=list(f.citations or []),
                tags=list(f.tags or []),
            )
            for f in findings
        ],
    )


@router.get("/queries/{query_id}/documents", response_model=list[DocumentOut])
def get_query_documents(query_id: int, limit: int = 200) -> list[DocumentOut]:
    init_db()
    with session_scope() as sess:
        rows = list(
            sess.execute(
                select(ResearchDocument)
                .where(ResearchDocument.query_id == query_id)
                .order_by(desc(ResearchDocument.score))
                .limit(limit)
            ).scalars()
        )
        sess.expunge_all()
    return [
        DocumentOut(
            id=r.id,
            source=r.source,
            title=r.title,
            url=r.url,
            author=r.author,
            published_at=r.published_at,
            score=r.score,
        )
        for r in rows
    ]


# ---- background task wrapper ----------------------------------------------

def _run_in_background(topic: str, query_id: int) -> None:
    """Run the async pipeline against an existing pending row.

    FastAPI runs BackgroundTasks after the response is sent. We use asyncio.run
    here because the pipeline manages its own event loop and DB sessions.
    """
    from src.research.pipeline import run_research

    try:
        asyncio.run(run_research(topic, query_id=query_id))
    except Exception as exc:
        log.exception("research[%d] background run failed", query_id)
        with session_scope() as sess:
            row = sess.get(ResearchQuery, query_id)
            if row and row.status not in ("done", "failed"):
                row.status = "failed"
                row.error = str(exc)[:1024]
