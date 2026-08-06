"""
Storage abstraction — the server-side counterpart to the Flutter client's
`StorageService` interface (see lib/core/services/storage_service.dart).

This module deals with raw file *bytes* only. File-tree metadata (name,
parentFolderId, ownerId, timestamps) lives in the SQLite-backed `database`
module, not here — same "metadata and storage are separate" principle the
Flutter side already follows with Firestore vs. Firebase Storage.

A "storage key" is an opaque, forward-slash-separated relative path, e.g.
"users/abc123/1731000000000_sunset.jpg" — the same flat-key scheme
FirebaseStorageService already uses. Because storage keys don't encode
folder structure, moving an item between folders is a metadata-only
operation (update parentFolderId in SQLite); the bytes never move. Renaming
is the same — only the display name in metadata changes, the storage key
stays put. That means this interface never needs a "move" or "rename"
method, mirroring FirebaseStorageService exactly.
"""

from __future__ import annotations

import abc
from typing import AsyncIterator


class StorageError(Exception):
    """Base class for all storage-layer errors."""


class StorageNotFoundError(StorageError):
    """Raised when a requested storage key does not exist."""


class StoragePathError(StorageError):
    """Raised when a storage key is invalid or would escape the storage
    root (path traversal protection)."""


class StorageRepository(abc.ABC):
    """
    Abstract interface for binary file storage.

    Implementations are responsible for mapping an opaque storage key onto
    wherever bytes actually live (local disk today; S3/R2/etc. later).
    Swapping implementations should require zero changes to the routers
    or database module that call this interface.
    """

    @abc.abstractmethod
    async def save(self, storage_key: str, stream: AsyncIterator[bytes]) -> int:
        """
        Write `stream` to `storage_key`, creating any parent structure the
        implementation needs. Returns the number of bytes written.
        Overwrites if `storage_key` already exists.
        """

    @abc.abstractmethod
    async def read(self, storage_key: str) -> bytes:
        """
        Read the entire file at `storage_key` into memory. Suitable for
        small text/JSON/markdown previews; large files should use
        `open_range` instead.
        """

    @abc.abstractmethod
    def open_range(
        self, storage_key: str, start: int, end: int
    ) -> AsyncIterator[bytes]:
        """
        Stream bytes from `storage_key` over the inclusive range
        [start, end]. Used to satisfy HTTP Range requests for video
        scrubbing. Callers must clamp `end` to the file's actual size
        (via `size()`) before calling this — implementations assume the
        range is already valid.
        """

    @abc.abstractmethod
    async def delete(self, storage_key: str) -> None:
        """Delete the file at `storage_key`. No-op if it doesn't exist."""

    @abc.abstractmethod
    async def exists(self, storage_key: str) -> bool:
        """Whether a file currently exists at `storage_key`."""

    @abc.abstractmethod
    async def size(self, storage_key: str) -> int:
        """Size in bytes of the file at `storage_key`."""
