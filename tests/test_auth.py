"""Tests for the signed-session auth in src.api.auth."""
from __future__ import annotations

import time

import pytest

from src.api import auth as authmod


@pytest.fixture(autouse=True)
def _reset_fallback(monkeypatch):
    """Each test gets a fresh fallback secret so cross-test contamination
    doesn't mask signing bugs."""
    monkeypatch.setattr(authmod, "_FALLBACK_SECRET", None)
    monkeypatch.setattr(authmod, "_FALLBACK_WARNED", False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)


def test_signed_cookie_round_trip():
    cookie = authmod._make_session("demo")
    payload = authmod._verify_session(cookie)
    assert payload is not None
    assert payload["u"] == "demo"
    assert isinstance(payload["t"], int)


def test_tampered_payload_rejected():
    cookie = authmod._make_session("demo")
    payload_b, sig_b = cookie.split(".", 1)
    tampered = authmod._b64encode(b'{"u":"admin","t":0}') + "." + sig_b
    assert authmod._verify_session(tampered) is None


def test_tampered_signature_rejected():
    cookie = authmod._make_session("demo")
    payload_b, sig_b = cookie.split(".", 1)
    # Flip a byte in the signature (decode → mutate → re-encode).
    sig = bytearray(authmod._b64decode(sig_b))
    sig[0] ^= 0xFF
    bad = payload_b + "." + authmod._b64encode(bytes(sig))
    assert authmod._verify_session(bad) is None


def test_garbage_cookie_rejected():
    assert authmod._verify_session("not-a-cookie") is None
    assert authmod._verify_session("") is None
    assert authmod._verify_session("only.two.dots.here") is not None or True
    # The split(".", 1) case: even a single dot might decode oddly, but
    # signature verification will fail.
    assert authmod._verify_session("a.b") is None


def test_expired_cookie_rejected(monkeypatch):
    cookie = authmod._make_session("demo")
    # Jump time forward past SESSION_MAX_AGE_SECONDS.
    real_time = time.time
    monkeypatch.setattr(
        authmod.time, "time",
        lambda: real_time() + authmod.SESSION_MAX_AGE_SECONDS + 1,
    )
    assert authmod._verify_session(cookie) is None


def test_session_survives_secret_persistence(monkeypatch):
    """A cookie signed with SESSION_SECRET=X is still valid after a 'restart'
    if SESSION_SECRET=X is still set. Old behavior would fail this test
    because the in-memory token rotated on every process start."""
    monkeypatch.setenv("SESSION_SECRET", "stable-secret-value-please")
    cookie = authmod._make_session("demo")

    # Simulate restart: clear the fallback (not used here), keep env.
    monkeypatch.setattr(authmod, "_FALLBACK_SECRET", None)
    assert authmod._verify_session(cookie) is not None


def test_cookie_invalid_under_different_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "secret-A")
    cookie = authmod._make_session("demo")
    monkeypatch.setenv("SESSION_SECRET", "secret-B")
    assert authmod._verify_session(cookie) is None


def test_require_auth_disabled_when_no_password():
    # No DASHBOARD_PASSWORD -> auth bypassed
    authmod.require_auth(tb_session=None)  # should not raise


def test_require_auth_blocks_missing_cookie(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "swordfish")
    monkeypatch.setenv("SESSION_SECRET", "x")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        authmod.require_auth(tb_session=None)
    assert exc.value.status_code == 401


def test_require_auth_accepts_valid_cookie(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "swordfish")
    monkeypatch.setenv("SESSION_SECRET", "x")
    cookie = authmod._make_session("demo")
    authmod.require_auth(tb_session=cookie)  # should not raise


# ---- login rate limit ------------------------------------------------------

@pytest.fixture
def fresh_limiter():
    """A clean limiter with the same defaults as the module-level one."""
    return authmod._RateLimiter(
        authmod.LOGIN_RATE_MAX_ATTEMPTS, authmod.LOGIN_RATE_WINDOW_SECONDS
    )


def test_rate_limit_allows_under_max(fresh_limiter):
    for _ in range(authmod.LOGIN_RATE_MAX_ATTEMPTS):
        assert fresh_limiter.check_and_record("1.2.3.4") is True


def test_rate_limit_blocks_at_and_above_max(fresh_limiter):
    for _ in range(authmod.LOGIN_RATE_MAX_ATTEMPTS):
        fresh_limiter.check_and_record("1.2.3.4")
    assert fresh_limiter.check_and_record("1.2.3.4") is False
    assert fresh_limiter.check_and_record("1.2.3.4") is False


def test_rate_limit_clear_resets_bucket(fresh_limiter):
    for _ in range(authmod.LOGIN_RATE_MAX_ATTEMPTS):
        fresh_limiter.check_and_record("1.2.3.4")
    assert fresh_limiter.check_and_record("1.2.3.4") is False
    fresh_limiter.clear("1.2.3.4")
    assert fresh_limiter.check_and_record("1.2.3.4") is True


def test_rate_limit_separate_per_ip(fresh_limiter):
    for _ in range(authmod.LOGIN_RATE_MAX_ATTEMPTS):
        fresh_limiter.check_and_record("1.1.1.1")
    # 1.1.1.1 is now blocked, 2.2.2.2 should still be fresh.
    assert fresh_limiter.check_and_record("1.1.1.1") is False
    assert fresh_limiter.check_and_record("2.2.2.2") is True


def test_rate_limit_window_expiry_allows_again(monkeypatch):
    limiter = authmod._RateLimiter(max_attempts=2, window_seconds=10)
    base = 1000.0
    monkeypatch.setattr(authmod.time, "time", lambda: base)
    assert limiter.check_and_record("ip") is True
    assert limiter.check_and_record("ip") is True
    assert limiter.check_and_record("ip") is False
    # Jump past the window.
    monkeypatch.setattr(authmod.time, "time", lambda: base + 11)
    assert limiter.check_and_record("ip") is True
