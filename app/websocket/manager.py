"""
Tracks active WebSocket connections, grouped by owner (Firebase uid), and
broadcasts messages to all of that user's connected clients. A user can
have more than one tab/window open at once, so this is a set of
connections per uid, not a single connection.

`manager` at the bottom is the process-wide singleton — every request
(REST or WebSocket) that needs to touch connections goes through this one
instance, not a fresh ConnectionManager().
"""

from __future__ import annotations

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, owner_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(owner_id, set()).add(websocket)

    def disconnect(self, owner_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(owner_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            del self._connections[owner_id]

    async def broadcast(self, owner_id: str, message: dict) -> None:
        """Send `message` as JSON to every connection this owner
        currently has open. A connection that fails to send is dropped
        silently — its disconnect will already be handled by the
        WebSocketDisconnect path in the endpoint itself."""
        connections = self._connections.get(owner_id)
        if not connections:
            return
        stale: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            connections.discard(ws)


manager = ConnectionManager()