"""
Response schema for a wallpaper. Maps onto
lib/core/models/wallpaper_item.dart's WallpaperItem, which extends
FileItem — hence the `parent_folder_id` and `type` fields, which are
constant here rather than stored: a wallpaper never has a parent and is
always an image. They're emitted anyway so the Flutter mapper can build a
WallpaperItem without inventing values at the call site.

Like FileResponse, this deliberately omits `storage_key` — bytes are
reached through GET /desktop/wallpapers/stream/{id}, never through a
client-constructed path.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import computed_field

from app.schemas.common import ORMModel


class WallpaperResponse(ORMModel):
    id: str
    name: str
    owner_id: str
    size: int
    is_set: bool
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def parent_folder_id(self) -> str | None:
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def type(self) -> str:
        return "image"
