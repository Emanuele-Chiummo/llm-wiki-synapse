"""
Shared SQLite in-memory database fixture for Synapse backend unit tests.

Single source of truth: uses ``Base.metadata.create_all()`` so that every future
schema change (new column, new table) propagates automatically to *all* test files
that import this module — eliminating the 14-file × hand-rolled-CREATE-TABLE drift
that caused ~23 simultaneous test breaks on every VaultState column addition
(QA-TEST-1, 1.9.0 W2).

Usage in test files
-------------------
Replace local ``_setup_sqlite(engine)`` calls with::

    from tests._db_fixtures import make_sqlite_engine, make_session_factory

Or, for a pytest fixture::

    from tests._db_fixtures import sqlite_engine_fixture as sqlite_engine

Design notes
------------
* Each call to ``make_sqlite_engine()`` returns a fresh in-memory SQLite engine
  (StaticPool, check_same_thread=False) so tests are fully isolated.
* JSONB columns use ``.with_variant(JSON(), "sqlite")`` in the models, so they
  round-trip as JSON strings in SQLite — same behaviour as when tests use raw SQL
  with ``json.dumps()``.
* UUID columns: ``Page.id`` uses ``UUID(as_uuid=True)`` (no sqlite variant) which
  SQLAlchemy renders as CHAR(32).  Tests that insert UUID strings via raw SQL work
  because SQLite has no type enforcement.  Tests that insert via the ORM receive a
  32-char hex UUID string in return (not a ``uuid.UUID`` object); adjust assertions
  accordingly if needed.
* ``TIMESTAMP(timezone=True)`` renders as DATETIME in SQLite; datetime values are
  stored as TEXT by aiosqlite and returned as strings by raw SQL selects.
* Partial indexes: ``uix_pages_vault_file_path_live`` and
  ``ix_conversations_vault_updated_live`` now carry ``sqlite_where=`` so the
  partial-uniqueness semantics are preserved (rows with ``deleted_at IS NOT NULL``
  are excluded from the uniqueness check).  See models.py change in this commit.

Caveats vs. the hand-rolled DDL
--------------------------------
* The full schema (16 tables) is created, not just the subset a test used before.
  This is intentional: tests can now reference any table without declaring it.
* ``UniqueConstraint("vault_id", ...)`` on ``vault_state`` and ``import_schedules``
  enforces one row per vault_id.  Tests that insert multiple vault_state rows with
  the same vault_id must use different vault_ids (they should already do so).
"""

from __future__ import annotations

import pytest
from app.models import Base
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


async def make_sqlite_engine() -> AsyncEngine:
    """
    Return a fresh in-memory SQLite ``AsyncEngine`` with the full Synapse schema.

    The schema is created via ``Base.metadata.create_all()``, which is the single
    source of truth for all column definitions, constraints, and indexes.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an ``async_sessionmaker`` bound to the given engine."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# Optional pytest fixture convenience — import it by name in test files that
# use pytest fixtures directly (as opposed to calling make_sqlite_engine()
# explicitly inside a custom fixture body).
# ---------------------------------------------------------------------------


@pytest.fixture()
async def sqlite_engine() -> AsyncEngine:
    """
    Pytest fixture: a fresh in-memory SQLite engine for each test function.

    Usage::

        from tests._db_fixtures import sqlite_engine  # noqa: F401 (fixture import)

        async def test_something(sqlite_engine: AsyncEngine) -> None:
            session_factory = make_session_factory(sqlite_engine)
            ...
    """
    engine = await make_sqlite_engine()
    yield engine
    await engine.dispose()
