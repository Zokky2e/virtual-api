"""Request bodies for folder/item mutations."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_folder_id: str | None = None


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MoveRequest(BaseModel):
    item_id: str
    parent_folder_id: str | None = None