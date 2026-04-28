"""Pipeline: planner → researcher → synthesizer, with persistence to SQLite.

Every run:
  1. Inserts a ResearchQuery row (status=running).
  2. Calls the planner — saves the plan onto the row.
  3. Calls the researcher with the plan — saves each ResearchDocument
     (idempotent: dedup by (source, external_id) globally so popular docs are
     reused across runs).
  4. Calls the synthesizer with the doc corpus — saves ResearchFinding rows.
  5. Marks the query done with stats.

Concurrency: the researcher's tools are concurrent under the hood; we don't try
to parallelize sub-queries at the agent level — the LLM decides ordering.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config import get_settings
from src.core.store import (
    ResearchDocument,
    ResearchFinding,
    ResearchQuery,
    init_db,
    session_scope,
)
from src.research.agents import (
    ResearcherDeps,
    planner_agent,
    researcher_agent,
    synthesizer_agent,
)
from src.research.schemas import ResearchPlan
from src.research.sources.base import DocumentRow, available_sources

log = logging.getLogger(__name__)


def _ensure_logfire() -> None:
    """If LOGFIRE_TOKEN is set, configure logfire so PydanticAI auto-traces.

    No-op otherwise. PydanticAI doesn't require it; this just gives us free
    timing + token-cost spans when a token is present.
    """
    s = get_settings()
    token = s.logfire_token or os.environ.get("LOGFIRE_TOKEN")
    if not token:
        return
    try:
        import logfire  # type: ignore[import-not-found]
        logfire.configure(token=token, service_name="trading-bot.research", send_to_logfire="if-token-present")
        logfire.instrument_pydantic_ai()
    except Exception:
        log.exception("logfire: setup failed (continuing without)")


def _gemini_key_check() -> None:
    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to your .env (see .env.example) — "
            "the research agent uses Gemini for planning + synthesis."
        )
    # PydanticAI's google-gla provider reads from GEMINI_API_KEY env var.
    os.environ.setdefault("GEMINI_API_KEY", s.gemini_api_key)


async def run_research(topic: str, *, query_id: int | None = None) -> int:
    """Execute one full research run for `topic`. Returns the ResearchQuery id.

    Pass `query_id` to reuse an existing pending row (the FastAPI route does this
    so it can return an id to the client immediately and run the work in a
    background task that flips the status field on the same row).

    Idempotency: source documents are deduped globally by (source, external_id),
    so two runs on similar topics naturally share storage but each has its own
    findings tied to that run's perspective.
    """
    _gemini_key_check()
    _ensure_logfire()
    init_db()

    # 1) record (or reuse) the query row.
    with session_scope() as sess:
        if query_id is None:
            q = ResearchQuery(topic=topic, status="running")
            sess.add(q)
            sess.flush()
            query_id = q.id
        else:
            row = sess.get(ResearchQuery, query_id)
            if row is None:
                raise ValueError(f"no such ResearchQuery id={query_id}")
            row.status = "running"
            row.topic = topic  # in case the caller normalized whitespace

    sources = available_sources()
    if not sources:
        _fail(query_id, "no source adapters available — set TAVILY_API_KEY and/or REDDIT_* and try again")
        raise RuntimeError("research: no sources available")
    log.info("research[%d] topic=%r sources=%s", query_id, topic, list(sources.keys()))

    # 2) plan.
    try:
        plan_run = await planner_agent().run(_planner_prompt(topic, sorted(sources.keys())))
        plan: ResearchPlan = plan_run.output
        with session_scope() as sess:
            row = sess.get(ResearchQuery, query_id)
            row.plan = plan.model_dump()
    except Exception as exc:
        _fail(query_id, f"planner failed: {exc}")
        raise

    # 3) research — populate ResearcherDeps.docs.
    deps = ResearcherDeps(docs=[], seen_ids=_existing_doc_keys())
    try:
        await researcher_agent().run(_researcher_prompt(plan), deps=deps, usage_limits=None)
    except Exception as exc:
        log.exception("researcher: agent run failed; will synthesize whatever was collected")
        _audit_partial(query_id, str(exc))

    # Persist the documents we just collected (skip ones already in DB).
    new_doc_ids = _persist_documents(query_id, deps.docs)
    log.info("research[%d] collected docs=%d (new in this run=%d)", query_id, len(deps.docs), len(new_doc_ids))

    if not deps.docs:
        _fail(query_id, "researcher returned 0 documents — check source credentials")
        raise RuntimeError("research: no documents found")

    # 4) synthesize.
    try:
        bundle_run = await synthesizer_agent().run(_synthesizer_prompt(plan, deps.docs))
        bundle = bundle_run.output
    except Exception as exc:
        _fail(query_id, f"synthesizer failed: {exc}")
        raise

    # 5) persist findings + mark done.
    finding_count = _persist_findings(query_id, bundle.findings, deps.docs)
    _finish(query_id, stats={
        "docs_collected": len(deps.docs),
        "docs_new": len(new_doc_ids),
        "findings": finding_count,
        "summary": bundle.summary,
        "sources_used": sorted({d.source for d in deps.docs}),
    })
    log.info("research[%d] done: %d findings", query_id, finding_count)
    return query_id


# --------------------------------------------------------------------------------
# Prompt builders — keep the dynamic context out of the system prompts so caching
# works (system prompts stay stable across runs).
# --------------------------------------------------------------------------------

def _planner_prompt(topic: str, available_source_ids: list[str]) -> str:
    return (
        f"Topic: {topic}\n\n"
        f"Available source adapters this run: {', '.join(available_source_ids)}.\n"
        "Adapters NOT in this list won't run, so don't suggest them. Produce a ResearchPlan."
    )


def _researcher_prompt(plan: ResearchPlan) -> str:
    lines = [f"Research objective: {plan.objective}", "", "Sub-queries:"]
    for i, sq in enumerate(plan.sub_queries):
        pref = f" [prefer: {', '.join(sq.preferred_sources)}]" if sq.preferred_sources else ""
        lines.append(f"  {i+1}. {sq.text}{pref}")
        lines.append(f"     why: {sq.rationale}")
    lines.append("")
    lines.append("Use the available search tools to gather documents covering each sub-query.")
    lines.append("Aim for ~30-60 distinct documents total. Report when done.")
    return "\n".join(lines)


def _synthesizer_prompt(plan: ResearchPlan, docs: list[DocumentRow]) -> str:
    parts = [
        f"Topic: {plan.topic}",
        f"Objective: {plan.objective}",
        f"Success criteria: {plan.success_criteria}",
        "",
        f"Documents (n={len(docs)}):",
        "",
    ]
    for i, d in enumerate(docs):
        snippet = (d.content or "").strip().replace("\n", " ")[:2000]
        parts.append(
            f"[{i}] source={d.source} score={d.score:.0f}  url={d.url}\n"
            f"    title: {d.title}\n"
            f"    {snippet}\n"
        )
    parts.append("\nProduce a FindingsBundle. Cite document indices in `citations`.")
    return "\n".join(parts)


# --------------------------------------------------------------------------------
# Persistence helpers.
# --------------------------------------------------------------------------------

def _existing_doc_keys() -> set[tuple[str, str]]:
    """Pre-load the (source, external_id) pairs already in the DB so we don't
    re-fetch documents shared with prior runs."""
    with session_scope() as sess:
        rows = sess.execute(
            select(ResearchDocument.source, ResearchDocument.external_id)
        ).all()
    return {(r[0], r[1]) for r in rows}


def _persist_documents(query_id: int, docs: list[DocumentRow]) -> list[int]:
    """Upsert docs by (source, external_id). Returns the ids of NEW rows."""
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


def _persist_findings(query_id: int, findings, docs: list[DocumentRow]) -> int:
    """Map citation indices (positions in `docs`) to actual ResearchDocument.ids."""
    if not findings:
        return 0
    # Look up the persisted ids for the docs we passed to the synthesizer.
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


def _finish(query_id: int, stats: dict) -> None:
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.status = "done"
        row.completed_at = datetime.now(timezone.utc)
        row.stats = stats


def _fail(query_id: int, message: str) -> None:
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.status = "failed"
        row.error = message[:1024]
        row.completed_at = datetime.now(timezone.utc)


def _audit_partial(query_id: int, message: str) -> None:
    with session_scope() as sess:
        row = sess.get(ResearchQuery, query_id)
        row.meta = {**(row.meta or {}), "researcher_warning": message[:500]}
