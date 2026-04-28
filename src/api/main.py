"""FastAPI app — read-only dashboard backend over the bot's SQLite + Alpaca live data.

Run locally:  uvicorn src.api.main:app --port 8000 --reload
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import public_router, router
from src.api.research_routes import router as research_router
from src.config import get_settings
from src.core.logging_setup import setup_logging
from src.core.store import init_db

setup_logging()
log = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Bot API",
    version="1.0",
    description="Read-only dashboard backend. Never moves money.",
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


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("api ready")
