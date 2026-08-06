"""
File content endpoints (upload/download) plus the generic item
operations — rename, move, soft-delete — that apply to files and folders
alike, since both are FileRecord rows. These live here rather than
folders.py to match the REST shape in Virtual_Desktop_Server_Architecture.md
(PATCH /desktop/file/{id}, PATCH /desktop/move, DELETE /desktop/file/{id}),
even though "file" in those paths really means "item".
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, Response, UploadFile, status

from app.api.dependencies import (
    get_file_service,
    get_folder_service,
    get_notification_service,
)
from app.auth.dependencies import get_current_user_header_or_query, get_current_user
from app.auth.models import AuthUser
from app.schemas.file import FileResponse
from app.schemas.folder import MoveRequest, RenameRequest
from app.services.file_service import FileService
from app.services.folder_service import FolderService
from app.services.notification_service import NotificationService

router = APIRouter(tags=["files"])

# Chunk size for re-streaming an UploadFile into StorageRepository.save(),
# which expects an async byte-chunk iterator rather than the UploadFile
# object itself — keeps the storage layer decoupled from FastAPI's types.
_UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile,
    parent_folder_id: str | None = None,
    user: AuthUser = Depends(get_current_user),
    files: FileService = Depends(get_file_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    async def chunks():
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    record = await files.upload(
        owner_id=user.uid,
        name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        parent_folder_id=parent_folder_id,
        stream=chunks(),
    )
    await notifications.item_created(user.uid, record)
    return FileResponse.model_validate(record)


@router.get("/file/{item_id}", response_model=FileResponse)
async def get_item(
    item_id: str,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> FileResponse:
    record = await folders.get_item(user.uid, item_id)
    return FileResponse.model_validate(record)


@router.get("/download/{item_id}")
async def download_file(
    item_id: str,
    user: AuthUser = Depends(get_current_user_header_or_query),
    files: FileService = Depends(get_file_service),
) -> Response:
    record, data = await files.download(user.uid, item_id)
    content_type = mimetypes.guess_type(record.name)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{record.name}"'},
    )


@router.patch("/file/{item_id}", response_model=FileResponse)
async def rename_item(
    item_id: str,
    body: RenameRequest,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    record = await folders.rename(user.uid, item_id, body.name)
    await notifications.item_renamed(user.uid, record)
    return FileResponse.model_validate(record)


@router.patch("/move", response_model=FileResponse)
async def move_item(
    body: MoveRequest,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    # Captured before the move so the notification can tell clients which
    # folder to remove the item from, not just which one to add it to.
    previous = await folders.get_item(user.uid, body.item_id)
    old_parent_folder_id = previous.parent_folder_id

    record = await folders.move(user.uid, body.item_id, body.parent_folder_id)
    await notifications.item_moved(user.uid, record, old_parent_folder_id)
    return FileResponse.model_validate(record)


@router.delete("/file/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: str,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> None:
    """Soft delete — moves the item to the recycle bin. Permanent delete
    is DELETE /recycle-bin/{id} in api/folders.py."""
    record = await folders.get_item(user.uid, item_id)
    await folders.soft_delete(user.uid, item_id)
    await notifications.item_deleted(user.uid, record)