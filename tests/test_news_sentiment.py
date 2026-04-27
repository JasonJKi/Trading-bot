"""News adapter + sentiment aggregation tests (no torch — we stub FinBERT)."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.data import news, sentiment
from src.bots.sentiment import SentimentStrategy
from src.core.store import NewsItem, init_db, session_scope
from src.core.strategy import StrategyContext


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test_secret")
    from src import config
    from src.core import store

    config._settings = None
    store._engine = None
    store._SessionLocal = None
    init_db()
    yield tmp.name
    os.unlink(tmp.name)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, params=None, headers=None):
        self.calls += 1
        return _FakeResponse(self.payload)


def _alpaca_news_row(item_id, symbols, headline, when, source="WSJ"):
    return {
        "id": item_id,
        "symbols": symbols,
        "headline": headline,
        "summary": headline + " summary",
        "created_at": when,
        "source": source,
        "url": "https://example.com/" + str(item_id),
        "author": "x",
    }


def test_news_returns_empty_without_creds(temp_db, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    from src import config

    config._settings = None
    assert news.fetch_recent_news(["AAPL"]) == []


def test_news_fetch_explodes_per_symbol_listing(temp_db):
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "news": [
            _alpaca_news_row("1", ["AAPL", "MSFT"], "two tickers", now_iso, "Reuters"),
            _alpaca_news_row("2", ["AAPL"], "just apple", now_iso, "WSJ"),
        ]
    }
    fake = _FakeClient(payload)
    rows = news.fetch_recent_news(["AAPL", "MSFT"], hours=24, http=fake)
    # First item should produce 2 rows (one per symbol), second produces 1.
    assert len(rows) == 3
    assert {r.symbol for r in rows} == {"AAPL", "MSFT"}


def test_signed_score_mapping():
    assert sentiment._signed_score("positive", 0.9) == pytest.approx(0.9)
    assert sentiment._signed_score("negative", 0.7) == pytest.approx(-0.7)
    assert sentiment._signed_score("neutral", 0.5) == 0.0


def _seed_news(symbol, score, hours_ago=1.0, source="WSJ", label="positive"):
    with session_scope() as sess:
        sess.add(
            NewsItem(
                external_id=f"{symbol}-{hours_ago}-{source}",
                published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                symbol=symbol,
                headline=f"{symbol} news",
                summary="",
                source=source,
                url="",
                sentiment_score=score,
                sentiment_label=label,
                sentiment_model="ProsusAI/finbert",
            )
        )


def test_rolling_sentiment_decays_with_age(temp_db):
    _seed_news("AAPL", score=+1.0, hours_ago=0.1, source="WSJ")
    _seed_news("AAPL", score=-1.0, hours_ago=8.0, source="WSJ")  # outside default window
    agg = sentiment.rolling_sentiment("AAPL", hours=4)
    # Old article excluded by window -> only the recent +1 counts.
    assert agg.score == pytest.approx(1.0, abs=1e-6)
    assert agg.n_articles == 1


def test_rolling_sentiment_recency_weighting(temp_db):
    _seed_news("MSFT", score=+1.0, hours_ago=0.1, source="A")
    _seed_news("MSFT", score=-1.0, hours_ago=3.5, source="B")
    agg = sentiment.rolling_sentiment("MSFT", hours=4, half_life_hours=2.0)
    # Fresh +1 weighted heavier than 3.5h-old -1 -> overall positive.
    assert agg.score > 0
    assert agg.n_articles == 2
    assert agg.n_distinct_sources == 2


def test_strategy_emits_long_when_threshold_cleared(temp_db, monkeypatch):
    monkeypatch.setenv("SENTIMENT_UNIVERSE", "AAPL")
    # Three articles, two sources, all positive enough.
    _seed_news("AAPL", score=+0.9, hours_ago=0.1, source="WSJ")
    _seed_news("AAPL", score=+0.8, hours_ago=1.0, source="Reuters")
    _seed_news("AAPL", score=+0.7, hours_ago=2.0, source="WSJ")

    bot = SentimentStrategy({"score_threshold": 0.5, "min_articles": 3, "min_sources": 2})
    ctx = StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={}, bot_equity=25_000, regime="bull"
    )
    targets = bot.target_positions(ctx)
    assert len(targets) == 1
    assert targets[0].symbol == "AAPL"
    assert targets[0].weight > 0


def test_strategy_skips_below_threshold(temp_db, monkeypatch):
    monkeypatch.setenv("SENTIMENT_UNIVERSE", "AAPL")
    _seed_news("AAPL", score=+0.4, hours_ago=0.1, source="WSJ")
    _seed_news("AAPL", score=+0.3, hours_ago=1.0, source="Reuters")
    _seed_news("AAPL", score=+0.2, hours_ago=2.0, source="WSJ")

    bot = SentimentStrategy({"score_threshold": 0.6, "min_articles": 3, "min_sources": 2})
    ctx = StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={}, bot_equity=25_000, regime="bull"
    )
    assert bot.target_positions(ctx) == []


def test_strategy_skips_in_crisis(temp_db, monkeypatch):
    monkeypatch.setenv("SENTIMENT_UNIVERSE", "AAPL")
    _seed_news("AAPL", score=+0.9, hours_ago=0.1, source="WSJ")
    _seed_news("AAPL", score=+0.9, hours_ago=1.0, source="Reuters")
    _seed_news("AAPL", score=+0.9, hours_ago=2.0, source="WSJ")
    bot = SentimentStrategy()
    ctx = StrategyContext(
        now=datetime.now(timezone.utc), cash=25_000, positions={}, bot_equity=25_000, regime="crisis"
    )
    assert bot.target_positions(ctx) == []
