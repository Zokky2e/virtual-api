"""
Firebase Admin SDK setup and ID token verification. This is the only
module in the app that imports firebase_admin directly — everything else
(routers, services) depends on auth/dependencies.py's get_current_user,
never on this module or the SDK.
"""

from __future__ import annotations

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


def verify_id_token(id_token: str) -> dict:
    """
    Verifies a Firebase ID token and returns its decoded claims (at least
    "uid", usually "email"/"email_verified"). `check_revoked=True` makes
    this call Firebase's revocation-check endpoint, so a signed-out /
    revoked session is rejected here rather than trusting a still
    cryptographically-valid but revoked token.
    """
    app = get_firebase_app()
    try:
        return firebase_auth.verify_id_token(id_token, app=app, check_revoked=True)
    except Exception as exc:  # firebase_admin raises several distinct exception types
        raise TokenVerificationError(str(exc)) from exc