"""
Folder / tree operations — create, list, rename, move, soft-delete,
restore, hard-delete, search. Applies uniformly to files and folders
(both are FileRecord rows), mirroring FirestoreFileSystemRepository on
the Flutter side. Only hard_delete touches StorageRepository directly —
everything else here is metadata-only, same "storage vs. metadata"
separation as the client.
"""

from __future__ import annotations
from pathlib import Path

from app.database.models import FileRecord
from app.database.repositories import FileRepository
from app.exceptions import ConflictError, InvalidOperationError, NotFoundError
from app.storage.base import StorageRepository


class FolderService:
    def __init__(self, repo: FileRepository, storage: StorageRepository):
        self._repo = repo
        self._storage = storage

    async def create_folder(
        self, *, owner_id: str, name: str, parent_folder_id: str | None
    ) -> FileRecord:
        if await self._repo.name_exists_in_folder(owner_id, parent_folder_id, name):
            raise ConflictError(f'"{name}" already exists in this folder.')
        return await self._repo.create_folder(
            owner_id=owner_id, name=name, parent_folder_id=parent_folder_id
        )

    async def list_folder(
        self, owner_id: str, folder_id: str | None
    ) -> list[FileRecord]:
        return await self._repo.get_folder(owner_id, folder_id)

    async def list_deleted(self, owner_id: str) -> list[FileRecord]:
        return await self._repo.watch_deleted(owner_id)

    async def search(self, owner_id: str, query: str) -> list[FileRecord]:
        return await self._repo.search(owner_id, query)

    async def get_item(self, owner_id: str, item_id: str) -> FileRecord:
        record = await self._repo.get_by_id(owner_id, item_id)
        if record is None:
            raise NotFoundError(f"Item not found: {item_id}")
        return record

    async def rename(self, owner_id: str, item_id: str, new_name: str) -> FileRecord:
        record = await self.get_item(owner_id, item_id)
        # Preserve extension if none was provided
        if "." not in Path(new_name).name:
            extension = "".join(Path(record.name).suffixes)
            new_name = f"{new_name}{extension}"
        if new_name != record.name and await self._repo.name_exists_in_folder(
            owner_id, record.parent_folder_id, new_name
        ):
            raise ConflictError(f'"{new_name}" already exists in this folder.')
        await self._repo.rename(owner_id, item_id, new_name)
        return await self.get_item(owner_id, item_id)

    async def move(
        self, owner_id: str, item_id: str, new_parent_folder_id: str | None
    ) -> FileRecord:
        record = await self.get_item(owner_id, item_id)

        if new_parent_folder_id is not None:
            destination = await self.get_item(owner_id, new_parent_folder_id)
            if not destination.is_folder:
                raise InvalidOperationError("Destination is not a folder.")

            if record.is_folder and await self._repo.is_descendant(
                owner_id, item_id, new_parent_folder_id
            ):
                # The guard the Flutter drag-and-drop UI is currently
                # missing on the client side. Enforced here so a bad move
                # can never succeed even if that client gap never gets
                # patched.
                raise InvalidOperationError(
                    "Cannot move a folder into itself or one of its own subfolders."
                )

        if new_parent_folder_id != record.parent_folder_id and await (
            self._repo.name_exists_in_folder(owner_id, new_parent_folder_id, record.name)
        ):
            raise ConflictError(
                f'"{record.name}" already exists in the destination folder.'
            )

        await self._repo.move(owner_id, item_id, new_parent_folder_id)
        return await self.get_item(owner_id, item_id)

    async def soft_delete(self, owner_id: str, item_id: str) -> None:
        if not await self._repo.soft_delete(owner_id, item_id):
            raise NotFoundError(f"Item not found: {item_id}")

    async def restore(self, owner_id: str, item_id: str) -> None:
        if not await self._repo.restore(owner_id, item_id):
            raise NotFoundError(f"Item not found: {item_id}")

    async def hard_delete(self, owner_id: str, item_id: str) -> None:
        """
        Permanently deletes one item. If it's a file, its storage bytes
        are deleted too. If it's a folder, the DB cascade on
        FileRecord.children (cascade="all, delete-orphan") removes
        descendant rows, but this does NOT walk the subtree to clean up
        their storage bytes — recursively purging a deleted folder's
        files is a known gap, same limitation the current Flutter
        recycle bin has (it only ever hard-deletes items one at a time
        from a flat list). Worth fixing together later, not a regression
        introduced here.
        """
        record = await self.get_item(owner_id, item_id)
        if record.storage_key is not None:
            await self._storage.delete(record.storage_key)
        await self._repo.hard_delete(owner_id, item_id)