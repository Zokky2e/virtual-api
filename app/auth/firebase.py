"""
Firebase Admin SDK setup and ID token verification. This is the only
module in the app that imports firebase_admin directly — everything else
(routers, services) depends on auth/dependencies.py's get_current_user,
never on this module or the SDK.
"""

from __future__ import annotations

import hashlib
import threading
import time
from functools import lru_cache

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.config import get_settings


class TokenVerificationError(Exception):
    """Raised when an ID token is missing, malformed, expired, revoked, or
    issued for the wrong Firebase project. Deliberately doesn't
    distinguish which — the HTTP layer only ever needs to return 401
    either way, and finer-grained reasons are logged, not surfaced."""


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    """
    Initializes the Firebase Admin app exactly once per process. If
    `firebase_credentials_path` is set, uses that service account key;
    otherwise falls back to Application Default Credentials (e.g.
    GOOGLE_APPLICATION_CREDENTIALS in the environment, or GCP metadata
    server if ever deployed there).
    """
    if firebase_admin._apps:
        return firebase_admin.get_app()

    settings = get_settings()
    if settings.firebase_credentials_path:
        cred = credentials.Certificate(settings.firebase_credentials_path)
    else:
        cred = credentials.ApplicationDefault()

    return firebase_admin.initialize_app(
        cred, options={"projectId": settings.firebase_project_id}
    )


# How long a successful verification is reused before Firebase is consulted
# again.
#
# `check_revoked=True` costs a live round trip to Google's Identity Toolkit on
# every call — measured at ~220 ms against this project. That is affordable for
# ordinary API calls but not for /stream: VLC issues hundreds of HTTP range
# requests while probing and seeking a single video, and at ~220 ms apiece an
# MKV took ~66 seconds to start playing.
#
# Caching bounds how stale a revocation decision can be instead of removing the
# check. A revoked or signed-out session is still rejected, just up to this many
# seconds later. Lower it for a tighter window at the cost of more round trips;
# set it to 0 to verify on every request as before.
VERIFICATION_CACHE_TTL_SECONDS = 30.0

# Bounds memory if many distinct tokens are seen (token rotation, several users).
_VERIFICATION_CACHE_MAX_ENTRIES = 512

_verification_cache: dict[str, tuple[float, dict]] = {}
_verification_cache_lock = threading.Lock()


def _cache_key(id_token: str) -> str:
    """Tokens are credentials — key the cache by digest, not by the token."""
    return hashlib.sha256(id_token.encode("utf-8")).hexdigest()


def _cached_claims(key: str, now: float) -> dict | None:
    with _verification_cache_lock:
        entry = _verification_cache.get(key)
        if entry is None:
            return None
        expires_at, claims = entry
        if expires_at <= now:
            del _verification_cache[key]
            return None
        return claims


def _store_claims(key: str, claims: dict, now: float) -> None:
    expires_at = now + VERIFICATION_CACHE_TTL_SECONDS

    # Never let a cache entry outlive the token it was derived from, or an
    # expired token would keep working until the TTL lapsed. This compares
    # against `exp`, which is epoch seconds, so the clock here must be
    # time.time() and not time.monotonic().
    token_exp = claims.get("exp")
    if isinstance(token_exp, (int, float)) and not isinstance(token_exp, bool):
        expires_at = min(expires_at, float(token_exp))

    if expires_at <= now:
        return

    with _verification_cache_lock:
        if len(_verification_cache) >= _VERIFICATION_CACHE_MAX_ENTRIES:
            for stale_key in [
                k for k, (exp, _) in _verification_cache.items() if exp <= now
            ]:
                del _verification_cache[stale_key]
            if len(_verification_cache) >= _VERIFICATION_CACHE_MAX_ENTRIES:
                _verification_cache.clear()
        _verification_cache[key] = (expires_at, claims)


def clear_verification_cache() -> None:
    """Drops every cached verification. For tests, and for forcing an
    immediate re-check after revoking a session."""
    with _verification_cache_lock:
        _verification_cache.clear()


def verify_id_token(id_token: str) -> dict:
    """
    Verifies a Firebase ID token and returns its decoded claims (at least
    "uid", usually "email"/"email_verified"). `check_revoked=True` makes
    this call Firebase's revocation-check endpoint, so a signed-out /
    revoked session is rejected here rather than trusting a still
    cryptographically-valid but revoked token.

    Successful verifications are cached for VERIFICATION_CACHE_TTL_SECONDS
    (see above). Failures are never cached, so a rejected token costs a full
    verification every time and cannot be replayed cheaply.

    This does blocking network I/O on a cache miss. Callers in async code must
    dispatch it with `run_in_threadpool` rather than awaiting it inline, or a
    single slow verification stalls the whole event loop.
    """
    if VERIFICATION_CACHE_TTL_SECONDS > 0:
        now = time.time()
        key = _cache_key(id_token)
        cached = _cached_claims(key, now)
        if cached is not None:
            return cached

    app = get_firebase_app()
    try:
        claims = firebase_auth.verify_id_token(id_token, app=app, check_revoked=True)
    except Exception as exc:  # firebase_admin raises several distinct exception types
        raise TokenVerificationError(str(exc)) from exc

    if VERIFICATION_CACHE_TTL_SECONDS > 0:
        _store_claims(key, claims, now)

    return claims