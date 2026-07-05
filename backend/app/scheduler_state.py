"""
Persistent scheduler state helpers (R13-4 / T4).

Both OpsScheduler and ImportScheduler previously kept last-run timestamps in
memory only — every container restart (e.g. via Watchtower) lost that state and
caused weekly/daily ops to fire immediately on the first tick after restart.

This module provides two thin helpers that read/write directly to the existing
``app_config`` key/value store (AppConfig model, __tablename__ = "app_config").
These keys are NOT in ALLOWED_CONFIG_KEYS (they are internal, not user-configurable
via the API) so they bypass the API security allow-list and are written directly.

Key naming convention:
  ops_scheduler.last_run.<op>   — one per OpsScheduler op (lint, backfill, …)
  import_scheduler.last_run     — single key for ImportScheduler

Persistence contract:
  - On each *successful* scheduled run: upsert the key with the ISO-8601 timestamp.
  - On startup: load persisted timestamps to avoid spurious re-runs (R13-4 T4 goal).
  - Non-fatal: any DB error is logged and swallowed (scheduler must never crash on
    a failed state persist — I7 non-fatal loop contract).
  - Fail-closed on load: if the key is absent or malformed, return None so the
    scheduler treats it as "never run" (safe: may run once more than necessary,
    never skips a run indefinitely).

ADR references: ADR-0053 (app_config layer), R13-4 (T4 — persistent scheduler state).
Invariants: I7 (non-fatal), I1 (no rescan on startup — purely reading timestamps).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Keys we write — informational only; not enforced here (no allow-list for internal keys).
_KEY_PREFIX_OPS = "ops_scheduler.last_run."
_KEY_IMPORT = "import_scheduler.last_run"


async def save_scheduler_ts(key: str, dt: datetime) -> None:
    """
    Upsert *key* → ISO-8601 timestamp into app_config.

    Non-fatal: any DB error is logged at DEBUG level and swallowed.
    Writes the UTC timestamp regardless of the timezone on *dt* (normalises to UTC).
    """
    import app.db as _db  # noqa: PLC0415
    from app.models import AppConfig  # noqa: PLC0415

    # Normalise to UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    value = dt.isoformat()

    try:
        from sqlalchemy import select  # noqa: PLC0415

        async with _db.get_session() as session:
            row = await session.execute(select(AppConfig).where(AppConfig.key == key))
            cfg = row.scalar_one_or_none()
            if cfg is None:
                session.add(AppConfig(key=key, value=value, updated_at=datetime.now(UTC)))
            else:
                cfg.value = value
                cfg.updated_at = datetime.now(UTC)
        logger.debug("scheduler_state: saved key=%s value=%s", key, value)
    except Exception as exc:  # noqa: BLE001 — non-fatal (I7)
        logger.debug("scheduler_state: failed to save key=%s: %s", key, exc)


async def load_scheduler_ts(key: str) -> datetime | None:
    """
    Load a persisted timestamp for *key* from app_config.

    Returns a UTC-aware datetime on success, or None on absence/error.
    Fail-closed: returns None so caller treats the state as "never run" (safe).
    """
    import app.db as _db  # noqa: PLC0415
    from app.models import AppConfig  # noqa: PLC0415

    try:
        from sqlalchemy import select  # noqa: PLC0415

        async with _db.get_session() as session:
            row = await session.execute(select(AppConfig).where(AppConfig.key == key))
            cfg = row.scalar_one_or_none()
            if cfg is None:
                return None
            # Parse ISO-8601; ensure UTC-aware
            ts = datetime.fromisoformat(cfg.value)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            else:
                ts = ts.astimezone(UTC)
            logger.debug("scheduler_state: loaded key=%s value=%s", key, ts.isoformat())
            return ts
    except Exception as exc:  # noqa: BLE001 — fail-closed (I7)
        logger.debug("scheduler_state: failed to load key=%s: %s", key, exc)
        return None
