"""Auth-related Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel


class AuthUser(BaseModel):
    """
    The authenticated caller, extracted from a verified Firebase ID token.
    Routers and services work with this — never with the raw token or raw
    decoded claims dict — so there's exactly one shape for "who is making
    this request" throughout the app.
    """

    uid: str
    email: str | None = None
    email_verified: bool = False