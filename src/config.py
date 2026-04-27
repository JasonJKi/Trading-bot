"""Environment-driven configuration with a hard guard against accidental live trading."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_CONFIRM_TOKEN = "YES_I_MEAN_IT"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True
    alpaca_live_confirm: str = ""

    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'trading.db'}"

    account_starting_equity: float = 100_000.0
    per_bot_cap: float = 25_000.0
    per_position_pct: float = 0.05
    global_max_drawdown: float = 0.10

    quiver_api_key: str = ""
    newsapi_key: str = ""

    enabled_bots: str = "momentum,mean_reversion"

    momentum_universe: str = "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA"
    mean_reversion_universe: str = "SPY,QQQ,IWM,DIA"
    crypto_universe: str = "BTC/USD,ETH/USD"

    log_level: str = "INFO"

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


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
