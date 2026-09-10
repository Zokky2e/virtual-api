"""
FastAPI dependency that verifies the Authorization header and yields the
authenticated caller. Every protected route depends on `get_current_user`
(directly, or transitively via a service that requires it) — there is no
other path by which an owner_id enters the system, and no route should
ever accept owner_id as a client-supplied query/body param instead.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.auth.firebase import TokenVerificationError, verify_id_token
from app.auth.models import AuthUser
from app.auth.stream_token import StreamTokenError
from app.auth.stream_token import verify as verify_stream_token

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = await run_in_threadpool(
            verify_id_token, credentials.credentials
        )
    except TokenVerificationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    return AuthUser(
        uid=claims["uid"],
        email=claims.get("email"),
        email_verified=claims.get("email_verified", False),
    )

async def get_current_user_header_or_query(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    token: str | None = Query(default=None),
) -> AuthUser:
    """
    Same verification as get_current_user, but also accepts the ID token
    as `?token=` — mirrors the /ws handshake workaround. Used only by
    /download and /stream, since Image.network, VideoPlayerController,
    and the PDF viewer's <iframe> on the Flutter client can't attach a
    custom Authorization header.
    """
    raw_token = credentials.credentials if credentials else token
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = await run_in_threadpool(verify_id_token, raw_token)
    except TokenVerificationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return AuthUser(
        uid=claims["uid"],
        email=claims.get("email"),
        email_verified=claims.get("email_verified", False),
    )


async def get_stream_caller(
    item_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    token: str | None = Query(default=None),
) -> str:
    """
    Owner id for a streaming request, accepting either credential.

    A stream token is preferred: it is scoped to this one item and lives
    long enough to outlast a film (see auth/stream_token.py). A Firebase
    ID token still works, so a client that hasn't been updated keeps
    playing — it just keeps the old one-hour ceiling.

    `item_id` comes from the path of whichever route depends on this.
    """
    if token:
        try:
            return verify_stream_token(token, item_id)
        except StreamTokenError:
            # Not a stream token (or not one for this item) — it may still
            # be a Firebase ID token, so fall through rather than 401 here.
            pass

    user = await get_current_user_header_or_query(credentials, token)
    return user.uid
