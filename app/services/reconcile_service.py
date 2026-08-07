# app/services/reconcile_service.py
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

        # Relative dir path ("" == root) -> folder_id. Seeded with root.
        folder_ids: dict[str, str | None] = {"": None}

        async def ensure_folder(rel_dir: Path) -> str | None:
            rel_str = "" if str(rel_dir) == "." else rel_dir.as_posix()
            if rel_str in folder_ids:
                return folder_ids[rel_str]

            parent_id = await ensure_folder(rel_dir.parent)

            # Reuse an existing folder of the same name under this parent
            # instead of creating a duplicate on every sync run.
            siblings = await self._repo.get_folder(owner_id, parent_id)
            match = next(
                (r for r in siblings if r.is_folder and r.name == rel_dir.name),
                None,
            )
            if match is not None:
                folder_ids[rel_str] = match.id
                return match.id

            record = await self._repo.create_folder(
                owner_id=owner_id, name=rel_dir.name, parent_folder_id=parent_id
            )
            created.append(record)
            folder_ids[rel_str] = record.id
            return record.id

        for path in sorted(user_dir.rglob("*")):
            if not path.is_file():
                continue
            storage_key = path.relative_to(self._storage_root).as_posix()
            if storage_key in existing_keys:
                continue

            rel_dir = path.parent.relative_to(user_dir)
            parent_folder_id = await ensure_folder(rel_dir)

            mime_type, _ = mimetypes.guess_type(path.name)
            record = await self._repo.create_file(
                owner_id=owner_id,
                name=path.name,
                parent_folder_id=parent_folder_id,
                type_=type_from_mime(mime_type or "application/octet-stream", path.name),
                storage_key=storage_key,
                size=path.stat().st_size,
            )
            created.append(record)

        return created