"""
ImportScheduler — Feature S (ADR-0020 §4.3/§4.4/§4.5).

A SINGLE asyncio interval background task that:
1. Re-reads import_schedules on each tick (config is source of truth; task is stateless).
2. If enabled and the frequency interval has elapsed, runs one bounded scan.
3. Never overlaps scans (in-flight guard).
4. Copies only NEW or CHANGED files into raw_sources/ (I1 hash-compare).
5. Lets the watcher ingest copied files (I9 — do NOT call ingest_file directly here).
6. Is double-bounded by IMPORT_SCAN_MAX_FILES + IMPORT_SCAN_MAX_SECONDS (I7).

Lifecycle: started in the FastAPI lifespan AFTER the watcher; stopped on shutdown.
Config changes (PUT /import-schedule) take effect on the next tick without restart.

Do NOT call ingest_file() here — Feature S uses the copy-then-watcher path (ADR-0020 §4.3).
Do NOT import APScheduler or any scheduler framework (I9 — ADR-0020 §4.5).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.upload import safe_source_name

logger = logging.getLogger(__name__)

# ── Frequency → seconds mapping (I7 — bounded, no runaway interval) ──────────

FREQ_SECONDS: dict[str, int] = {
    "15m": 900,
    "1h": 3_600,
    "6h": 21_600,
    "daily": 86_400,
}

# Idle-poll interval when the schedule is disabled or source_dir is unset.
# Re-reads DB this often so a config change from disabled → enabled is picked up.
_IDLE_POLL_SECONDS: int = 60


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def load_schedule(vault_id: str) -> object | None:
    """
    Load the import_schedules row for *vault_id*, or return None if absent.
    Uses a fresh DB session (stateless between ticks).
    """
    from sqlalchemy import select

    import app.db as _db
    from app.models import ImportSchedule

    try:
        async with _db.get_session() as session:
            row = await session.execute(
                select(ImportSchedule).where(ImportSchedule.vault_id == vault_id)
            )
            schedule = row.scalar_one_or_none()
            if schedule is not None:
                # Expunge so we can use it outside the session
                session.expunge(schedule)
            return schedule
    except Exception as exc:  # noqa: BLE001 — DB may be unreachable on first tick
        logger.debug("load_schedule: DB unavailable — %s", exc)
        return None


async def upsert_schedule(
    vault_id: str,
    **kwargs: object,
) -> None:
    """
    Upsert the import_schedules row for *vault_id* with the given keyword fields.
    Creates the row if absent. Thread-safe: each call is its own committed transaction.
    """
    from sqlalchemy import select

    import app.db as _db
    from app.models import ImportSchedule

    async with _db.get_session() as session:
        row = await session.execute(
            select(ImportSchedule).where(ImportSchedule.vault_id == vault_id)
        )
        schedule = row.scalar_one_or_none()
        if schedule is None:
            schedule = ImportSchedule(id=uuid.uuid4(), vault_id=vault_id, **kwargs)
            session.add(schedule)
        else:
            for key, value in kwargs.items():
                setattr(schedule, key, value)
            schedule.updated_at = datetime.now(UTC)


# ── Bounded scan ───────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Compute sha256 of file bytes (used for content-hash compare, I1)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_one_scan(cfg: object) -> tuple[int, str, str | None]:
    """
    Run one bounded scan of cfg.source_dir → vault/raw/sources/.

    Returns (imported_count, last_status, last_error).

    Bounds (I7):
    - Non-recursive: single os.scandir pass (no rglob).
    - IMPORT_SCAN_MAX_FILES copies per tick.
    - IMPORT_SCAN_MAX_SECONDS wall-clock deadline.

    Only NEW or CHANGED files are copied (I1 hash-compare before copy).
    The WATCHER ingests copied files — this function does NOT call ingest_file (ADR-0020 §4.3).
    Non-text files are silently skipped (F12/M5 boundary).
    """
    from app.upload import _ALLOWED_EXTENSIONS  # reuse the extension set

    source_dir: str | None = getattr(cfg, "source_dir", None)
    if not source_dir:
        return 0, "dir_missing", "source_dir is not set"

    source_path = Path(source_dir)
    if not source_path.is_dir():
        return (
            0,
            "dir_missing",
            f"Directory not found or not readable inside the container: {source_dir}",
        )

    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)

    max_files: int = settings.import_scan_max_files
    max_seconds: int = settings.import_scan_max_seconds

    imported_count = 0
    scanned = 0
    skipped = 0
    t_start = time.monotonic()

    try:
        entries = list(os.scandir(str(source_path)))
    except PermissionError as exc:
        return 0, "error", f"Permission denied reading {source_dir}: {exc}"
    except OSError as exc:
        return 0, "error", f"OS error reading {source_dir}: {exc}"

    for entry in entries:
        # Wall-clock cap (I7)
        if time.monotonic() - t_start >= max_seconds:
            logger.info(
                "scheduled_import: wall-clock cap (%ds) reached after %d scanned — "
                "will continue next tick",
                max_seconds,
                scanned,
            )
            break

        # File cap (I7)
        if imported_count >= max_files:
            logger.info(
                "scheduled_import: file cap (%d) reached — will continue next tick",
                max_files,
            )
            break

        if not entry.is_file(follow_symlinks=False):
            continue

        scanned += 1
        src_path = Path(entry.path)
        suffix = src_path.suffix.lower()

        # Skip non-text files (F12/M5 boundary — no error, just skip)
        if suffix not in _ALLOWED_EXTENSIONS:
            skipped += 1
            continue

        # Sanitize name (reuse §2.2 sanitizer — skips unsafe names silently)
        try:
            name = safe_source_name(entry.name)
        except Exception:  # noqa: BLE001 — 422/415 from safe_source_name
            skipped += 1
            continue

        dst_path = raw_sources / name

        # I1 hash-compare: skip if destination holds identical bytes
        if dst_path.exists():
            try:
                src_hash = _sha256_file(src_path)
                dst_hash = _sha256_file(dst_path)
                if src_hash == dst_hash:
                    skipped += 1
                    continue
            except OSError:
                pass  # If we can't read either file, attempt the copy below

        # Copy new/changed file to raw_sources/ atomically (temp+rename)
        # The WATCHER observes the destination and ingests it (ADR-0020 §4.3)
        try:
            tmp_path = dst_path.with_suffix(dst_path.suffix + ".tmp_import")
            shutil.copy2(str(src_path), str(tmp_path))
            tmp_path.replace(dst_path)
            imported_count += 1
            logger.debug("scheduled_import: copied %s → %s", src_path.name, dst_path)
        except OSError as exc:
            logger.warning("scheduled_import: failed to copy %s: %s", entry.name, exc)
            skipped += 1

    elapsed = time.monotonic() - t_start
    logger.info(
        "scheduled_import vault=%s dir=%s scanned=%d copied=%d skipped=%d elapsed=%.2fs status=ok",
        settings.vault_id,
        source_dir,
        scanned,
        imported_count,
        skipped,
        elapsed,
    )
    return imported_count, "ok", None


# ── ImportScheduler ────────────────────────────────────────────────────────────


class _ClockProtocol(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class _RealClock:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ImportScheduler:
    """
    Single asyncio interval background task for scheduled folder import (ADR-0020 §4.5).

    Lifecycle:
      - start(loop) called in FastAPI lifespan AFTER the watcher starts.
      - stop() called in lifespan shutdown alongside stop_watcher().

    Config changes are picked up on the next tick (re-reads import_schedules from DB).
    A single in-flight guard prevents overlapping scans (I7).
    The scan is bounded by MAX_FILES + MAX_SECONDS (I7).

    Injectable clock for tests: pass a mock _ClockProtocol to control timing.
    """

    def __init__(
        self,
        clock: _ClockProtocol | None = None,
        scan_fn: Callable[..., Awaitable[tuple[int, str, str | None]]] | None = None,
    ) -> None:
        self._clock: _ClockProtocol = clock or _RealClock()
        self._scan_fn = scan_fn or run_one_scan
        self._stopping: bool = False
        self._scan_in_flight: bool = False
        self._task: asyncio.Task[None] | None = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the background scheduler task. Safe to call multiple times (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        try:
            running_loop = asyncio.get_running_loop()
            self._task = running_loop.create_task(self._run(), name="import_scheduler")
        except RuntimeError:
            # No running loop — create in the provided loop (test scenario)
            if loop is not None:
                self._task = loop.create_task(self._run(), name="import_scheduler")
        logger.info("ImportScheduler started")

    def stop(self) -> None:
        """Signal the task to stop. Called on lifespan shutdown."""
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
        logger.info("ImportScheduler stopped")

    @property
    def scan_in_flight(self) -> bool:
        """True if a scan is currently running (used by POST /import-schedule/run-now)."""
        return self._scan_in_flight

    async def run_now(self) -> None:
        """
        Run one scan immediately (POST /import-schedule/run-now).
        Raises RuntimeError if a scan is already in-flight (caller maps to 409).
        Raises ValueError if disabled or source_dir unset/missing.
        """
        if self._scan_in_flight:
            raise RuntimeError("scan_in_flight")

        cfg = await load_schedule(settings.vault_id)
        if cfg is None or not getattr(cfg, "enabled", False):
            raise ValueError("Schedule is disabled or not configured")
        source_dir = getattr(cfg, "source_dir", None)
        if not source_dir:
            raise ValueError("source_dir is not set")
        if not Path(source_dir).is_dir():
            raise ValueError(f"Directory not accessible inside container: {source_dir}")

        self._scan_in_flight = True
        try:
            await upsert_schedule(
                settings.vault_id,
                last_status="running",
                updated_at=datetime.now(UTC),
            )
            imported_count, status, error = await self._scan_fn(cfg)
            await upsert_schedule(
                settings.vault_id,
                last_run_at=datetime.now(UTC),
                last_status=status,
                last_imported_count=imported_count,
                last_error=error,
                updated_at=datetime.now(UTC),
            )
        except Exception as exc:
            await upsert_schedule(
                settings.vault_id,
                last_run_at=datetime.now(UTC),
                last_status="error",
                last_error=str(exc),
                updated_at=datetime.now(UTC),
            )
            raise
        finally:
            self._scan_in_flight = False

    async def _run(self) -> None:
        """Main scheduler loop (ADR-0020 §4.5 sketch)."""
        while not self._stopping:
            # Re-read config at the top of every tick
            cfg = await load_schedule(settings.vault_id)

            enabled: bool = getattr(cfg, "enabled", False) if cfg is not None else False
            frequency: str = getattr(cfg, "frequency", "1h") if cfg is not None else "1h"
            interval = FREQ_SECONDS.get(frequency, 3_600) if enabled else _IDLE_POLL_SECONDS

            # Sleep the full interval (injectable for tests)
            try:
                await self._clock.sleep(interval)
            except asyncio.CancelledError:
                break

            if self._stopping:
                break

            # Re-read config after sleep (may have changed)
            cfg = await load_schedule(settings.vault_id)
            if cfg is None:
                continue

            enabled = getattr(cfg, "enabled", False)
            source_dir = getattr(cfg, "source_dir", None)

            if not enabled or not source_dir:
                continue

            # Single in-flight guard (I7 — never overlap)
            if self._scan_in_flight:
                logger.debug("ImportScheduler: scan already in-flight — skipping tick")
                continue

            self._scan_in_flight = True
            try:
                await upsert_schedule(
                    settings.vault_id,
                    last_status="running",
                    updated_at=datetime.now(UTC),
                )
                imported_count, status, error = await self._scan_fn(cfg)
                await upsert_schedule(
                    settings.vault_id,
                    last_run_at=datetime.now(UTC),
                    last_status=status,
                    last_imported_count=imported_count,
                    last_error=error,
                    updated_at=datetime.now(UTC),
                )
            except asyncio.CancelledError:
                self._scan_in_flight = False
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("ImportScheduler: scan failed with unhandled error: %s", exc)
                try:
                    await upsert_schedule(
                        settings.vault_id,
                        last_run_at=datetime.now(UTC),
                        last_status="error",
                        last_error=str(exc),
                        updated_at=datetime.now(UTC),
                    )
                except Exception as write_exc:  # noqa: BLE001
                    logger.debug("ImportScheduler: failed to persist error status: %s", write_exc)
            finally:
                self._scan_in_flight = False


# ── Module-level singleton (initialised in main.py lifespan) ─────────────────
_import_scheduler: ImportScheduler | None = None
