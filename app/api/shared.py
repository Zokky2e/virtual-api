# app/api/shared.py
"""
Endpoints for the shared folder — a single tree, owned by the reserved
SHARED_OWNER_ID, visible and writable by every authenticated user. Files
dropped directly on the server under storage_root/users/shared/ (via scp,
rsync, browser download, etc.) show up here after a /desktop/shared/sync
call, exactly like ReconcileService already does for per-user folders.

Deliberately does NOT reuse the /desktop/file/{id} or /desktop/move
routes from files.py — those resolve ownership from the caller's own
uid, and a shared item's owner_id is always SHARED_OWNER_ID, not the
caller's. Keeping this as its own small router avoids threading an
"is this a shared id?" branch through every generic file endpoint.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from app.api.dependencies import (
    get_file_service,
    get_folder_service,
    get_notification_service,
    get_reconcile_service,
    get_stream_service,
)
from app.auth.dependencies import get_current_user, get_current_user_header_or_query
from app.auth.models import AuthUser
from app.constants import SHARED_OWNER_ID
from app.schemas.file import FileResponse
from app.schemas.folder import FolderCreateRequest, MoveRequest, RenameRequest
from app.services.file_service import FileService
from app.services.folder_service import FolderService
from app.services.notification_service import NotificationService
from app.services.reconcile_service import ReconcileService
from app.services.stream_service import StreamService

router = APIRouter(prefix="/shared", tags=["shared"])

_UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.get("", response_model=list[FileResponse])
async def list_shared_root(
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    items = await folders.list_folder(SHARED_OWNER_ID, None)
    return [FileResponse.model_validate(i) for i in items]


@router.get("/folder/{folder_id}", response_model=list[FileResponse])
async def list_shared_folder(
    folder_id: str,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    await folders.get_item(SHARED_OWNER_ID, folder_id)
    items = await folders.list_folder(SHARED_OWNER_ID, folder_id)
    return [FileResponse.model_validate(i) for i in items]


@router.get("/file/{item_id}", response_model=FileResponse)
async def get_shared_item(
    item_id: str,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> FileResponse:
    """Mirrors files.py's get_item — needed because
    ApiFileSystemRepository.createFile() re-fetches an item's metadata by
    id right after upload (see that method's docstring). Without this,
    uploading into the shared tree reports a spurious failure even though
    the file and its record are created correctly."""
    record = await folders.get_item(SHARED_OWNER_ID, item_id)
    return FileResponse.model_validate(record)

@router.post("/folder", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def create_shared_folder(
    body: FolderCreateRequest,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    record = await folders.create_folder(
        owner_id=SHARED_OWNER_ID, name=body.name, parent_folder_id=body.parent_folder_id
    )
    await notifications.shared_item_changed("folder_created", record)
    return FileResponse.model_validate(record)


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_shared_file(
    file: UploadFile,
    parent_folder_id: str | None = None,
    _: AuthUser = Depends(get_current_user),
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
        owner_id=SHARED_OWNER_ID,
        name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        parent_folder_id=parent_folder_id,
        stream=chunks(),
    )
    await notifications.shared_item_changed("file_created", record)
    return FileResponse.model_validate(record)


@router.get("/download/{item_id}")
async def download_shared_file(
    item_id: str,
    _: AuthUser = Depends(get_current_user),
    files: FileService = Depends(get_file_service),
) -> Response:
    record, data = await files.download(SHARED_OWNER_ID, item_id)
    content_type = mimetypes.guess_type(record.name)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{record.name}"'},
    )


@router.get("/stream/{item_id}")
async def stream_shared_file(
    item_id: str,
    request: Request,
    _: AuthUser = Depends(get_current_user_header_or_query),
    stream_service: StreamService = Depends(get_stream_service),
) -> StreamingResponse:
    range_header = request.headers.get("range")
    return await stream_service.stream(SHARED_OWNER_ID, item_id, range_header)


@router.patch("/file/{item_id}", response_model=FileResponse)
async def rename_shared_item(
    item_id: str,
    body: RenameRequest,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    record = await folders.rename(SHARED_OWNER_ID, item_id, body.name)
    await notifications.shared_item_changed(
        "folder_renamed" if record.is_folder else "file_renamed", record
    )
    return FileResponse.model_validate(record)


@router.patch("/move", response_model=FileResponse)
async def move_shared_item(
    body: MoveRequest,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    record = await folders.move(SHARED_OWNER_ID, body.item_id, body.parent_folder_id)
    await notifications.shared_item_changed(
        "folder_moved" if record.is_folder else "file_moved", record
    )
    return FileResponse.model_validate(record)


@router.delete("/file/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shared_item(
    item_id: str,
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> None:
    record = await folders.get_item(SHARED_OWNER_ID, item_id)
    await folders.soft_delete(SHARED_OWNER_ID, item_id)
    await notifications.shared_item_changed(
        "folder_deleted" if record.is_folder else "file_deleted", record
    )


@router.get("/search", response_model=list[FileResponse])
async def search_shared(
    q: str = Query(min_length=1),
    _: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    items = await folders.search(SHARED_OWNER_ID, q)
    return [FileResponse.model_validate(i) for i in items]

@router.post("/sync", response_model=list[FileResponse])
async def sync_shared_storage(
    _: AuthUser = Depends(get_current_user),
    reconcile: ReconcileService = Depends(get_reconcile_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> list[FileResponse]:
    """Same reconciliation as the per-user /desktop/sync, but scanning
    storage_root/users/shared/ — pick up anything scp'd or downloaded
    directly on the server into the shared drop zone."""
    records = await reconcile.reconcile_user(SHARED_OWNER_ID)
    for record in records:
        await notifications.shared_item_changed(
			"folder_created" if record.is_folder else "file_created", record
		)
    return [FileResponse.model_validate(r) for r in records]