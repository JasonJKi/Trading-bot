"""Congress copycat — placeholder until the Quiver/Capitol Trades adapter is wired up.

Returns no signals when no API key is configured. The class still exists so the
orchestrator registry resolves cleanly and `enabled_bots=congress` is harmless.
"""
from __future__ import annotations

import logging

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition

log = logging.getLogger(__name__)


class CongressStrategy(Strategy):
    id = "congress"
    name = "Congress Copycat"
    version = "0.1"
    schedule = {"hour": "*/1", "minute": "30"}  # poll hourly

    def universe(self) -> list[str]:
        return []

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        if not get_settings().quiver_api_key:
            log.info("congress bot idle: no QUIVER_API_KEY set")
            return []
        # TODO(phase 4): fetch latest disclosures, filter to allowlist, build targets.
        return []
