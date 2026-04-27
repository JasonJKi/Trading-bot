"""News/sentiment bot — placeholder until FinBERT inference is wired up.

Importing transformers/torch is deferred to phase 5 so the base install stays light.
"""
from __future__ import annotations

import logging

from src.core.strategy import Strategy, StrategyContext, TargetPosition

log = logging.getLogger(__name__)


class SentimentStrategy(Strategy):
    id = "sentiment"
    name = "News Sentiment (FinBERT)"
    schedule = {"hour": "13-21", "minute": "*/15"}  # US trading hours, every 15 min

    def universe(self) -> list[str]:
        return []

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        # TODO(phase 5): consume Alpaca News stream, score with FinBERT, emit weights.
        log.info("sentiment bot idle: not implemented yet")
        return []
