"""
Folder / tree-browsing endpoints. Item mutations that apply to both files
and folders (rename, move, soft-delete) live in api/files.py instead —
see that module's docstring for why.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.api.dependencies import get_folder_service
from app.schemas.file import FileResponse
from app.schemas.folder import FolderCreateRequest
from app.services.folder_service import FolderService

router = APIRouter(tags=["folders"])


@router.get("", response_model=list[FileResponse])
async def list_root(
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    items = await folders.list_folder(user.uid, None)
    return [FileResponse.model_validate(i) for i in items]


@router.get("/folder/{folder_id}", response_model=list[FileResponse])
async def list_folder(
    folder_id: str,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    # get_item raises NotFoundError (-> 404) if folder_id doesn't exist or
    # isn't owned by this user — same "existence and ownership look
    # identical from outside" behavior as the rest of the API.
    await folders.get_item(user.uid, folder_id)
    items = await folders.list_folder(user.uid, folder_id)
    return [FileResponse.model_validate(i) for i in items]


@router.post("/folder", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreateRequest,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> FileResponse:
    record = await folders.create_folder(
        owner_id=user.uid, name=body.name, parent_folder_id=body.parent_folder_id
    )
    return FileResponse.model_validate(record)


@router.get("/search", response_model=list[FileResponse])
async def search(
    q: str = Query(min_length=1),
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    items = await folders.search(user.uid, q)
    return [FileResponse.model_validate(i) for i in items]


@router.get("/recycle-bin", response_model=list[FileResponse])
async def list_recycle_bin(
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> list[FileResponse]:
    items = await folders.list_deleted(user.uid)
    return [FileResponse.model_validate(i) for i in items]


@router.post("/recycle-bin/{item_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_item(
    item_id: str,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> None:
    await folders.restore(user.uid, item_id)


@router.delete("/recycle-bin/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def purge_item(
    item_id: str,
    user: AuthUser = Depends(get_current_user),
    folders: FolderService = Depends(get_folder_service),
) -> None:
    """Permanent delete — the "Delete Forever" action, not the regular
    soft-delete (see api/files.py's DELETE /file/{id})."""
    await folders.hard_delete(user.uid, item_id)