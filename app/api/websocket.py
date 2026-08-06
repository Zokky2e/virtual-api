"""
WebSocket endpoint for live file-change notifications. Browsers can't set
an Authorization header on the WebSocket handshake, so the Firebase ID
token is passed as a query parameter instead — same verification path
(verify_id_token) as every other endpoint, just a different place to find
the token.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.firebase import TokenVerificationError, verify_id_token
from app.websocket.manager import manager

router = APIRouter(tags=["websocket"])

# WebSocket close code for "authentication failed" — the 4000-4999 range
# is reserved for application use per RFC 6455.
_CLOSE_UNAUTHORIZED = 4401


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str) -> None:
    try:
        claims = verify_id_token(token)
    except TokenVerificationError:
        await websocket.close(code=_CLOSE_UNAUTHORIZED)
        return

    owner_id = claims["uid"]
    await manager.connect(owner_id, websocket)
    try:
        while True:
            # Clients don't need to send anything over this socket — this
            # just keeps the coroutine alive and lets WebSocketDisconnect
            # surface naturally when the client goes away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(owner_id, websocket)