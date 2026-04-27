"""FinBERT sentiment scoring + rolling per-symbol aggregation.

Scoring is deliberately heavy (torch + transformers): we lazy-import on
first use so the worker image doesn't have to install [sentiment] extras
unless the bot is actually enabled.

API:
  score_text(text) -> (label, score)               score in [-1, +1]
  score_unscored(batch_size=16) -> n_scored        run on cached rows
  rolling_sentiment(symbol, hours) -> AggSentiment

Rolling aggregation gives a strategy a single number per symbol:
  mean weighted-by-recency score, count of articles, and dominant label.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.core.store import NewsItem, session_scope

log = logging.getLogger(__name__)

MODEL_NAME = "ProsusAI/finbert"
LABELS = ("negative", "neutral", "positive")

_pipeline = None  # cached transformers pipeline


def _load_pipeline():
    """Lazy import — only paid when sentiment scoring actually runs."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
    except ImportError as exc:  # pragma: no cover - covered by [sentiment] extras
        raise RuntimeError(
            "FinBERT requires the [sentiment] extras: pip install '.[sentiment]'"
        ) from exc
    _pipeline = hf_pipeline(
        "text-classification",
        model=MODEL_NAME,
        truncation=True,
        return_all_scores=False,
    )
    return _pipeline


def _signed_score(label: str, score: float) -> float:
    """Map FinBERT's (label, prob) to a single [-1, +1] number.

    FinBERT outputs label in {negative, neutral, positive} and a confidence.
    We map negative -> -score, neutral -> 0, positive -> +score.
    """
    label = label.lower()
    if label == "positive":
        return score
    if label == "negative":
        return -score
    return 0.0


def score_text(text: str) -> tuple[str, float]:
    """Score one text. Returns (label, signed_score in [-1, +1])."""
    if not text or not text.strip():
        return "neutral", 0.0
    pipe = _load_pipeline()
    out = pipe(text[:512])
    if not out:
        return "neutral", 0.0
    label = str(out[0]["label"]).lower()
    conf = float(out[0]["score"])
    return label, _signed_score(label, conf)


def score_unscored(batch_size: int = 16, max_items: int = 200) -> int:
    """Run FinBERT on every NewsItem with no sentiment_label yet."""
    from src.data.news import unscored_items

    rows = unscored_items(limit=max_items)
    if not rows:
        return 0
    pipe = _load_pipeline()

    n_scored = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [(r.headline + ". " + (r.summary or ""))[:512] for r in batch]
        try:
            out = pipe(texts)
        except Exception:
            log.exception("FinBERT scoring failed on batch starting at %d", i)
            continue
        with session_scope() as sess:
            for row, raw in zip(batch, out):
                label = str(raw["label"]).lower()
                conf = float(raw["score"])
                signed = _signed_score(label, conf)
                # Re-fetch in this session for write.
                live = sess.get(NewsItem, row.id)
                if live is None:
                    continue
                live.sentiment_label = label
                live.sentiment_score = signed
                live.sentiment_model = MODEL_NAME
                n_scored += 1
    log.info("sentiment: scored %d items", n_scored)
    return n_scored


@dataclass(slots=True)
class AggSentiment:
    symbol: str
    score: float          # weighted mean in [-1, +1]
    n_articles: int
    n_distinct_sources: int
    last_published: datetime | None


def rolling_sentiment(symbol: str, hours: int = 4, half_life_hours: float = 2.0) -> AggSentiment:
    """Recency-weighted mean sentiment for `symbol` over the last `hours` hours.

    Uses an exponential decay (half-life = `half_life_hours`) so a 6-hour-old
    article counts about 1/8 as much as a 30-minute-old one. The strategy
    typically requires |score| > threshold AND n_articles >= some minimum.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    # SQLite stores naive UTC; strip tz from cutoff so the comparison works.
    cutoff_naive = cutoff.replace(tzinfo=None)
    with session_scope() as sess:
        rows = list(
            sess.execute(
                select(NewsItem)
                .where(NewsItem.symbol == symbol)
                .where(NewsItem.published_at >= cutoff_naive)
                .where(NewsItem.sentiment_label != "")
            ).scalars()
        )
        sess.expunge_all()

    if not rows:
        return AggSentiment(symbol=symbol, score=0.0, n_articles=0, n_distinct_sources=0, last_published=None)

    now = datetime.now(timezone.utc)
    total_w = 0.0
    weighted = 0.0
    sources = set()
    for r in rows:
        # SQLite returns naive datetimes; normalize so subtraction works.
        published = r.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_h = max((now - published).total_seconds() / 3600.0, 0.0)
        w = math.pow(0.5, age_h / max(half_life_hours, 0.1))
        weighted += r.sentiment_score * w
        total_w += w
        if r.source:
            sources.add(r.source)
    score = weighted / total_w if total_w > 0 else 0.0
    return AggSentiment(
        symbol=symbol,
        score=score,
        n_articles=len(rows),
        n_distinct_sources=len(sources),
        last_published=max(r.published_at for r in rows),
    )
