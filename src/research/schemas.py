"""Pydantic schemas — the contracts between agents.

Keeping these in one file makes the agent boundaries auditable: planner emits a
`ResearchPlan`, researcher emits `ResearchDocument`s (via tools), synthesizer
emits `Finding`s. Each is a strict pydantic model the LLM is steered to produce.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubQuery(BaseModel):
    """One angle of attack on the user's research topic.

    The planner emits 4-8 of these; the researcher fans them out to source adapters.
    """

    text: str = Field(description="Concrete search-friendly phrasing (no 'find me' or 'I want').")
    rationale: str = Field(description="One sentence on why this angle is worth chasing.")
    preferred_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Adapter ids most likely to have signal for this sub-query. "
            "Choose from: reddit, youtube, hackernews, arxiv, github, web, x, tiktok, instagram. "
            "Empty = let the researcher decide."
        ),
    )


class ResearchPlan(BaseModel):
    topic: str
    objective: str = Field(description="What the user is ultimately trying to learn or build.")
    sub_queries: list[SubQuery] = Field(min_length=1, max_length=12)
    success_criteria: str = Field(
        description="When does this research run count as 'done'? Concrete observable outcomes."
    )


FindingCategory = Literal[
    "strategy",
    "indicator",
    "framework",
    "risk",
    "data_source",
    "infra",
    "anti_pattern",
    "other",
]


class Finding(BaseModel):
    """A single structured insight extracted from the gathered documents."""

    category: FindingCategory
    title: str = Field(description="Short noun phrase, <= 100 chars. No marketing-ese.")
    summary: str = Field(description="1-2 sentences. Plainspoken, technical, no hedging.")
    detail: str = Field(
        description=(
            "Markdown. Mechanism + when it works + when it fails + how to implement. "
            "Cite document indices like [3] referring to the documents passed in."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How well-supported by the docs (0..1).")
    novelty: float = Field(ge=0.0, le=1.0, description="How unique vs other findings in this run (0..1).")
    actionable: bool = Field(description="Could a competent dev plug this into the existing trading-bot repo?")
    citations: list[int] = Field(description="Indices of the supporting documents (0-based).")
    tags: list[str] = Field(default_factory=list, description="Free-form labels: 'mean-reversion', 'crypto', etc.")


class FindingsBundle(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    summary: str = Field(description="2-paragraph executive summary tying the findings together.")
