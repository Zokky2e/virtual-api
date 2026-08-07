"""
File-content operations — upload/download of file bytes plus the metadata
record that goes with them. Combines FileRepository (metadata) and
StorageRepository (bytes) behind operations a router can call in one
line, mirroring what UploadBloc/DownloadBloc do together with
FirestoreFileSystemRepository + FirebaseStorageService on the Flutter
side.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from pathlib import Path

from app.database.models import FileRecord, FileType
from app.database.repositories import FileRepository
from app.exceptions import InvalidOperationError, NotFoundError
from app.storage.base import StorageRepository


def type_from_mime(mime_type: str, filename: str | None = None) -> FileType:
    """Mirrors UploadBloc._typeFromMime in
    lib/features/file-system/bloc/upload_bloc.dart — keep both in sync by
    hand if either gains a new type."""
    if mime_type.startswith("image/"):
        return FileType.image
    if mime_type.startswith("video/"):
        return FileType.video
    if mime_type.startswith("audio/"):
        return FileType.audio
    if mime_type == "application/pdf":
        return FileType.pdf
    if mime_type == "application/json":
        return FileType.json
    if mime_type == "text/markdown":
        return FileType.markdown
    if mime_type in ("application/x-subrip", "text/vtt"):
        return FileType.subtitle
    if filename and Path(filename).suffix.lower() in (".srt", ".vtt"):
        return FileType.subtitle
    if mime_type.startswith("text/"):
        return FileType.text
    return FileType.other


class FileService:
    def __init__(self, repo: FileRepository, storage: StorageRepository):
        self._repo = repo
        self._storage = storage

    async def upload(
        self,
        *,
        owner_id: str,
        name: str,
        mime_type: str,
        parent_folder_id: str | None,
        stream: AsyncIterator[bytes],
    ) -> FileRecord:
        # Same flat-key scheme as FirebaseStorageService's caller
        # (UploadBloc): "users/{uid}/{timestamp}_{name}" — no folder
        # structure baked into the key, since parent_folder_id already
        # lives in metadata (see storage/base.py's docstring).
        storage_key = f"users/{owner_id}/{int(time.time() * 1000)}_{name}"
        size = await self._storage.save(storage_key, stream)
        return await self._repo.create_file(
            owner_id=owner_id,
            name=name,
            parent_folder_id=parent_folder_id,
            type_=type_from_mime(mime_type, name),
            storage_key=storage_key,
            size=size,
        )

    async def get_record(self, owner_id: str, item_id: str) -> FileRecord:
        """Fetch a file's metadata. Raises NotFoundError if it doesn't
        exist, belongs to another owner, or is actually a folder."""
        record = await self._repo.get_by_id(owner_id, item_id)
        if record is None or record.is_folder:
            raise NotFoundError(f"File not found: {item_id}")
        return record

    async def download(self, owner_id: str, item_id: str) -> tuple[FileRecord, bytes]:
        record = await self.get_record(owner_id, item_id)
        if record.storage_key is None:
            raise InvalidOperationError("File has no content to download.")
        data = await self._storage.read(record.storage_key)
        return record, data