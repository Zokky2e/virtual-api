"""
Short-lived, item-scoped tokens for the streaming endpoints.

`/desktop/stream/{id}` used to be handed the caller's raw Firebase ID
token as a query parameter, because a <video> element, Image.network and
the PDF iframe cannot attach an Authorization header. That had two
problems.

It expires. Firebase ID tokens last an hour, and the URL is resolved
once when the preview window opens, so a long film 401s partway through
— silently on web, as a generic decoder error on Windows.

And it over-grants. A Firebase ID token is a full-privilege credential
for the entire API, and this one sits in a URL, where it reaches browser
history, proxy logs and Referer headers.

A stream token fixes both: signed here, scoped to one owner and one
item, and valid for a window that comfortably outlasts a viewing
session. Leaking one exposes a single file rather than the account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from functools import lru_cache

from app.config import get_settings


class StreamTokenError(Exception):
    """Raised when a stream token is malformed, expired, signed with a
    different secret, or issued for a different item. Like
    TokenVerificationError, it deliberately doesn't say which."""


@lru_cache
def _ephemeral_secret() -> bytes:
    """Per-process fallback when stream_token_secret isn't configured.

    Tokens then stop verifying after a restart — acceptable, since the
    client re-resolves the URL whenever a preview is reopened — but they
    also can't be verified by a *different* worker, so a multi-worker
    deployment must set STREAM_TOKEN_SECRET in .env.
    """
    return os.urandom(32)


def _secret() -> bytes:
    configured = get_settings().stream_token_secret
    return configured.encode("utf-8") if configured else _ephemeral_secret()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(payload: str) -> str:
    return _b64(hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest())


def mint(owner_id: str, item_id: str, ttl_seconds: int) -> tuple[str, int]:
    """Returns (token, unix expiry). Neither owner ids (Firebase uids or
    the SHARED_OWNER_ID sentinel) nor item ids (hex uuids) contain ':',
    so a plain delimiter is unambiguous here."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{owner_id}:{item_id}:{expires_at}"
    return f"{_b64(payload.encode('utf-8'))}.{_sign(payload)}", expires_at


def verify(token: str, item_id: str) -> str:
    """Returns the owner id the token was minted for, or raises."""
    try:
        encoded_payload, signature = token.split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload = base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        token_owner, token_item, expires_at = payload.rsplit(":", 2)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise StreamTokenError("Malformed stream token.") from exc

    # compare_digest, not ==, so a wrong signature can't be recovered a
    # byte at a time from response timing.
    if not hmac.compare_digest(signature, _sign(payload)):
        raise StreamTokenError("Bad stream token signature.")
    if token_item != item_id:
        raise StreamTokenError("Stream token is for a different item.")
    if int(expires_at) < int(time.time()):
        raise StreamTokenError("Stream token expired.")
    return token_owner
