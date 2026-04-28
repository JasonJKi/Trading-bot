"""News sentiment bot.

Reads pre-scored sentiment from the NewsItem cache. Each cycle:
  1. For every symbol in the universe, compute rolling 4h sentiment.
  2. Require |score| > THRESHOLD AND at least N articles from M+ sources.
  3. Long names with positive score, optionally fade names with strongly
     negative score.

Bot runs every 15 minutes during US trading hours; news fetching +
FinBERT scoring run on a separate scheduled job so the bot's own cycle
stays fast and DB-only.
"""
from __future__ import annotations

import logging
import os

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.sentiment import rolling_sentiment

log = logging.getLogger(__name__)


def _universe() -> list[str]:
    raw = os.environ.get(
        "SENTIMENT_UNIVERSE",
        "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,JPM",
    )
    return [s.strip() for s in raw.split(",") if s.strip()]


class SentimentStrategy(Strategy):
    id = "sentiment"
    name = "News Sentiment (FinBERT)"
    description = "Long names with positive rolling news sentiment, intraday."
    version = "1.0"
    schedule = {"hour": "13-21", "minute": "*/15"}  # US session, every 15 min

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.window_hours = float(self.params.get("window_hours", 4.0))
        self.score_threshold = float(self.params.get("score_threshold", 0.6))
        self.min_articles = int(self.params.get("min_articles", 3))
        self.min_sources = int(self.params.get("min_sources", 2))
        self.fade_negative = bool(self.params.get("fade_negative", False))

    def universe(self) -> list[str]:
        return _universe()

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        settings = get_settings()
        if not (settings.alpaca_api_key and settings.alpaca_api_secret):
            log.info("sentiment: idle (no Alpaca creds for news feed)")
            return []
        if ctx.regime == "crisis":
            log.info("sentiment: standing down (regime=crisis)")
            return []

        candidates = []
        for sym in self.universe():
            agg = rolling_sentiment(sym, hours=int(self.window_hours))
            if agg.n_articles < self.min_articles:
                continue
            if agg.n_distinct_sources < self.min_sources:
                continue
            if abs(agg.score) < self.score_threshold:
                continue
            if agg.score > 0:
                candidates.append((sym, agg, +1))
            elif self.fade_negative:
                candidates.append((sym, agg, -1))

        if not candidates:
            log.info("sentiment: no symbols cleared threshold this cycle")
            return []

        cap = settings.per_position_pct
        weight = min(1.0 / len(candidates), cap)
        targets = [
            TargetPosition(
                symbol=sym,
                weight=weight * direction,
                meta={
                    "score": agg.score,
                    "n_articles": agg.n_articles,
                    "n_sources": agg.n_distinct_sources,
                    "regime": ctx.regime,
                },
            )
            for sym, agg, direction in candidates
        ]
        log.info(
            "sentiment: %d targets (regime=%s, threshold=%.2f)",
            len(targets), ctx.regime, self.score_threshold,
        )
        return targets
