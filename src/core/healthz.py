"""Tiny stdlib-only HTTP health server for external uptime monitoring.

Bound to 0.0.0.0:8081 inside the worker process. Returns 200 + JSON if the
orchestrator process is alive (i.e., this thread is running) AND the DB is
reachable. Anything else returns 503.

No deps beyond the stdlib so it never adds attack surface.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import text

from src.core.store import init_db, session_scope

log = logging.getLogger(__name__)
HEALTHZ_PORT = 8081


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib API
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        try:
            with session_scope() as sess:
                sess.execute(text("SELECT 1"))
            payload = {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}
            self.send_response(200)
        except Exception as exc:  # pragma: no cover - DB failure mode
            payload = {"status": "error", "error": str(exc)[:200]}
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_args, **_kwargs):
        # Silence default access log spam.
        return


def start_in_background(port: int = HEALTHZ_PORT) -> None:
    """Run the health server in a daemon thread. Safe to call multiple times."""
    init_db()  # ensure the engine is built so the first probe doesn't race.
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="healthz", daemon=True)
    thread.start()
    log.info("healthz listening on 0.0.0.0:%d/healthz", port)
