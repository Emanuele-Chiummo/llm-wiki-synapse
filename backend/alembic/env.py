"""
Alembic environment — async configuration for asyncpg + SQLAlchemy 2.

Alembic lives at backend/alembic/ (OUTSIDE backend/app/) per AQ-7.
The no-raw-SQL grep scope is backend/app/ only; migration SQL here is expected.

All DB connection info from DATABASE_URL env var (AC-DC-5 — no secrets in code).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ── Alembic config object ──────────────────────────────────────────────────────
config = context.config

# Interpret the config file for Python logging (if present)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)  # type: ignore[arg-type]

# ── Import models so autogenerate can see them ────────────────────────────────
# NOTE: importing app.models triggers Settings() which reads env vars.
# That is intentional: the migration environment must know the schema.
from app.models import Base  # noqa: E402

target_metadata = Base.metadata

# ── Get database URL from environment (or alembic.ini fallback) ───────────────


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. " "Pass it via environment or alembic.ini sqlalchemy.url."
        )
    # Ensure the URL uses asyncpg driver
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# ── Offline mode (generate SQL without DB connection) ─────────────────────────


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emits SQL to stdout or a script)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (runs against a live DB) ─────────────────────────────────────


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations via a sync wrapper."""
    engine = create_async_engine(_get_url(), poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode."""
    asyncio.run(run_async_migrations())


# ── Dispatch ──────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
