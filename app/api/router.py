from __future__ import annotations

from fastapi import APIRouter

from app.api import files, folders, health, streaming, websocket

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(folders.router, prefix="/desktop")
api_router.include_router(files.router, prefix="/desktop")
api_router.include_router(streaming.router, prefix="/desktop")
api_router.include_router(websocket.router)  # /ws — outside the REST namespace