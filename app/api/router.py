from __future__ import annotations

from fastapi import APIRouter

from app.api import files, folders, health, shared, streaming, wallpapers, websocket

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(folders.router, prefix="/desktop")
api_router.include_router(files.router, prefix="/desktop")
api_router.include_router(streaming.router, prefix="/desktop")
api_router.include_router(websocket.router)  # /ws — outside the REST namespace
api_router.include_router(shared.router, prefix="/desktop")
# Wallpapers are not file-tree items and get their own prefix, table and
# storage root — see app/api/wallpapers.py. Mounted after the /desktop
# routers so its more specific prefix is unambiguous.
api_router.include_router(wallpapers.router, prefix="/desktop/wallpapers")