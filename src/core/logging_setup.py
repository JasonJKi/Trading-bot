"""Structured logging setup. Pretty + colored on a TTY (dev), JSON on stdout in prod.

Use `setup_logging()` once at process startup; everything else uses stdlib `logging`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - thin wrapper
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("strategy_id", "symbol", "order_id", "bot"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger once.

    Format selection:
      LOG_FORMAT=json  -> machine-readable JSON (production)
      LOG_FORMAT=pretty -> rich/colorized (default in TTY)
    """
    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    fmt = os.environ.get("LOG_FORMAT", "auto").lower()

    root = logging.getLogger()
    root.handlers.clear()

    use_json = fmt == "json" or (fmt == "auto" and not sys.stderr.isatty())

    handler = logging.StreamHandler(sys.stdout)
    if use_json:
        handler.setFormatter(_JsonFormatter())
    else:
        try:
            from rich.logging import RichHandler

            handler = RichHandler(rich_tracebacks=True, show_path=False)
        except ImportError:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            )

    root.addHandler(handler)
    root.setLevel(lvl)
    # Tame chatty libs.
    for noisy in ("urllib3", "yfinance", "matplotlib", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
