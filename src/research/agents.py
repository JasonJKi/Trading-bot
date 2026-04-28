"""PydanticAI agents — planner, researcher, synthesizer.

Why PydanticAI:
  - Type-safe outputs: every agent's response is validated against a pydantic schema.
    LLMs hallucinate; schema validation makes that hallucination loud and fixable.
  - Native Gemini support, no extra adapter layer.
  - Tool use is just `@agent.tool` — the researcher can call our source adapters
    as first-class tools the LLM picks dynamically.
  - Same library for all three agents → consistent observability via Logfire.

Why three agents instead of one:
  - Separation of *planning* (slow, premium model) from *fetching* (fast, cheap
    model called many times) from *synthesis* (slow, premium model). This is the
    canonical "deep research" pattern Anthropic / OpenAI / Google all converged on.
  - Each agent has a small, testable prompt with a small, validated output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from src.config import get_settings
from src.research.schemas import Finding, FindingsBundle, ResearchPlan, SubQuery
from src.research.sources.base import DocumentRow, available_sources

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------
# Planner — turns a fuzzy user topic into 4-8 concrete, source-aware sub-queries.
# --------------------------------------------------------------------------------

PLANNER_SYSTEM = """You are a research planner for an AI-trading-bot project.

The user gives you a fuzzy topic. Your job:

1. Restate the *objective* — what would success actually look like? Be concrete.
2. Decompose into 4-8 sub-queries that, taken together, cover the topic from
   complementary angles. Examples of useful angles:
     - "what strategies are people running today" (current practice)
     - "what does the academic literature say"   (rigorous foundation)
     - "what are the failure modes / anti-patterns"
     - "what open-source implementations exist"  (production code)
     - "what data sources are required"
     - "how do practitioners size positions / manage risk"
3. For each sub-query, *suggest* the most fruitful source adapters. The available
   adapters are: reddit (practitioners), youtube (tutorials), hackernews (HN-style
   commentary), arxiv (papers), github (code), web (general). Leave preferred_sources
   empty if it should hit all of them.

Style rules:
  - Sub-query text must be SEARCH-friendly: no first-person, no "find me X", just
    the keywords/phrasing you'd type into a search bar.
  - Avoid duplicating angles. Each sub-query should be answerable independently.
  - The goal is depth, not volume — 6 great sub-queries beat 12 redundant ones.
"""


def planner_agent() -> Agent[None, ResearchPlan]:
    s = get_settings()
    return Agent(
        f"google-gla:{s.research_model}",
        output_type=ResearchPlan,
        system_prompt=PLANNER_SYSTEM,
    )


# --------------------------------------------------------------------------------
# Researcher — runs sub-queries against source adapters as tools.
#
# We wire the *available* (creds-present) adapters as tools at agent-construction
# time. The LLM picks which to call. Returns a list of DocumentRow.
# --------------------------------------------------------------------------------

RESEARCHER_SYSTEM = """You are a research executor.

You are given a list of sub-queries and a set of source-search tools. Your job is
to call those tools to gather as many high-quality, distinct documents as possible.

Strategy:
  - For each sub-query, call 2-4 source tools. Prefer tools the planner suggested.
  - Default to limit=8 per call; raise to 15 if the topic seems sparse.
  - Skip tools that returned 0 results on a similar query already.
  - Don't summarize the documents — just return the raw search results. Synthesis
    happens downstream.

When you have collected enough material across all sub-queries, stop calling tools
and respond with a one-paragraph note describing what you collected and any gaps.
"""


@dataclass
class ResearcherDeps:
    """Mutable bag the researcher tools write into. Each tool appends DocumentRows."""

    docs: list[DocumentRow]
    seen_ids: set[tuple[str, str]]   # (source_id, external_id) already added


def _make_tool_for(adapter):
    """Build a single async tool function bound to the given adapter."""
    sid = adapter.id
    sname = adapter.name

    async def tool_impl(ctx: RunContext[ResearcherDeps], query: str, limit: int = 8) -> str:
        rows = await adapter.search(query, limit=limit)
        added = 0
        for r in rows:
            key = (r.source, r.external_id)
            if key in ctx.deps.seen_ids:
                continue
            ctx.deps.seen_ids.add(key)
            ctx.deps.docs.append(r)
            added += 1
        return f"{sname}: {len(rows)} results, {added} new (total docs collected: {len(ctx.deps.docs)})"

    tool_impl.__name__ = f"search_{sid}"
    tool_impl.__doc__ = (
        f"Search {sname} for `query`. Returns up to `limit` documents and adds them "
        f"to the running collection. Output is a 1-line status; use it to decide "
        f"whether to keep going or move on."
    )
    return tool_impl


def researcher_agent() -> Agent[ResearcherDeps, str]:
    s = get_settings()
    agent: Agent[ResearcherDeps, str] = Agent(
        f"google-gla:{s.research_fast_model}",
        deps_type=ResearcherDeps,
        system_prompt=RESEARCHER_SYSTEM,
    )
    sources = available_sources()
    if not sources:
        log.warning("researcher: no source adapters available — nothing to search")
    for sid, adapter in sources.items():
        agent.tool(_make_tool_for(adapter))
        log.info("researcher: registered tool search_%s", sid)
    return agent


# --------------------------------------------------------------------------------
# Synthesizer — reads the document corpus and emits structured findings.
# --------------------------------------------------------------------------------

SYNTHESIZER_SYSTEM = """You synthesize gathered research into structured findings.

You will be given:
  - The original topic + objective.
  - A numbered list of source documents (truncated to ~2k chars each).

Produce 6-15 distinct Findings. Each finding must:
  - Be a self-contained idea worth implementing or knowing about.
  - Have category ∈ {strategy, indicator, framework, risk, data_source, infra,
    anti_pattern, other}.
  - Cite supporting document indices (0-based). No citation = don't include it.
  - Set `actionable=True` only if a developer could plausibly add this to a Python
    trading-bot codebase that already has Alpaca/SQLAlchemy/APScheduler/pandas.

Prioritize:
  - Concrete > abstract: "RSI(2) mean-reversion on SPY with stops at 2x ATR"
    beats "consider mean-reversion strategies".
  - Anti-patterns are valuable — what's commonly tried but doesn't work / overfits.
  - Confidence reflects evidence strength: 1 doc with weak claim → 0.3; 5 docs +
    a paper → 0.9.
  - Novelty is *relative to the other findings in this run* — don't include three
    near-duplicates.

Then write a 2-paragraph executive summary tying the findings together.
"""


def synthesizer_agent() -> Agent[None, FindingsBundle]:
    s = get_settings()
    return Agent(
        f"google-gla:{s.research_model}",
        output_type=FindingsBundle,
        system_prompt=SYNTHESIZER_SYSTEM,
    )


__all__ = [
    "ResearcherDeps",
    "Finding",
    "FindingsBundle",
    "ResearchPlan",
    "SubQuery",
    "planner_agent",
    "researcher_agent",
    "synthesizer_agent",
]
