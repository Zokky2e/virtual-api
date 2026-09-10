"""Response shape for the stream-token endpoints (see
app/auth/stream_token.py for why these exist)."""

from __future__ import annotations

from pydantic import BaseModel


class StreamTokenResponse(BaseModel):
    token: str
    # Unix seconds. The client doesn't currently schedule a refresh off
    # this — it re-resolves whenever a preview is opened — but it's the
    # one piece of information needed to start.
    expires_at: int
