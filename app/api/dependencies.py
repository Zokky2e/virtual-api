"""
Dependency providers wiring together the layers for the API routers:
DB session -> FileRepository -> {FileService, FolderService}, plus a
process-wide StorageRepository singleton. Routers depend on
get_file_service / get_folder_service and never construct these
themselves — this is the one place that knows how the layers fit
together.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.database import get_session
from app.database.repositories import FileRepository
from app.services.file_service import FileService
from app.services.folder_service import FolderService
from app.storage.base import StorageRepository
from app.storage.local_storage import LocalFileStorage


@lru_cache
def get_storage() -> StorageRepository:
    """
    Process-wide storage singleton. Swapping to an S3/R2 implementation
    later is a one-line change here — nothing downstream (services,
    routers) needs to know or change.
    """
    return LocalFileStorage(get_settings().storage_root)


def get_file_repository(
    session: AsyncSession = Depends(get_session),
) -> FileRepository:
    return FileRepository(session)


def get_file_service(
    repo: FileRepository = Depends(get_file_repository),
    storage: StorageRepository = Depends(get_storage),
) -> FileService:
    return FileService(repo, storage)


def get_folder_service(
    repo: FileRepository = Depends(get_file_repository),
    storage: StorageRepository = Depends(get_storage),
) -> FolderService:
    return FolderService(repo, storage)