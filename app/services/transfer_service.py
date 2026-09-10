"""
Moving and copying items between the personal and shared trees.

Neither files.py nor shared.py is the right home for this: each is scoped
to a single owner by construction, and the whole point of a transfer is
that it touches two. So it gets its own service and its own router, the
same way shared.py exists rather than threading an "is this shared?"
branch through the generic routers.

Two things make this more than a metadata edit.

Storage keys are owner-prefixed (`users/{owner_id}/...`), so bytes have to
be relocated as well as re-recorded — see storage/base.py. Leaving them
under the old owner would make ReconcileService treat them as untracked
on the next sync and mint a duplicate record pointing at the same file.

And a folder is a subtree, so moving one re-owns every descendant, not
just the row that was dragged.

Doing this server-side rather than as download-then-upload on the client
is the point: these are the same multi-GB videos the Range-streaming
endpoints exist for, and the web client would have to hold one entirely
in memory as a Uint8List to round-trip it.
"""

from __future__ import annotations

import time

from app.database.models import FileRecord
from app.database.repositories import FileRepository
from app.exceptions import ConflictError, InvalidOperationError, NotFoundError
from app.storage.base import StorageRepository


def _storage_key_for(owner_id: str, name: str) -> str:
    """Same flat scheme FileService.upload uses."""
    return f"users/{owner_id}/{int(time.time() * 1000)}_{name}"


class TransferResult:
    """What the router needs in order to notify both trees: the record in
    its new home, plus where it used to live so the source tree can be
    told to drop it."""

    def __init__(
        self,
        *,
        record: FileRecord,
        source_parent_folder_id: str | None,
        removed_from_source: bool,
    ):
        self.record = record
        self.source_parent_folder_id = source_parent_folder_id
        self.removed_from_source = removed_from_source


class TransferService:
    def __init__(self, repo: FileRepository, storage: StorageRepository):
        self._repo = repo
        self._storage = storage

    async def transfer(
        self,
        *,
        item_id: str,
        source_owner_id: str,
        destination_owner_id: str,
        destination_parent_folder_id: str | None,
        move: bool,
        name: str | None = None,
    ) -> TransferResult:
        if source_owner_id == destination_owner_id:
            raise InvalidOperationError(
                "Source and destination are the same tree — use the ordinary "
                "move or copy instead."
            )

        record = await self._repo.get_by_id(source_owner_id, item_id)
        if record is None:
            raise NotFoundError(f"Item not found: {item_id}")

        if record.is_folder and not move:
            # The same limitation the within-tree paste already has, kept
            # deliberately identical so "copy" means one thing everywhere.
            raise InvalidOperationError(
                "Copying folders isn't supported yet — only files."
            )

        # A move keeps its own name; only a copy may be renamed.
        effective_name = record.name if move else (name or record.name)
        await self._validate_destination(
            destination_owner_id, destination_parent_folder_id, effective_name
        )

        source_parent_folder_id = record.parent_folder_id

        if move:
            moved = await self._move(
                record=record,
                source_owner_id=source_owner_id,
                destination_owner_id=destination_owner_id,
                destination_parent_folder_id=destination_parent_folder_id,
            )
            return TransferResult(
                record=moved,
                source_parent_folder_id=source_parent_folder_id,
                removed_from_source=True,
            )

        copied = await self._copy_file(
            record=record,
            destination_owner_id=destination_owner_id,
            destination_parent_folder_id=destination_parent_folder_id,
            name=effective_name,
        )
        return TransferResult(
            record=copied,
            source_parent_folder_id=source_parent_folder_id,
            removed_from_source=False,
        )

    async def _validate_destination(
        self, destination_owner_id: str, parent_folder_id: str | None, name: str
    ) -> None:
        if parent_folder_id is not None:
            destination = await self._repo.get_by_id(
                destination_owner_id, parent_folder_id
            )
            if destination is None:
                raise NotFoundError(
                    f"Destination folder not found: {parent_folder_id}"
                )
            if not destination.is_folder:
                raise InvalidOperationError("Destination is not a folder.")

        if await self._repo.name_exists_in_folder(
            destination_owner_id, parent_folder_id, name
        ):
            raise ConflictError(
                f'"{name}" already exists in the destination folder.'
            )

    async def _copy_file(
        self,
        *,
        record: FileRecord,
        destination_owner_id: str,
        destination_parent_folder_id: str | None,
        name: str,
    ) -> FileRecord:
        if record.storage_key is None:
            raise InvalidOperationError("File has no content to copy.")

        destination_key = _storage_key_for(destination_owner_id, name)
        size = await self._storage.copy(record.storage_key, destination_key)

        return await self._repo.create_file(
            owner_id=destination_owner_id,
            name=name,
            parent_folder_id=destination_parent_folder_id,
            type_=record.type,
            storage_key=destination_key,
            size=size,
        )

    async def _move(
        self,
        *,
        record: FileRecord,
        source_owner_id: str,
        destination_owner_id: str,
        destination_parent_folder_id: str | None,
    ) -> FileRecord:
        # Collected up front: once the root has changed hands, looking its
        # children up under the old owner would stop working.
        subtree = await self._repo.list_subtree(source_owner_id, record.id)

        for item in subtree:
            new_key: str | None = None
            if item.storage_key is not None:
                old_key = item.storage_key
                new_key = _storage_key_for(destination_owner_id, item.name)
                await self._storage.copy(old_key, new_key)
                await self._storage.delete(old_key)

            await self._repo.reassign_owner(
                source_owner_id,
                item.id,
                new_owner_id=destination_owner_id,
                new_storage_key=new_key,
            )

        # Re-parenting happens last, under the new owner — by now the row
        # belongs to the destination tree.
        await self._repo.move(
            destination_owner_id, record.id, destination_parent_folder_id
        )
        moved = await self._repo.get_by_id(destination_owner_id, record.id)
        if moved is None:  # pragma: no cover — the row was just written
            raise NotFoundError(f"Item not found after transfer: {record.id}")
        return moved
