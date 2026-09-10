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

from app.database.models import FileRecord, FileType, WallpaperRecord


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

    async def list_subtree(self, owner_id: str, root_id: str) -> list[FileRecord]:
        """`root_id` plus every non-deleted descendant, parents first.

        Used by a cross-tree move: the whole subtree changes owner, and
        the parents-first order lets a caller reassign them in sequence
        without a child ever briefly outliving its parent's ownership.
        """
        root = await self.get_by_id(owner_id, root_id)
        if root is None:
            return []
        ordered = [root]
        queue = [root]
        while queue:
            current = queue.pop(0)
            if not current.is_folder:
                continue
            children = await self.get_folder(owner_id, current.id)
            ordered.extend(children)
            queue.extend(c for c in children if c.is_folder)
        return ordered

    async def reassign_owner(
        self,
        owner_id: str,
        item_id: str,
        *,
        new_owner_id: str,
        new_storage_key: str | None = None,
    ) -> bool:
        """Hands one record to another owner, optionally repointing it at
        relocated bytes. The lookup is still scoped to the *current*
        owner, so this cannot be used to grab another owner's row."""
        record = await self.get_by_id(owner_id, item_id)
        if record is None:
            return False
        record.owner_id = new_owner_id
        if new_storage_key is not None:
            record.storage_key = new_storage_key
        return True

    async def list_storage_keys(self, owner_id: str) -> set[str]:
        """All storage_keys currently tracked for this owner — used by
        ReconcileService to figure out which on-disk files are untracked."""
        stmt = select(FileRecord.storage_key).where(
            FileRecord.owner_id == owner_id,
            FileRecord.storage_key.is_not(None),
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}


class WallpaperRepository:
    """
    Data access for WallpaperRecord — the server-side mirror of Flutter's
    WallpaperRepository interface (lib/core/repositories/
    wallpaper_repository.dart). Same owner-scoping discipline as
    FileRepository: every method takes owner_id and filters on it, so no
    method here can return another user's rows by construction.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_for_owner(self, owner_id: str) -> list[WallpaperRecord]:
        stmt = (
            select(WallpaperRecord)
            .where(WallpaperRecord.owner_id == owner_id)
            .order_by(WallpaperRecord.created_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self, owner_id: str, wallpaper_id: str
    ) -> WallpaperRecord | None:
        """None both when the id doesn't exist and when it belongs to
        another owner — same no-ownership-probing rule as FileRepository."""
        record = await self._session.get(WallpaperRecord, wallpaper_id)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    async def find_existing(
        self, owner_id: str, name: str, size: int
    ) -> WallpaperRecord | None:
        """Backs the client's dedupe check — re-picking the same image in
        the file picker should reuse the stored copy, not upload it again."""
        stmt = select(WallpaperRecord).where(
            WallpaperRecord.owner_id == owner_id,
            WallpaperRecord.name == name,
            WallpaperRecord.size == size,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create(
        self, *, owner_id: str, name: str, storage_key: str, size: int
    ) -> WallpaperRecord:
        record = WallpaperRecord(
            owner_id=owner_id, name=name, storage_key=storage_key, size=size
        )
        self._session.add(record)
        await self._session.flush()  # populate record.id for the caller
        return record

    async def set_active(
        self, owner_id: str, wallpaper_id: str
    ) -> WallpaperRecord | None:
        """Marks one wallpaper active and clears every other row for this
        owner in the same transaction, so "at most one is_set per owner"
        can't be broken by a partially applied update."""
        target = await self.get_by_id(owner_id, wallpaper_id)
        if target is None:
            return None
        for record in await self.list_for_owner(owner_id):
            record.is_set = record.id == wallpaper_id
        return target