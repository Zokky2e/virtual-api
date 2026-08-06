"""
LocalFileStorage — stores file bytes on the local filesystem, rooted at a
single confined directory (e.g. /srv/virtual-desktop/). This is the
server-side equivalent of FirebaseStorageService: same interface, disk
instead of a cloud bucket. When the self-hosted feature outgrows a single
Ubuntu laptop, an S3FileStorage/R2FileStorage can implement StorageRepository
without touching anything upstream of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import aiofiles
import aiofiles.os

from .base import StorageNotFoundError, StoragePathError, StorageRepository

_CHUNK_SIZE = 1024 * 1024  # 1 MiB per read/write chunk


class LocalFileStorage(StorageRepository):
    def __init__(self, root_dir: str | Path):
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        """
        Turn an opaque storage key into an absolute path under the root,
        rejecting anything that could escape it. This is the one place
        path-traversal protection lives — every other method routes
        through here first.
        """
        if not storage_key or storage_key.startswith("/") or "\\" in storage_key:
            raise StoragePathError(f"Invalid storage key: {storage_key!r}")

        candidate = (self._root / storage_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise StoragePathError(
                f"Storage key escapes storage root: {storage_key!r}"
            ) from None
        return candidate

    async def save(self, storage_key: str, stream: AsyncIterator[bytes]) -> int:
        path = self._resolve(storage_key)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        total = 0
        async with aiofiles.open(path, "wb") as f:
            async for chunk in stream:
                await f.write(chunk)
                total += len(chunk)
        return total

    async def read(self, storage_key: str) -> bytes:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise StorageNotFoundError(f"Not found: {storage_key!r}")
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def open_range(
        self, storage_key: str, start: int, end: int
    ) -> AsyncIterator[bytes]:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise StorageNotFoundError(f"Not found: {storage_key!r}")

        async with aiofiles.open(path, "rb") as f:
            await f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = await f.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    async def delete(self, storage_key: str) -> None:
        path = self._resolve(storage_key)
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            pass  # already gone — deleting is idempotent, not an error

    async def exists(self, storage_key: str) -> bool:
        return self._resolve(storage_key).is_file()

    async def size(self, storage_key: str) -> int:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise StorageNotFoundError(f"Not found: {storage_key!r}")
        stat_result = await aiofiles.os.stat(path)
        return stat_result.st_size
