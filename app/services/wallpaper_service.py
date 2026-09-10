"""
Wallpaper operations — upload/list/select plus range-streaming of the
image bytes. The wallpaper counterpart to FileService + StreamService,
kept separate from both because a wallpaper is not a file-tree item.

The distinction that matters is the storage prefix. Tree files live under
`users/{owner_id}/`, which ReconcileService walks on every
POST /desktop/shared/sync, creating a FileRecord for anything it finds
untracked. Wallpapers therefore live under `wallpapers/{owner_id}/`
instead — outside that subtree, so a sync can never turn a wallpaper into
a desktop icon.
"""

from __future__ import annotations

import mimetypes
import time
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

from app.database.models import WallpaperRecord
from app.database.repositories import WallpaperRepository
from app.exceptions import InvalidOperationError, NotFoundError
from app.storage.base import StorageRepository
from app.streaming.range import RangeParseError, parse_range_header
from app.streaming.response import build_stream_response

# Deliberately not "users/" — see the module docstring.
WALLPAPER_KEY_PREFIX = "wallpapers"


class WallpaperService:
    def __init__(self, repo: WallpaperRepository, storage: StorageRepository):
        self._repo = repo
        self._storage = storage

    async def upload(
        self,
        *,
        owner_id: str,
        name: str,
        stream: AsyncIterator[bytes],
    ) -> WallpaperRecord:
        """Writes the bytes and creates the metadata row together, so the
        client never has to make a second call to register what it just
        uploaded (same contract as FileService.upload)."""
        storage_key = f"{WALLPAPER_KEY_PREFIX}/{owner_id}/{int(time.time() * 1000)}_{name}"
        size = await self._storage.save(storage_key, stream)
        return await self._repo.create(
            owner_id=owner_id, name=name, storage_key=storage_key, size=size
        )

    async def list(self, owner_id: str) -> list[WallpaperRecord]:
        return await self._repo.list_for_owner(owner_id)

    async def get(self, owner_id: str, wallpaper_id: str) -> WallpaperRecord:
        record = await self._repo.get_by_id(owner_id, wallpaper_id)
        if record is None:
            raise NotFoundError(f"Wallpaper not found: {wallpaper_id}")
        return record

    async def find_existing(
        self, owner_id: str, name: str, size: int
    ) -> WallpaperRecord | None:
        return await self._repo.find_existing(owner_id, name, size)

    async def set_active(self, owner_id: str, wallpaper_id: str) -> WallpaperRecord:
        record = await self._repo.set_active(owner_id, wallpaper_id)
        if record is None:
            raise NotFoundError(f"Wallpaper not found: {wallpaper_id}")
        return record

    async def stream(
        self, owner_id: str, wallpaper_id: str, range_header: str | None
    ) -> StreamingResponse:
        record = await self.get(owner_id, wallpaper_id)
        file_size = await self._storage.size(record.storage_key)
        content_type = (
            mimetypes.guess_type(record.name)[0] or "application/octet-stream"
        )

        try:
            byte_range = parse_range_header(range_header, file_size)
        except RangeParseError as exc:
            raise InvalidOperationError(str(exc)) from exc

        return build_stream_response(
            storage=self._storage,
            storage_key=record.storage_key,
            file_size=file_size,
            content_type=content_type,
            byte_range=byte_range,
        )
