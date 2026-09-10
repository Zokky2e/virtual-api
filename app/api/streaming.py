"""Streaming endpoint — serves file bytes with HTTP Range support so
video players can scrub without downloading the whole file."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_file_service, get_stream_service
from app.auth.dependencies import get_current_user, get_stream_caller
from app.auth.models import AuthUser
from app.auth.stream_token import mint as mint_stream_token
from app.config import get_settings
from app.schemas.stream import StreamTokenResponse
from app.services.file_service import FileService
from app.services.stream_service import StreamService

router = APIRouter(tags=["streaming"])


@router.get("/stream-token/{item_id}", response_model=StreamTokenResponse)
async def issue_stream_token(
    item_id: str,
    user: AuthUser = Depends(get_current_user),
    files: FileService = Depends(get_file_service),
) -> StreamTokenResponse:
    """Mints a token the player can keep using for the whole of a long
    file, instead of embedding the caller's Firebase ID token in the URL.

    get_record runs first and raises 404 unless this caller really owns
    the item — the token is only as trustworthy as this check.
    """
    await files.get_record(user.uid, item_id)
    token, expires_at = mint_stream_token(
        user.uid, item_id, get_settings().stream_token_ttl_seconds
    )
    return StreamTokenResponse(token=token, expires_at=expires_at)


@router.get("/stream/{item_id}")
async def stream_file(
    item_id: str,
    request: Request,
    owner_id: str = Depends(get_stream_caller),
    stream_service: StreamService = Depends(get_stream_service),
) -> StreamingResponse:
    range_header = request.headers.get("range")
    return await stream_service.stream(owner_id, item_id, range_header)