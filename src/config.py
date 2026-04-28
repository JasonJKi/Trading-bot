"""Environment-driven configuration with a hard guard against accidental live trading."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_CONFIRM_TOKEN = "YES_I_MEAN_IT"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- broker ---
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True
    alpaca_live_confirm: str = ""

    # --- storage ---
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'trading.db'}"

    # --- capital + risk ---
    account_starting_equity: float = 100_000.0
    per_bot_cap: float = 25_000.0
    per_position_pct: float = 0.05
    global_max_drawdown: float = 0.10
    per_bot_max_drawdown: float = 0.15

    # --- integrations ---
    quiver_api_key: str = ""
    newsapi_key: str = ""

    # --- research agent (optional) ---
    # LLM
    gemini_api_key: str = ""
    research_model: str = "gemini-2.5-pro"           # planner + synthesizer
    research_fast_model: str = "gemini-2.5-flash"    # researcher tool-calls
    research_embed_model: str = "text-embedding-004"
    # Web search
    tavily_api_key: str = ""
    # Reddit (https://www.reddit.com/prefs/apps — "script" type)
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "trading-bot-research/0.1"
    # GitHub PAT (optional — raises rate limits, public-only scopes are fine)
    github_token: str = ""
    # Apify (optional — for X / TikTok / Instagram scraping in v2)
    apify_token: str = ""
    # YouTube Data API key (optional — for keyword search; transcripts work without it)
    youtube_api_key: str = ""
    # Logfire (optional — observability)
    logfire_token: str = ""

    # --- bot enablement / universes ---
    enabled_bots: str = "momentum,mean_reversion"
    momentum_universe: str = "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA"
    mean_reversion_universe: str = "SPY,QQQ,IWM,DIA"
    crypto_universe: str = "BTC/USD,ETH/USD"

    # --- alerting ---
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""
    alert_email_to: str = ""
    alert_email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # --- logging ---
    log_level: str = "INFO"
    log_format: str = "auto"  # auto | pretty | json

    # --- ports (override in .env if any conflict locally) ---
    healthz_port: int = 8081
    api_port: int = 8000
    web_port: int = 3000

    @field_validator("alpaca_paper", mode="before")
    @classmethod
    def _coerce_paper(cls, v):
        if isinstance(v, str):
            return v.strip().lower() not in {"false", "0", "no"}
        return bool(v)

    @property
    def is_live(self) -> bool:
        return not self.alpaca_paper

    @property
    def enabled_bot_list(self) -> list[str]:
        return [b.strip() for b in self.enabled_bots.split(",") if b.strip()]

    def momentum_symbols(self) -> list[str]:
        return [s.strip() for s in self.momentum_universe.split(",") if s.strip()]

    def mean_reversion_symbols(self) -> list[str]:
        return [s.strip() for s in self.mean_reversion_universe.split(",") if s.strip()]

    def crypto_symbols(self) -> list[str]:
        return [s.strip() for s in self.crypto_universe.split(",") if s.strip()]

    def assert_safe_to_trade(self) -> None:
        """Refuse to start in live mode without an explicit confirmation token."""
        if self.is_live and self.alpaca_live_confirm != LIVE_CONFIRM_TOKEN:
            raise RuntimeError(
                "Refusing to start: ALPACA_PAPER=false but ALPACA_LIVE_CONFIRM is not set "
                f"to {LIVE_CONFIRM_TOKEN!r}. This guard prevents accidental real-money trading."
            )

    def _has_any_alert_channel(self) -> bool:
        if self.slack_webhook_url:
            return True
        if self.discord_webhook_url:
            return True
        if (
            self.alert_email_to
            and self.smtp_host
            and self.smtp_user
            and self.smtp_password
        ):
            return True
        return False

    def validate_for_runtime(self) -> None:
        """Fail loud at startup on configuration that would silently misbehave.

        Called from every entry point that brings up a real process
        (orchestrator, API, deploy release_command). Admin CLI commands like
        `status`, `pause`, `enable` skip this intentionally — those must work
        even in a partially-configured environment.

        Always:
          - DATABASE_URL must be non-empty.

        In live mode additionally:
          - The live-confirm token must be set (delegates to assert_safe_to_trade).
          - Alpaca creds must both be set.
          - At least one alert channel must be wired. Silent failures during
            live trading are unacceptable.
        """
        if not self.database_url:
            raise RuntimeError("Refusing to start: DATABASE_URL is empty.")

        if not self.is_live:
            return

        self.assert_safe_to_trade()

        if not (self.alpaca_api_key and self.alpaca_api_secret):
            raise RuntimeError(
                "Refusing to start in live mode: ALPACA_API_KEY and "
                "ALPACA_API_SECRET must both be set."
            )

        if not self._has_any_alert_channel():
            raise RuntimeError(
                "Refusing to start in live mode: no alert channel is configured. "
                "Set at least one of SLACK_WEBHOOK_URL, DISCORD_WEBHOOK_URL, or "
                "(ALERT_EMAIL_TO + SMTP_HOST + SMTP_USER + SMTP_PASSWORD). "
                "Live trading without alerts is not safe."
            )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
