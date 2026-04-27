"""Standalone DB initializer used by Fly's `release_command`.

Runs `init_db()` (which creates tables if missing) and exits. Idempotent —
safe to run on every deploy.
"""
from __future__ import annotations

import logging

from src.config import get_settings
from src.core.store import init_db


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    log = logging.getLogger(__name__)
    init_db()
    log.info("schema ready at %s", get_settings().database_url)


if __name__ == "__main__":
    main()
