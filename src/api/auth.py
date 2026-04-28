"""Cookie-based password gate. If DASHBOARD_PASSWORD is unset, auth is bypassed
(matches the Streamlit dashboard's behavior — open + warn)."""
from __future__ import annotations

import hmac
import logging
import os
import secrets

from fastapi import Cookie, HTTPException, Response, status

log = logging.getLogger(__name__)

COOKIE_NAME = "tb_session"
_TOKEN: str | None = None


def _expected_password() -> str:
    return os.environ.get("DASHBOARD_PASSWORD", "")


def _session_token() -> str:
    """One random token per process lifetime; rotates on every restart."""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = secrets.token_urlsafe(32)
    return _TOKEN


def auth_disabled() -> bool:
    return not _expected_password()


def verify_password(password: str) -> bool:
    expected = _expected_password()
    if not expected:
        return True
    return hmac.compare_digest(password, expected)


def issue_cookie(response: Response) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=_session_token(),
        httponly=True,
        samesite="lax",
        secure=False,  # local dev — flip on when serving over HTTPS
        max_age=60 * 60 * 24 * 7,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_auth(tb_session: str | None = Cookie(default=None)) -> None:
    """FastAPI dependency. Raise 401 unless cookie matches the live token."""
    if auth_disabled():
        return
    if tb_session is None or not hmac.compare_digest(tb_session, _session_token()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
