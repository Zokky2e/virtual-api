"""
Streaming operations — resolves an item id + optional Range header into a
ready-to-return StreamingResponse, combining FileRepository (metadata:
storage_key, size) and StorageRepository (bytes). Mirrors what
PreviewBloc + StorageService.getDownloadUrl do together on the Flutter
side, except here the server streams bytes directly rather than handing
back a signed URL.
"""

from __future__ import annotations

import mimetypes

from fastapi.responses import StreamingResponse

from app.database.repositories import FileRepository
from app.exceptions import InvalidOperationError, NotFoundError
from app.storage.base import StorageRepository
from app.streaming.range import RangeParseError, parse_range_header
from app.streaming.response import build_stream_response


class StreamService:
    def __init__(self, repo: FileRepository, storage: StorageRepository):
        self._repo = repo
        self._storage = storage

    async def stream(
        self, owner_id: str, item_id: str, range_header: str | None
    ) -> StreamingResponse:
        record = await self._repo.get_by_id(owner_id, item_id)
        if record is None or record.is_folder or record.storage_key is None:
            raise NotFoundError(f"Streamable file not found: {item_id}")

        file_size = await self._storage.size(record.storage_key)
        content_type = mimetypes.guess_type(record.name)[0] or "application/octet-stream"

        try:
            byte_range = parse_range_header(range_header, file_size)
        except RangeParseError as exc:
            raise InvalidOperationError(str(exc)) from exc

        return build_stream_response(
            storage=self._storage,
            storage_key=record.storage_key,
            file_size=file_size,
            content_type=content_type,
            byte_range=byte_range,
        )