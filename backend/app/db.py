"""
Async database engine and session factory (SQLAlchemy 2 + asyncpg).

All configuration from Settings (no hardcoded DSNs — AC-DC-5).
Import `async_session_factory` to get a context-managed session.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,  # set True via ENV for SQL debug logging only
    pool_pre_ping=True,  # detect stale connections
    pool_size=5,
    max_overflow=10,
)

# ── Session factory ───────────────────────────────────────────────────────────
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an AsyncSession via an async context manager.

    Usage::

        async with get_session() as session:
            result = await session.execute(select(Page))

    The session is committed on clean exit and rolled back on exception.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the connection pool (called on shutdown)."""
    await engine.dispose()
