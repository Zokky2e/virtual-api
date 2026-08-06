"""
FastAPI application entry point. Run with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000

(or through a process manager / systemd unit that does the same, once
this moves onto the Ubuntu box behind Caddy).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.database.database import init_db
from app.exceptions import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev/early-stage convenience — creates tables if they don't exist.
    # Once the schema stabilizes, replace this with an Alembic migration
    # run as a separate deploy step instead of doing it on every startup.
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Virtual Desktop API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    return app


app = create_app()