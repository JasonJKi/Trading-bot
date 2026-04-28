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
from fastapi.responses import FileResponse, JSONResponse

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
# Preview tree, populated by `make mac-deploy-preview`. When a request comes in
# on `preview.67quant.com` we serve from here instead of _STATIC_DIR — same
# uvicorn process, same /api/*, just a different static frontend bundle.
_PREVIEW_STATIC_DIR = _REPO_ROOT / "web-preview" / "out"
_PREVIEW_HOST = "preview.67quant.com"


def _maybe_mount_dashboard() -> None:
    if not _STATIC_DIR.is_dir():
        log.info("dashboard static dir not found at %s — dev mode", _STATIC_DIR)
        return

    # Catch-all SPA fallback. Registered last so /api/* wins first.
    # response_model=None keeps FastAPI from deriving a Pydantic schema from
    # the union return type — Response subclasses aren't Pydantic models.
    #
    # We deliberately handle /_next/* here too (no separate StaticFiles mount)
    # so the preview hostname can serve a different bundle. Loses StaticFiles'
    # ETag/Range support but Next's hashed-filename assets get a hard 1-year
    # cache header below, and Cloudflare's edge caches them.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def _spa_fallback(full_path: str, request: Request) -> FileResponse:
        # Unknown /api/* paths must 404 as JSON, not return HTML.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        # Host-route: preview.67quant.com → web-preview/out/, anything else
        # → web/out/. Falls back to the prod tree if preview hasn't been
        # populated yet (first deploy state).
        host = (request.url.hostname or "").lower()
        if host == _PREVIEW_HOST and _PREVIEW_STATIC_DIR.is_dir():
            base = _PREVIEW_STATIC_DIR
        else:
            base = _STATIC_DIR

        # 1. Exact file in the chosen tree (favicon, robots.txt, /_next/...).
        candidate = base / full_path
        if candidate.is_file():
            response = FileResponse(candidate)
            # Next.js /_next/static/* uses content-hashed filenames; safe to
            # cache forever at the browser + Cloudflare edge.
            if full_path.startswith("_next/static/"):
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            return response
        # 2. Trailing-slash export (Next writes /positions/ as /positions/index.html).
        as_dir = candidate / "index.html"
        if as_dir.is_file():
            return FileResponse(as_dir)
        # 3. SPA fallback — let the client router handle unknown routes.
        return FileResponse(base / "index.html")

    log.info(
        "dashboard mounted from %s (preview tree: %s, %s)",
        _STATIC_DIR,
        _PREVIEW_STATIC_DIR,
        "present" if _PREVIEW_STATIC_DIR.is_dir() else "not yet populated",
    )


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
