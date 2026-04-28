"""Congress copycat — buys names recently purchased by allowlisted politicians.

Reads from the local CongressDisclosure cache (populated hourly by the
orchestrator). Each cycle:
  1. Pull recent buys (last 14d) by politicians on the allowlist.
  2. Aggregate per-symbol unique-politician count + median dollar size.
  3. Equal-weight long the top symbols, capped per position.

Disclosures are reported up to 45 days late, so this is a low-frequency
strategy. Don't expect intraday activity.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from statistics import median

from src.config import get_settings
from src.core.strategy import Strategy, StrategyContext, TargetPosition
from src.data.congress import recent_buys_for

log = logging.getLogger(__name__)

# Politicians with historically strong alpha. Override via env if you have
# a different list. Comma-separated full names matching how Quiver labels them.
DEFAULT_POLITICIANS = [
    "Nancy Pelosi",
    "Daniel Crenshaw",
    "Tommy Tuberville",
    "Josh Gottheimer",
    "Pat Fallon",
    "Susie Lee",
]


def _allowlist() -> list[str]:
    raw = os.environ.get("CONGRESS_POLITICIANS", "")
    return [p.strip() for p in raw.split(",") if p.strip()] or DEFAULT_POLITICIANS


class CongressStrategy(Strategy):
    id = "congress"
    name = "Congress Copycat"
    description = "Copies recent buys from allowlisted U.S. lawmakers."
    version = "1.0"
    schedule = {"hour": "*/2", "minute": "10"}  # poll every 2 hours

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.lookback_days = int(self.params.get("lookback_days", 14))
        self.min_unique_politicians = int(self.params.get("min_unique_politicians", 1))
        self.max_names = int(self.params.get("max_names", 10))

    def universe(self) -> list[str]:
        # No fixed universe — we trade whatever shows up in disclosures.
        return []

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        if not get_settings().quiver_api_key:
            log.info("congress: idle (no QUIVER_API_KEY)")
            return []

        allowlist = _allowlist()
        rows = recent_buys_for(politicians=allowlist, days=self.lookback_days)
        if not rows:
            log.info("congress: no recent buys from allowlist over %dd", self.lookback_days)
            return []

        # Aggregate: per symbol, count unique politicians + median dollar size.
        per_symbol: dict[str, list] = defaultdict(list)
        for r in rows:
            per_symbol[r.symbol].append(r)

        scored = []
        for sym, hits in per_symbol.items():
            unique_pols = {h.politician for h in hits}
            if len(unique_pols) < self.min_unique_politicians:
                continue
            med_size = median([(h.amount_low + h.amount_high) / 2 for h in hits])
            scored.append(
                {
                    "symbol": sym,
                    "unique_politicians": len(unique_pols),
                    "median_size": med_size,
                    "n_hits": len(hits),
                }
            )

        if not scored:
            return []

        # Rank by (unique_politicians desc, median_size desc).
        scored.sort(key=lambda x: (-x["unique_politicians"], -x["median_size"]))
        scored = scored[: self.max_names]

        cap = get_settings().per_position_pct
        weight = min(1.0 / len(scored), cap)
        targets = [
            TargetPosition(
                symbol=item["symbol"],
                weight=weight,
                meta={
                    "unique_politicians": item["unique_politicians"],
                    "median_size": item["median_size"],
                    "n_hits": item["n_hits"],
                    "regime": ctx.regime,
                },
            )
            for item in scored
        ]
        log.info(
            "congress: %d targets from %d disclosures (lookback=%dd)",
            len(targets), len(rows), self.lookback_days,
        )
        return targets
