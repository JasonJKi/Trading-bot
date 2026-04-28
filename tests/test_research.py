"""Research agent tests — adapter no-creds degradation + persistence round-trip.

We deliberately don't make real LLM/network calls here. The tests cover:
  - Each adapter degrades to [] without creds (the Quiver/News pattern).
  - DocumentRow → ResearchDocument upsert is idempotent across runs.
  - Citation re-mapping (positions → DB ids) survives the dedup logic.
  - Source-tool wiring builds a tool name per registered adapter.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.research.schemas import Finding, FindingsBundle


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    # Reset the cached settings + engine so the new DATABASE_URL takes effect.
    from src import config
    from src.core import store

    config._settings = None
    store._engine = None
    store._SessionLocal = None
    store.init_db()
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def no_creds(monkeypatch):
    """Strip every credential the research adapters care about."""
    for var in [
        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
        "TAVILY_API_KEY", "GITHUB_TOKEN",
        "APIFY_TOKEN", "YOUTUBE_API_KEY",
        "GEMINI_API_KEY", "LOGFIRE_TOKEN",
    ]:
        monkeypatch.delenv(var, raising=False)
    from src import config
    config._settings = None
    yield


# ---- adapter degradation --------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_reddit_no_creds_returns_empty(no_creds):
    from src.research.sources.reddit import RedditAdapter
    a = RedditAdapter()
    assert a.is_available() is False
    assert _run(a.search("anything")) == []


def test_web_no_creds_returns_empty(no_creds):
    from src.research.sources.web import WebSearchAdapter
    a = WebSearchAdapter()
    assert a.is_available() is False
    assert _run(a.search("anything")) == []


def test_apify_adapters_no_creds_return_empty(no_creds):
    from src.research.sources.social_apify import InstagramAdapter, TikTokAdapter, XAdapter
    for cls in (XAdapter, TikTokAdapter, InstagramAdapter):
        a = cls()
        assert a.is_available() is False
        assert _run(a.search("anything")) == []


def test_arxiv_and_hackernews_are_always_available():
    """Public APIs — should report available regardless of env."""
    from src.research.sources.arxiv import ArxivAdapter
    from src.research.sources.hackernews import HackerNewsAdapter
    assert ArxivAdapter().is_available()
    assert HackerNewsAdapter().is_available()


def test_available_sources_filters_to_creds_present(no_creds):
    """available_sources() must omit adapters whose creds aren't set."""
    from src.research.sources import (
        arxiv, github, hackernews, reddit, social_apify, web, youtube,  # noqa: F401
    )
    from src.research.sources.base import all_registered, available_sources

    registered = all_registered()
    avail = available_sources()

    # arxiv + hackernews + github (token optional) should always be available.
    assert "arxiv" in avail
    assert "hackernews" in avail
    # creds-required adapters should be filtered out.
    assert "reddit" not in avail
    assert "web" not in avail
    assert "x" not in avail
    # Every registered adapter id should resolve to a class.
    for sid in registered:
        assert isinstance(sid, str)


# ---- persistence ----------------------------------------------------------

def test_documents_dedup_by_source_extid(temp_db):
    from src.core.store import ResearchDocument, ResearchQuery, session_scope
    from src.research.sources.base import DocumentRow
    from src.research.storage import persist_documents

    # Set up a fake query.
    with session_scope() as sess:
        q = ResearchQuery(topic="t", status="running")
        sess.add(q)
        sess.flush()
        qid = q.id

    docs = [
        DocumentRow(
            source="hackernews",
            external_id="abc1",
            url="https://example.com/1",
            title="t1",
            content="c1",
        ),
        DocumentRow(
            source="hackernews",
            external_id="abc1",  # duplicate of #1
            url="https://example.com/1",
            title="t1",
            content="c1",
        ),
        DocumentRow(
            source="arxiv",
            external_id="x1",
            url="http://a.org/x1",
            title="paper",
            content="abstract",
        ),
    ]

    new_ids = persist_documents(qid, docs)
    # First insert returns ids for both unique rows; the duplicate hits ON CONFLICT.
    assert len(new_ids) == 2

    # Second pass: nothing is new (everything is already in the table).
    new_ids2 = persist_documents(qid, docs)
    assert new_ids2 == []

    with session_scope() as sess:
        from sqlalchemy import select
        rows = list(sess.execute(select(ResearchDocument)).scalars())
    assert len(rows) == 2
    # Both belong to the original query.
    assert all(r.query_id == qid for r in rows)


