from __future__ import annotations

from fastapi import APIRouter

from app.api import files, folders, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(folders.router, prefix="/desktop")
api_router.include_router(files.router, prefix="/desktop")