"""FastAPI app — read-only dashboard backend over the bot's SQLite + Alpaca live data.

In dev: uvicorn src.api.main:app --port 8000 --reload  (Next.js dev server runs separately)
In prod (Docker): the Next.js static export is bundled into web/out/ and served by this app.
"""
# Populate os.environ from .env BEFORE any module reads it. pydantic-settings
# already loads .env into Settings, but auth.py and a few other modules read
# os.environ directly — without this, those reads come back empty under
# launchd (the plist only injects PATH + PYTHONUNBUFFERED), which silently
# disables the dashboard auth gate. Done at the very top so every subsequent
# import sees a populated environ.
# ruff: noqa: E402
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.public_bot_routes import public_bot_router
from src.api.research_routes import router as research_router
from src.api.routes import public_router, router
from src.config import get_settings
from src.core.logging_setup import setup_logging
from src.core.store import init_db

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks. Replaces deprecated @app.on_event."""
    get_settings().validate_for_runtime()
    init_db()
    log.info("api ready")
    yield
    log.info("api shutdown")


app = FastAPI(
    title="Trading Bot API",
    version="1.0",
    description="Read-only dashboard backend. Never moves money.",
    lifespan=lifespan,
)

# Allow the Next.js dev server to call us during development. WEB_PORT comes
# from .env so changing it doesn't break CORS.
_web_port = get_settings().web_port
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_web_port}",
        f"http://127.0.0.1:{_web_port}",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(public_router)
app.include_router(router)
app.include_router(research_router)
app.include_router(public_bot_router)


# --- static dashboard --------------------------------------------------------
# When running in production (Docker), Next.js was built with `output: "export"`
# and the result lives at web/out/. We mount it under "/" so the dashboard and
# the API share an origin (cookies "just work", no CORS preflight in prod).
# In dev this directory is absent and we silently skip the mount — the Next.js
# dev server on WEB_PORT serves the UI instead.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = _REPO_ROOT / "web" / "out"


def _maybe_mount_dashboard() -> None:
    if not _STATIC_DIR.is_dir():
        log.info("dashboard static dir not found at %s — dev mode", _STATIC_DIR)
        return

    # Mount /_next first (StaticFiles for ETag/Range support on JS/CSS chunks).
    next_assets = _STATIC_DIR / "_next"
    if next_assets.is_dir():
        app.mount("/_next", StaticFiles(directory=next_assets), name="next-static")

    # Catch-all SPA fallback. Registered last so /api/* and /_next/* win first.
    # response_model=None keeps FastAPI from trying to derive a Pydantic schema
    # from the union return type — Response subclasses aren't Pydantic models.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def _spa_fallback(full_path: str, request: Request) -> FileResponse | RedirectResponse:
        # Don't swallow unknown API paths — those should 404 as JSON, not HTML.
        if full_path.startswith("api/") or full_path.startswith("_next/"):
            raise HTTPException(status_code=404)
        # Apex (`67quant.com`) is the marketing surface — root request lands
        # directly on /welcome instead of flashing the auth-gated dashboard.
        # Any other path on apex falls through normally so deep links still work.
        if full_path == "" and request.url.hostname == "67quant.com":
            return RedirectResponse(url="/welcome", status_code=302)
        # 1. Exact file in /web/out (favicon, robots.txt, /file.js, …).
        candidate = _STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        # 2. Trailing-slash export (Next writes /positions/ as /positions/index.html).
        as_dir = candidate / "index.html"
        if as_dir.is_file():
            return FileResponse(as_dir)
        # 3. SPA fallback — let the client router handle unknown routes.
        return FileResponse(_STATIC_DIR / "index.html")

    log.info("dashboard mounted from %s", _STATIC_DIR)


_maybe_mount_dashboard()


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for *unexpected* errors. Starlette's MRO-based dispatch means
    HTTPException (and its subclasses) are still handled by FastAPI's built-in
    handler — those produce the right status code and detail. This handler
    only fires for genuinely unhandled exceptions (ValueError, RuntimeError,
    DB errors, etc.); we log the stack for the operator and return a
    sanitized 500 so internal messages don't leak to clients.
    """
    log.exception(
        "unhandled exception in %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(status_code=500, content={"error": "internal_error"})
