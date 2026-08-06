"""
SQLAlchemy models for file-tree metadata — the server-side counterpart to
lib/core/models/file_item.dart. Binary bytes live in app/storage/ (see
storage/base.py's docstring); this table only ever stores the tree
structure, ownership, and pointers into storage.

Mirrors FileItem's fields 1:1 so the eventual LocalServerStorageService on
the Flutter side can map a FastAPI JSON response straight onto FileItem
with no field-name gymnastics.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class FileType(str, enum.Enum):
    """Mirrors FileItemType in lib/core/models/file_item.dart — keep these
    two enums in sync by hand; there are only nine values."""

    folder = "folder"
    image = "image"
    video = "video"
    audio = "audio"
    pdf = "pdf"
    text = "text"
    json = "json"
    markdown = "markdown"
    other = "other"


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Self-referential — null means "lives at the root of this owner's tree".
    parent_folder_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("files.id"), nullable=True, index=True
    )

    # Firebase UID of the owning user. Every query in the repositories
    # layer filters on this — there is no cross-user access at the DB
    # layer, same as the Firestore security-rule boundary on the client.
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)

    # Null for folders. Opaque key into StorageRepository — see
    # storage/base.py. Never a filesystem path directly.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    children: Mapped[list["FileRecord"]] = relationship(
        "FileRecord",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent: Mapped["FileRecord | None"] = relationship(
        "FileRecord", back_populates="children", remote_side=[id]
    )

    @property
    def is_folder(self) -> bool:
        return self.type is FileType.folder