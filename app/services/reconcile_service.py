# app/services/reconcile_service.py
"""
Reconciliation service — scans the physical storage root for files that
exist on disk but have no corresponding FileRecord (e.g. dropped in via
scp, rsync, or a browser download directly on the Ubuntu server) and
creates metadata records for them so they show up in Virtual Desktop.

Storage layout convention: files live flat under
storage_root/users/{owner_id}/... (see storage/base.py's docstring — the
on-disk layout was never meant to mirror the desktop's folder tree).
Anything found there without a matching storage_key in the database is
imported into that user's root folder (parent_folder_id=None); move it
into a subfolder afterward like any other item.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from app.database.models import FileRecord
from app.database.repositories import FileRepository
from app.services.file_service import type_from_mime


class ReconcileService:
    def __init__(self, repo: FileRepository, storage_root: Path):
        self._repo = repo
        self._storage_root = Path(storage_root)

    async def reconcile_user(self, owner_id: str) -> list[FileRecord]:
        user_dir = self._storage_root / "users" / owner_id
        if not user_dir.is_dir():
            return []

        existing_keys = await self._repo.list_storage_keys(owner_id)
        created: list[FileRecord] = []

        for path in sorted(user_dir.rglob("*")):
            if not path.is_file():
                continue
            storage_key = path.relative_to(self._storage_root).as_posix()
            if storage_key in existing_keys:
                continue

            mime_type, _ = mimetypes.guess_type(path.name)
            record = await self._repo.create_file(
                owner_id=owner_id,
                name=path.name,
                parent_folder_id=None,
                type_=type_from_mime(mime_type or "application/octet-stream"),
                storage_key=storage_key,
                size=path.stat().st_size,
            )
            created.append(record)

        return created