"""
Wallpaper endpoints, mounted at /desktop/wallpapers.

Separate from files.py on purpose. Wallpapers used to have no routes at
all, so the Flutter web client uploaded them through the ordinary
personal-tree upload endpoint — every wallpaper became a real file-tree
record at the root of the user's desktop, under a mangled
`{timestamp}_{name}` name. These routes give wallpapers their own table
and their own storage prefix so that can't happen (see
services/wallpaper_service.py).

Note the route order: /stream/{id} is declared before /{id} so the
literal segment wins, and neither collides with the collection routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile, status

from app.api.dependencies import get_wallpaper_service
from app.auth.dependencies import get_current_user, get_current_user_header_or_query
from app.auth.models import AuthUser
from app.schemas.wallpaper import WallpaperResponse
from app.services.wallpaper_service import WallpaperService

router = APIRouter(tags=["wallpapers"])

# Matches files.py's chunk size — re-streams an UploadFile into
# StorageRepository.save(), which wants an async byte-chunk iterator
# rather than FastAPI's UploadFile type.
_UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.get("", response_model=list[WallpaperResponse])
async def list_wallpapers(
    user: AuthUser = Depends(get_current_user),
    wallpapers: WallpaperService = Depends(get_wallpaper_service),
) -> list[WallpaperResponse]:
    records = await wallpapers.list(user.uid)
    return [WallpaperResponse.model_validate(r) for r in records]


@router.post(
    "/upload", response_model=WallpaperResponse, status_code=status.HTTP_201_CREATED
)
async def upload_wallpaper(
    file: UploadFile,
    user: AuthUser = Depends(get_current_user),
    wallpapers: WallpaperService = Depends(get_wallpaper_service),
) -> WallpaperResponse:
    async def chunks():
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    record = await wallpapers.upload(
        owner_id=user.uid,
        name=file.filename or "wallpaper",
        stream=chunks(),
    )
    # No notification: wallpapers aren't part of the file tree, so no
    # folder watcher needs to refetch, and pushing one would make every
    # open desktop re-list for a change it can't see.
    return WallpaperResponse.model_validate(record)


@router.get("/stream/{wallpaper_id}")
async def stream_wallpaper(
    wallpaper_id: str,
    request: Request,
    # Token-in-query like /desktop/stream/{id}: Image.network can't attach
    # an Authorization header.
    user: AuthUser = Depends(get_current_user_header_or_query),
    wallpapers: WallpaperService = Depends(get_wallpaper_service),
):
    range_header = request.headers.get("range")
    return await wallpapers.stream(user.uid, wallpaper_id, range_header)


@router.get("/{wallpaper_id}", response_model=WallpaperResponse)
async def get_wallpaper(
    wallpaper_id: str,
    user: AuthUser = Depends(get_current_user),
    wallpapers: WallpaperService = Depends(get_wallpaper_service),
) -> WallpaperResponse:
    record = await wallpapers.get(user.uid, wallpaper_id)
    return WallpaperResponse.model_validate(record)


@router.patch("/{wallpaper_id}/active", response_model=WallpaperResponse)
async def set_active_wallpaper(
    wallpaper_id: str,
    user: AuthUser = Depends(get_current_user),
    wallpapers: WallpaperService = Depends(get_wallpaper_service),
) -> WallpaperResponse:
    """Applies this wallpaper and clears whichever one was previously set
    — `is_set` is a single-selection flag, not a per-row toggle."""
    record = await wallpapers.set_active(user.uid, wallpaper_id)
    return WallpaperResponse.model_validate(record)
