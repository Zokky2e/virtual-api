"""
Builds a StreamingResponse (full or partial) for a file, given its size
and a StorageRepository, so the 200-vs-206 + header split lives in one
place instead of inside the router.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import status
from fastapi.responses import StreamingResponse

from app.storage.base import StorageRepository
from app.streaming.range import ByteRange


async def _full_file_iterator(
    storage: StorageRepository, storage_key: str, size: int
) -> AsyncIterator[bytes]:
    if size == 0:
        return
    async for chunk in storage.open_range(storage_key, 0, size - 1):
        yield chunk


def build_stream_response(
    *,
    storage: StorageRepository,
    storage_key: str,
    file_size: int,
    content_type: str,
    byte_range: ByteRange | None,
) -> StreamingResponse:
    if byte_range is None:
        return StreamingResponse(
            _full_file_iterator(storage, storage_key, file_size),
            status_code=status.HTTP_200_OK,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    content_length = byte_range.end - byte_range.start + 1
    return StreamingResponse(
        storage.open_range(storage_key, byte_range.start, byte_range.end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{file_size}",
            "Content-Length": str(content_length),
        },
    )