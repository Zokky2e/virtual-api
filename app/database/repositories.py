"""
Data access layer for file-tree metadata. This is the server-side mirror
of FirestoreFileSystemRepository (lib/core/providers/firebase/
firestore_file_system_repository.dart) — same method shapes, same
owner-scoping on every query, same soft-delete semantics. The `services`
layer calls this; routers never touch FileRecord/SQLAlchemy directly.

Every method takes owner_id explicitly and filters on it — there is no
method here that can return another user's data by construction.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FileRecord, FileType


class FileRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_folder(
        self, owner_id: str, folder_id: str | None
    ) -> list[FileRecord]:
        """Direct children of `folder_id` (None = root), excluding deleted."""
        stmt = select(FileRecord).where(
            FileRecord.owner_id == owner_id,
            FileRecord.is_deleted.is_(False),
            FileRecord.parent_folder_id == folder_id
            if folder_id is not None
            else FileRecord.parent_folder_id.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, owner_id: str, item_id: str) -> FileRecord | None:
        """Returns None both when the id doesn't exist and when it belongs
        to a different owner — callers can't distinguish the two, which is
        the point (no ownership probing via error messages)."""
        record = await self._session.get(FileRecord, item_id)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    async def create_folder(
        self, *, owner_id: str, name: str, parent_folder_id: str | None
    ) -> FileRecord:
        return await self._create(
            owner_id=owner_id,
            name=name,
            parent_folder_id=parent_folder_id,
            type_=FileType.folder,
            storage_key=None,
            size=0,
        )

    async def create_file(
        self,
        *,
        owner_id: str,
        name: str,
        parent_folder_id: str | None,
        type_: FileType,
        storage_key: str,
        size: int,
    ) -> FileRecord:
        return await self._create(
            owner_id=owner_id,
            name=name,
            parent_folder_id=parent_folder_id,
            type_=type_,
            storage_key=storage_key,
            size=size,
        )

    async def _create(
        self,
        *,
        owner_id: str,
        name: str,
        parent_folder_id: str | None,
        type_: FileType,
        storage_key: str | None,
        size: int,
    ) -> FileRecord:
        record = FileRecord(
            owner_id=owner_id,
            name=name,
            parent_folder_id=parent_folder_id,
            type=type_,
            storage_key=storage_key,
            size=size,
        )
        self._session.add(record)
        await self._session.flush()  # populate record.id for the caller
        return record

    async def rename(self, owner_id: str, item_id: str, new_name: str) -> bool:
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        record.name = new_name
        return True

    async def move(
        self, owner_id: str, item_id: str, new_parent_folder_id: str | None
    ) -> bool:
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        record.parent_folder_id = new_parent_folder_id
        return True

    async def soft_delete(self, owner_id: str, item_id: str) -> bool:
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        record.is_deleted = True
        return True

    async def restore(self, owner_id: str, item_id: str) -> bool:
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        record.is_deleted = False
        return True

    async def hard_delete(self, owner_id: str, item_id: str) -> bool:
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        await self._session.delete(record)
        # Flush now rather than waiting for commit — otherwise a
        # subsequent get_by_id() in the same session/request would still
        # find the row via the identity map even though it's pending
        # deletion.
        await self._session.flush()
        return True

    async def watch_deleted(self, owner_id: str) -> list[FileRecord]:
        stmt = select(FileRecord).where(
            FileRecord.owner_id == owner_id, FileRecord.is_deleted.is_(True)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, owner_id: str, query: str) -> list[FileRecord]:
        stmt = select(FileRecord).where(
            FileRecord.owner_id == owner_id,
            FileRecord.is_deleted.is_(False),
            FileRecord.name.ilike(f"%{query}%"),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def name_exists_in_folder(
        self, owner_id: str, parent_folder_id: str | None, name: str
    ) -> bool:
        stmt = select(FileRecord.id).where(
            FileRecord.owner_id == owner_id,
            FileRecord.is_deleted.is_(False),
            FileRecord.name == name,
            FileRecord.parent_folder_id == parent_folder_id
            if parent_folder_id is not None
            else FileRecord.parent_folder_id.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def is_descendant(self, owner_id: str, ancestor_id: str, item_id: str) -> bool:
        """
        True if `item_id` is `ancestor_id` itself, or lives anywhere under
        it. Use this before a move to reject "drag a folder into its own
        subfolder" — the one drag-and-drop gap flagged from the Flutter
        side that a server-side move endpoint must not reintroduce.
        """
        current: str | None = item_id
        seen: set[str] = set()
        while current is not None:
            if current == ancestor_id:
                return True
            if current in seen:
                return False  # corrupt cycle guard — bail rather than loop forever
            seen.add(current)
            record = await self._session.get(FileRecord, current)
            if record is None:
                return False
            current = record.parent_folder_id
        return False