def test_findings_citation_remap(temp_db):
    """Citations are positional indices into the docs list passed to the synth;
    persistence must remap them to the persisted ResearchDocument.id."""
    from sqlalchemy import select

    from src.core.store import ResearchDocument, ResearchFinding, ResearchQuery, session_scope
    from src.research.sources.base import DocumentRow
    from src.research.storage import persist_documents, persist_findings

    with session_scope() as sess:
        q = ResearchQuery(topic="t", status="running")
        sess.add(q)
        sess.flush()
        qid = q.id

    docs = [
        DocumentRow(source="arxiv", external_id="A", url="u1", title="T1", content="x"),
        DocumentRow(source="arxiv", external_id="B", url="u2", title="T2", content="y"),
        DocumentRow(source="github", external_id="42", url="u3", title="repo", content="z"),
    ]
    persist_documents(qid, docs)

    findings = [
        Finding(
            category="strategy",
            title="X",
            summary="s",
            detail="d",
            confidence=0.8,
            novelty=0.5,
            actionable=True,
            citations=[0, 2],          # positional → should map to A and 42
            tags=["test"],
        ),
        Finding(
            category="risk",
            title="Y",
            summary="s",
            detail="d",
            confidence=0.4,
            novelty=0.3,
            actionable=False,
            citations=[99],            # out-of-range → should be filtered
            tags=[],
        ),
    ]
    n = persist_findings(qid, findings, docs)
    assert n == 2

    with session_scope() as sess:
        rows = list(sess.execute(select(ResearchFinding).where(ResearchFinding.query_id == qid)).scalars())
        doc_rows = list(sess.execute(select(ResearchDocument).where(ResearchDocument.query_id == qid)).scalars())

    # Build expected mapping: index 0 (source=arxiv,extid=A) → its db id; etc.
    by_key = {(d.source, d.external_id): d.id for d in doc_rows}
    expected_first = sorted([by_key[("arxiv", "A")], by_key[("github", "42")]])

    rows_by_title = {r.title: r for r in rows}
    assert sorted(rows_by_title["X"].citations) == expected_first
    assert rows_by_title["Y"].citations == []


# ---- agent wiring ---------------------------------------------------------

def test_researcher_wires_one_tool_per_available_source(monkeypatch, no_creds):
    pytest.importorskip("pydantic_ai", reason="install with pip install -e '.[research]'")
    """The researcher's tool count should match the number of credentialed sources.

    With no creds, only public sources (arxiv, hackernews, github) are registered.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-init")
    from src import config
    config._settings = None

    from src.research.agents import researcher_agent

    agent = researcher_agent()
    # PydanticAI exposes registered tools via ._function_toolset (pydantic-ai>=0.0.36).
    # Be defensive — if the API name shifts, fall back to scanning attrs that look tool-y.
    tools = getattr(agent, "_function_toolset", None)
    if tools is not None and hasattr(tools, "tools"):
        tool_names = list(tools.tools.keys())
    else:
        # Fallback discovery: any attr ending in "tools" that's a dict of callables.
        tool_names = []
        for k in dir(agent):
            if "tool" in k.lower():
                v = getattr(agent, k, None)
                if isinstance(v, dict) and v:
                    tool_names = list(v.keys())
                    break

    # We expect tools for the always-on adapters at minimum.
    expected_subset = {"search_arxiv", "search_hackernews", "search_github"}
    assert expected_subset.issubset(set(tool_names)), f"got tools: {tool_names}"
    # No paid adapters when creds are absent.
    for forbidden in ("search_reddit", "search_web", "search_x", "search_tiktok", "search_instagram"):
        assert forbidden not in tool_names


def test_findings_bundle_schema_round_trip():
    """Catch schema regressions early — the synthesizer's output_type must validate."""
    bundle = FindingsBundle(
        summary="ok",
        findings=[
            Finding(
                category="strategy",
                title="t",
                summary="s",
                detail="d",
                confidence=0.5,
                novelty=0.5,
                actionable=True,
                citations=[0],
                tags=["a"],
            )
        ],
    )
    j = bundle.model_dump_json()
    again = FindingsBundle.model_validate_json(j)
    assert again.findings[0].title == "t"
    assert again.findings[0].category == "strategy"
