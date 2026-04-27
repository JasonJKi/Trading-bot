"""Strategy abstract base. Every bot inherits this so the orchestrator can drive them uniformly."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping


@dataclass(slots=True)
class Signal:
    symbol: str
    direction: str  # "long" | "short" | "flat"
    strength: float = 1.0  # 0..1, used for sizing weight
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class TargetPosition:
    """Desired post-rebalance position expressed as a fraction of the bot's allocated capital."""

    symbol: str
    weight: float  # signed: + long, - short; magnitude bounded per-position cap
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class StrategyContext:
    now: datetime
    cash: float
    positions: Mapping[str, float]  # symbol -> qty
    bot_equity: float


class Strategy(ABC):
    """Subclass per bot. The orchestrator calls `target_positions(ctx)` each cycle."""

    #: Stable, machine-readable id used as a foreign key in the store.
    id: str = "abstract"
    #: Human-readable name shown in the dashboard.
    name: str = "Abstract Strategy"
    #: Bumped whenever signal-generating logic changes. Stamped on every signal,
    #: trade, and order so historical rows stay reproducible.
    version: str = "1"
    #: Cron-style schedule used by the orchestrator (APScheduler CronTrigger fields).
    schedule: dict = {"hour": "*", "minute": "5"}

    def __init__(self, params: dict | None = None) -> None:
        self.params = params or {}

    @abstractmethod
    def universe(self) -> list[str]:
        """Symbols this strategy is allowed to trade."""

    @abstractmethod
    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        """Return the desired weights for each symbol this cycle.

        Symbols absent from the returned list are treated as flat (target=0).
        """

    def on_start(self) -> None:  # pragma: no cover - default no-op
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
