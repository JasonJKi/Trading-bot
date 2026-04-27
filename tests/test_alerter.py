"""Alerter tests using fake channels."""
from __future__ import annotations

import os
import tempfile

import pytest

from src.config import Settings
from src.core.alerter import AlertContext, Alerter, ConsoleChannel
from src.core.store import AuditEvent, init_db, session_scope


class _CaptureChannel:
    def __init__(self):
        self.sent: list[AlertContext] = []

    def send(self, ctx: AlertContext) -> None:
        self.sent.append(ctx)


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    from src import config
    from src.core import store

    config._settings = None
    store._engine = None
    store._SessionLocal = None
    init_db()
    yield tmp.name
    os.unlink(tmp.name)


def test_alerter_fans_out_to_all_channels(temp_db):
    a = _CaptureChannel()
    b = _CaptureChannel()
    alerter = Alerter(channels=[a, b], settings=Settings())
    alerter.send("warning", "test", "body", strategy_id="momentum")
    assert len(a.sent) == 1
    assert len(b.sent) == 1
    assert a.sent[0].title == "test"
    assert a.sent[0].strategy_id == "momentum"


def test_alerter_writes_audit_event(temp_db):
    alerter = Alerter(channels=[ConsoleChannel()], settings=Settings())
    alerter.send("error", "thing broke", "details", strategy_id="momentum")
    from sqlalchemy import select

    with session_scope() as sess:
        events = sess.execute(select(AuditEvent)).scalars().all()
    assert any(e.kind == "alert" and e.severity == "error" for e in events)


def test_alerter_one_channel_failure_does_not_block_others(temp_db):
    class _Fail:
        def send(self, ctx):
            raise RuntimeError("nope")

    capture = _CaptureChannel()
    alerter = Alerter(channels=[_Fail(), capture], settings=Settings())
    alerter.send("info", "hi", "body")
    assert len(capture.sent) == 1


def test_alerter_auto_discovers_channels_from_env():
    """Slack/Discord channels are added when the env var is set."""
    settings = Settings(slack_webhook_url="https://hooks.slack.com/x")
    alerter = Alerter(settings=settings)
    types = [type(c).__name__ for c in alerter.channels]
    assert "ConsoleChannel" in types
    assert "SlackChannel" in types
    assert "DiscordChannel" not in types
