"""
Async database engine/session setup. Routers and services never construct
sessions themselves — they depend on `get_session` via FastAPI's DI, which
keeps transaction boundaries (one session per request) consistent and
makes it easy to swap in a test database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.database.models import Base


def create_engine() -> AsyncEngine:
    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(settings.database_url, echo=False)


engine: AsyncEngine = create_engine()
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """
    Create tables if they don't exist yet. Fine for early development;
    once the schema stabilizes, switch to Alembic migrations (the
    database/migrations/ folder is already reserved for that) instead of
    relying on create_all for anything beyond local dev/tests.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields one session per request, committing on
    clean exit and rolling back if the request raised."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise