"""Pluggable alerter for ops events.

Channels enabled by env vars:
  SLACK_WEBHOOK_URL     -> Slack incoming webhook
  DISCORD_WEBHOOK_URL   -> Discord webhook
  ALERT_EMAIL_TO + SMTP_* -> email (SMTP)
  (console is always on)

Severity levels: info | warning | error | critical.
Each alert is also written to AuditEvent for replay.

Usage:
    from src.core.alerter import alert
    alert("warning", "drawdown breach", "bot momentum hit -16% DD",
          strategy_id="momentum")
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Protocol

import httpx

from src.config import Settings, get_settings
from src.core.store import record_audit

log = logging.getLogger(__name__)

SEVERITY_COLOR = {
    "info": "#2F81F7",
    "warning": "#D29922",
    "error": "#F85149",
    "critical": "#A81E1E",
}
SEVERITY_EMOJI = {
    "info": ":information_source:",
    "warning": ":warning:",
    "error": ":x:",
    "critical": ":rotating_light:",
}


@dataclass(slots=True)
class AlertContext:
    severity: str
    title: str
    body: str
    strategy_id: str = ""
    meta: dict | None = None


class Channel(Protocol):  # pragma: no cover - interface
    def send(self, ctx: AlertContext) -> None: ...


class ConsoleChannel:
    def send(self, ctx: AlertContext) -> None:
        level = {"info": logging.INFO, "warning": logging.WARNING,
                 "error": logging.ERROR, "critical": logging.CRITICAL}.get(
            ctx.severity, logging.INFO
        )
        log.log(level, "ALERT [%s] %s — %s", ctx.severity.upper(), ctx.title, ctx.body)


class SlackChannel:
    def __init__(self, webhook_url: str) -> None:
        self.url = webhook_url

    def send(self, ctx: AlertContext) -> None:
        payload = {
            "attachments": [
                {
                    "color": SEVERITY_COLOR.get(ctx.severity, "#666"),
                    "title": f"{SEVERITY_EMOJI.get(ctx.severity, '')} {ctx.title}",
                    "text": ctx.body,
                    "fields": [
                        {"title": "Severity", "value": ctx.severity, "short": True},
                        *(
                            [{"title": "Bot", "value": ctx.strategy_id, "short": True}]
                            if ctx.strategy_id
                            else []
                        ),
                    ],
                }
            ]
        }
        try:
            httpx.post(self.url, json=payload, timeout=5)
        except Exception:
            log.exception("Slack alert failed")


class DiscordChannel:
    def __init__(self, webhook_url: str) -> None:
        self.url = webhook_url

    def send(self, ctx: AlertContext) -> None:
        payload = {
            "embeds": [
                {
                    "title": ctx.title,
                    "description": ctx.body,
                    "color": int(SEVERITY_COLOR.get(ctx.severity, "#666").lstrip("#"), 16),
                    "fields": [
                        {"name": "Severity", "value": ctx.severity, "inline": True},
                        *(
                            [{"name": "Bot", "value": ctx.strategy_id, "inline": True}]
                            if ctx.strategy_id
                            else []
                        ),
                    ],
                }
            ]
        }
        try:
            httpx.post(self.url, json=payload, timeout=5)
        except Exception:
            log.exception("Discord alert failed")


class EmailChannel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, ctx: AlertContext) -> None:
        s = self.settings
        if not (s.smtp_host and s.alert_email_to and s.alert_email_from):
            return
        msg = MIMEText(ctx.body)
        msg["Subject"] = f"[{ctx.severity.upper()}] {ctx.title}"
        msg["From"] = s.alert_email_from
        msg["To"] = s.alert_email_to
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if s.smtp_user and s.smtp_password:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        except Exception:
            log.exception("Email alert failed")


class Alerter:
    """Multi-channel alerter. Auto-discovers configured channels from settings."""

    def __init__(self, channels: list[Channel] | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if channels is not None:
            self.channels = channels
            return
        self.channels = [ConsoleChannel()]
        if self.settings.slack_webhook_url:
            self.channels.append(SlackChannel(self.settings.slack_webhook_url))
        if self.settings.discord_webhook_url:
            self.channels.append(DiscordChannel(self.settings.discord_webhook_url))
        if self.settings.alert_email_to and self.settings.smtp_host:
            self.channels.append(EmailChannel(self.settings))

    def send(self, severity: str, title: str, body: str, **meta) -> None:
        ctx = AlertContext(
            severity=severity,
            title=title,
            body=body,
            strategy_id=meta.pop("strategy_id", ""),
            meta=meta,
        )
        for ch in self.channels:
            try:
                ch.send(ctx)
            except Exception:
                log.exception("alert channel failed: %s", type(ch).__name__)
        record_audit(
            "alert", f"{title}: {body}", strategy_id=ctx.strategy_id,
            severity=severity, **meta,
        )


_alerter: Alerter | None = None


def get_alerter() -> Alerter:
    global _alerter
    if _alerter is None:
        _alerter = Alerter()
    return _alerter


def alert(severity: str, title: str, body: str, **meta) -> None:
    """Convenience function — fires through the singleton Alerter."""
    get_alerter().send(severity, title, body, **meta)
