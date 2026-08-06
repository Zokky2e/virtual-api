"""Shared schema building blocks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for response schemas built directly from SQLAlchemy model
    instances (`FileResponse.model_validate(record)`)."""

    model_config = ConfigDict(from_attributes=True)