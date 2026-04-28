"""Cookie-based password gate. If DASHBOARD_PASSWORD is unset, auth is bypassed.

Sessions are HMAC-signed cookies (not opaque random tokens). The cookie value
is `<base64-payload>.<base64-signature>`, where the signature is HMAC-SHA256
over the payload using SESSION_SECRET. Survives process restarts and works
across multiple workers as long as they share SESSION_SECRET.

When SESSION_SECRET is unset we fall back to a process-local random secret;
this matches the old behavior (restart = logout) and is fine for local dev,
but in production / multi-worker setups SESSION_SECRET must be configured
and stable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Cookie, HTTPException, Response, status

log = logging.getLogger(__name__)

COOKIE_NAME = "tb_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days

_FALLBACK_SECRET: bytes | None = None
_FALLBACK_WARNED = False


def _expected_password() -> str:
    return os.environ.get("DASHBOARD_PASSWORD", "")


def _cookie_secure() -> bool:
    """Set the `Secure` cookie flag when serving over HTTPS. Defaults to off
    so local dev (http://localhost) keeps working; flip via env in prod."""
    return os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}


def _session_secret() -> bytes:
    """HMAC secret. Prefers SESSION_SECRET env; falls back to a process-local
    random secret with a one-time warning if a password is configured."""
    configured = os.environ.get("SESSION_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")

    global _FALLBACK_SECRET, _FALLBACK_WARNED
    if _FALLBACK_SECRET is None:
        _FALLBACK_SECRET = secrets.token_bytes(32)
        if _expected_password() and not _FALLBACK_WARNED:
            log.warning(
                "SESSION_SECRET is not set; sessions will not survive a restart "
                "and will not be valid across multiple workers. Set "
                "SESSION_SECRET to a stable random string in production."
            )
            _FALLBACK_WARNED = True
    return _FALLBACK_SECRET


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_b: str) -> str:
    sig = hmac.new(_session_secret(), payload_b.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(sig)


def _make_session(user_id: str = "demo") -> str:
    """Build a signed session cookie value. Payload format kept tiny on
    purpose — `u` (user) and `t` (issued-at unix seconds) is enough until
    real auth + tenancy land."""
    payload = {"u": user_id, "t": int(time.time())}
    payload_b = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b}.{_sign(payload_b)}"


def _verify_session(cookie: str) -> dict | None:
    """Return the payload dict if the signature is valid AND the session
    has not expired. Otherwise return None."""
    try:
        payload_b, sig_b = cookie.split(".", 1)
    except ValueError:
        return None

    expected = _sign(payload_b)
    if not hmac.compare_digest(expected, sig_b):
        return None

    try:
        payload = json.loads(_b64decode(payload_b))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    issued_at = payload.get("t")
    if not isinstance(issued_at, (int, float)):
        return None
    if (time.time() - issued_at) > SESSION_MAX_AGE_SECONDS:
        return None
    return payload


def auth_disabled() -> bool:
    return not _expected_password()


def verify_password(password: str) -> bool:
    expected = _expected_password()
    if not expected:
        return True
    return hmac.compare_digest(password, expected)


def issue_cookie(response: Response, user_id: str = "demo") -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=_make_session(user_id),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=SESSION_MAX_AGE_SECONDS,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_auth(tb_session: str | None = Cookie(default=None)) -> None:
    """FastAPI dependency. Raises 401 unless the cookie has a valid signature
    and is not expired."""
    if auth_disabled():
        return
    if tb_session is None or _verify_session(tb_session) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")


# ---- login rate limit ------------------------------------------------------
# In-memory sliding-window limiter keyed by remote IP. Single-process only;
# multi-worker deploys would need a shared store (Redis) — not needed today.
LOGIN_RATE_MAX_ATTEMPTS = 5
LOGIN_RATE_WINDOW_SECONDS = 5 * 60


class _RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max = max_attempts
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check_and_record(self, key: str) -> bool:
        """Record an attempt and return True if it's still under the limit."""
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max:
                return False
            bucket.append(now)
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


_login_limiter = _RateLimiter(LOGIN_RATE_MAX_ATTEMPTS, LOGIN_RATE_WINDOW_SECONDS)


def check_login_rate_limit(client_ip: str) -> bool:
    """Returns True if the IP is allowed to attempt a login."""
    return _login_limiter.check_and_record(client_ip)


def reset_login_rate_limit(client_ip: str) -> None:
    """Clear the bucket for an IP — call after a successful login."""
    _login_limiter.clear(client_ip)
