"""Streaming endpoint — serves file bytes with HTTP Range support so
video players can scrub without downloading the whole file."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_stream_service
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.services.stream_service import StreamService

router = APIRouter(tags=["streaming"])


@router.get("/stream/{item_id}")
async def stream_file(
    item_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    stream_service: StreamService = Depends(get_stream_service),
) -> StreamingResponse:
    range_header = request.headers.get("range")
    return await stream_service.stream(user.uid, item_id, range_header)