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
from app.services.notification_service import NotificationService
from app.services.stream_service import StreamService
from app.storage.base import StorageRepository
from app.storage.local_storage import LocalFileStorage
from app.websocket.manager import manager as connection_manager


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


def get_stream_service(
    repo: FileRepository = Depends(get_file_repository),
    storage: StorageRepository = Depends(get_storage),
) -> StreamService:
    return StreamService(repo, storage)


def get_notification_service() -> NotificationService:
    # connection_manager is a process-wide singleton (see
    # websocket/manager.py) — every request shares the same one, unlike
    # get_file_repository which is fresh per-request.
    return NotificationService(connection_manager)