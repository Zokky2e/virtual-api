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
            build_event(event, item_id=record.id, parent_folder_id=record.parent_folder_id),
        )

    async def item_renamed(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_renamed" if record.is_folder else "file_renamed"
        await self._manager.broadcast(
            owner_id,
            build_event(event, item_id=record.id, parent_folder_id=record.parent_folder_id),
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
                old_parent_folder_id=old_parent_folder_id,
            ),
        )

    async def item_deleted(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_deleted" if record.is_folder else "file_deleted"
        await self._manager.broadcast(
            owner_id,
            build_event(event, item_id=record.id, parent_folder_id=record.parent_folder_id),
        )

    async def item_restored(self, owner_id: str, record: FileRecord) -> None:
        event = "folder_restored" if record.is_folder else "file_restored"
        await self._manager.broadcast(
            owner_id,
            build_event(event, item_id=record.id, parent_folder_id=record.parent_folder_id),
        )