"""
Response schema for a file-tree item (file or folder). Field names and
shapes are chosen to map onto lib/core/models/file_item.dart's FileItem
with minimal translation on the Flutter side. Deliberately does NOT
include storage_key — that's an internal pointer into StorageRepository,
never something the client needs; downloads go through /desktop/download/
{id}, not a client-constructed path.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import computed_field

from app.database.models import FileType
from app.schemas.common import ORMModel


class FileResponse(ORMModel):
    id: str
    name: str
    parent_folder_id: str | None
    owner_id: str
    type: FileType
    size: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_folder(self) -> bool:
        return self.type is FileType.folder