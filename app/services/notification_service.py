"""
Thin wrapper around the WebSocket ConnectionManager, giving routers a
small, typed surface (`item_created`, `item_moved`, ...) instead of
importing ConnectionManager + events directly and building payload dicts
inline. Called from api/folders.py and api/files.py after a mutation
succeeds — never from inside FileService/FolderService themselves, so
those stay pure metadata/storage logic with no I/O side effects beyond
the DB and disk.
"""

from __future__ import annotations

from app.constants import SHARED_OWNER_ID
from app.database.models import FileRecord
from app.websocket.events import build_event
from app.websocket.manager import ConnectionManager


class NotificationService:
    def __init__(self, manager: ConnectionManager):
        self._manager = manager

    async def item_created(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_created" if record.is_folder else "file_created"
        await self._manager.broadcast(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
            ),
        )

    async def item_renamed(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_renamed" if record.is_folder else "file_renamed"
        await self._manager.broadcast(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
            ),
        )

    async def item_moved(
        self, owner_id: str, record: FileRecord, old_parent_folder_id: str | None
    ) -> None:
        event = "folder_moved" if record.is_folder else "file_moved"
        await self._manager.broadcast(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
                old_parent_folder_id=old_parent_folder_id,
            ),
        )

    async def item_deleted(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_deleted" if record.is_folder else "file_deleted"
        await self._manager.broadcast(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
            ),
        )

    async def item_restored(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_restored" if record.is_folder else "file_restored"
        await self._manager.broadcast(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
            ),
        )

    async def shared_item_changed(self, event: str, record: "FileRecord") -> None:
        """Like item_created/item_moved/etc. but for the shared folder —
        broadcasts to all connected users, not just SHARED_OWNER_ID's own
        (nonexistent) sockets."""
        await self._manager.broadcast_all(
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=record.owner_id,
            )
        )

    async def _broadcast_to_tree(self, owner_id: str, payload: dict) -> None:
        """Personal trees reach one user's sockets; the shared tree reaches
        everyone, because everyone is looking at it."""
        if owner_id == SHARED_OWNER_ID:
            await self._manager.broadcast_all(payload)
        else:
            await self._manager.broadcast(owner_id, payload)

    async def item_entered_tree(self, owner_id: str, record: FileRecord) -> None:
        """A cross-tree transfer looks like a create to the destination."""
        event = "folder_created" if record.is_folder else "file_created"
        await self._broadcast_to_tree(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=record.parent_folder_id,
                owner_id=owner_id,
            ),
        )

    async def item_left_tree(
        self, owner_id: str, record: FileRecord, parent_folder_id: str | None
    ) -> None:
        """...and like a delete to the source. The record's own
        parent_folder_id already points at the destination by the time
        this runs, so the old one is passed in."""
        event = "folder_deleted" if record.is_folder else "file_deleted"
        await self._broadcast_to_tree(
            owner_id,
            build_event(
                event,
                item_id=record.id,
                parent_folder_id=parent_folder_id,
                owner_id=owner_id,
            ),
        )
