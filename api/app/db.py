"""Async SQLAlchemy engine/session for app tables."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import APP_SCHEMA, Settings

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _sessions
    _engine = create_async_engine(
        settings.sqlalchemy_url(),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,  # Cloud SQL drops idle connections
        connect_args={"server_settings": {"search_path": f"{APP_SCHEMA},public"}},
    )
    _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB engine not initialised")
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    if _sessions is None:
        raise RuntimeError("DB engine not initialised")
    async with _sessions() as session:
        yield session
