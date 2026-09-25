"""
Scheduled + on-demand Postgres backup (1.9.1 W4 — SEC-OPS-2).

The vault (raw/ + wiki/) already has manual export via GET /export; the database
(pages/links/edges/runs/review_items/conversations/provider_config/vault_state) had NO
backup at all before Watchtower auto-updated the containers and ran `alembic upgrade head`
on every boot with no dump beforehand. This module fixes that gap:

  run_backup(vault_id=...) → BackupSummary
    1. `pg_dump -Fc` (custom/compressed format — supports partial/selective `pg_restore`)
       the database referenced by settings.database_url, into
       settings.backup_root / "synapse-<vault_id>-<UTC timestamp>.dump".
    2. Deletes archives beyond settings.backup_retention_count (oldest first), keeping the
       backup directory bounded (I7 — no unbounded disk growth).

Callers:
  - OpsScheduler (app/ops_scheduler.py) — new "backup" op, same off|hourly|daily|weekly
    pattern as lint/backfill/schema_review/reclassify (backup_schedule config key).
  - POST /ops/system-update (app/ops/system_update.py) — runs ONE backup immediately,
    best-effort, BEFORE poking Watchtower, so an update that breaks something has a fresh
    rollback point. A backup failure does NOT block the update (documented trade-off below).

Bounds (I7):
  - `pg_dump` runs under a hard wall-clock timeout (settings.backup_timeout_seconds);
    a stuck dump is killed rather than hanging the scheduler/endpoint forever.
  - Retention caps the number of archives kept — never unbounded.
  - Never raises out of `run_backup`: every failure mode (missing pg_dump binary, DSN parse
    error, subprocess non-zero exit, timeout, disk error) is caught and returned as
    `stopped_reason="error"` so the OpsScheduler's `_interpret_result` (R13-12 contract)
    reports the TRUE outcome instead of a blind "ok".

Security: the Postgres password is passed via the `PGPASSWORD` subprocess env var (never on
the command line, so it never appears in `ps`/logs); the DSN itself is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from app.config import settings

logger = logging.getLogger(__name__)

_ARCHIVE_GLOB_PREFIX = "synapse-"
_ARCHIVE_SUFFIX = ".dump"

# The UTC stamp `_archive_filename` embeds: "%Y%m%dT%H%M%SZ" (fixed width, so plain
# lexicographic ordering on the full filename is chronological).
_ARCHIVE_STAMP_PATTERN = r"\d{8}T\d{6}Z"


@dataclass
class BackupSummary:
    """Outcome of one backup run (mirrors BackfillSummary/ReclassifySummary shape, R13-12)."""

    stopped_reason: str = "complete"  # complete | error
    archive_path: str | None = None
    archive_bytes: int = 0
    deleted_count: int = 0
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stopped_reason": self.stopped_reason,
            "archive_path": self.archive_path,
            "archive_bytes": self.archive_bytes,
            "deleted_count": self.deleted_count,
            "error_message": self.error_message,
        }


class _DsnParseError(RuntimeError):
    """Raised when settings.database_url cannot be decomposed into pg_dump args."""


def _redact_dsn(database_url: str) -> str:
    """
    Render *database_url* for a human-readable message with the password replaced by ``***``.

    The module contract (see the header) is that the DSN is NEVER logged. The parse-failure
    path was the one place that broke it: the raw DSN was interpolated into the
    :class:`_DsnParseError` message, and that message is both logged at ERROR by
    :func:`run_backup` and returned verbatim in ``BackupSummary.error_message`` (which
    ``system_update`` logs a second time). A DSN missing a host is an ordinary operator
    misconfiguration — a unix-socket DSN like ``postgresql+asyncpg://u:pw@/synapse`` — so this
    is reachable with no adversary, and the resulting log line is exactly what an operator
    would paste into a bug report.

    The netloc is REBUILT from the parsed parts rather than string-replaced, so a password
    containing ``@`` or ``:`` cannot survive into the output. A DSN with no password is
    returned unchanged (there is no secret in it).
    """
    try:
        parsed = urlparse(database_url)
    except ValueError:
        return "<unparseable DSN>"
    if not parsed.password:
        return database_url
    host_part = parsed.netloc.rsplit("@", 1)[-1]
    user_part = f"{parsed.username}:***" if parsed.username else "***"
    return urlunparse(parsed._replace(netloc=f"{user_part}@{host_part}"))


def _archive_name_re(vault_id: str) -> re.Pattern[str]:
    """
    Exact-identity matcher for ONE vault's archive filenames.

    ``synapse-<vault_id>-<stamp>.dump`` — fully anchored, with *vault_id* escaped. The glob
    ``synapse-<vault_id>-*.dump`` is NOT an identity test: ``*`` also swallows the
    ``<suffix>-`` of any OTHER vault whose id starts with this one plus ``-`` (vault ``home``
    matching vault ``home-lab``'s archives), which let one vault's retention prune another
    vault's dumps. Anchoring on the fixed-width timestamp closes that.
    """
    return re.compile(
        rf"^{re.escape(_ARCHIVE_GLOB_PREFIX)}{re.escape(vault_id)}-"
        rf"{_ARCHIVE_STAMP_PATTERN}{re.escape(_ARCHIVE_SUFFIX)}$"
    )


def _pg_dump_args(database_url: str) -> tuple[list[str], dict[str, str]]:
    """
    Turn an asyncpg SQLAlchemy DSN (postgresql+asyncpg://user:pass@host:port/db) into
    (`pg_dump` argv tail, env overrides). The driver suffix (+asyncpg) is stripped —
    pg_dump speaks the wire protocol directly, no driver involved.

    Raises _DsnParseError on a DSN missing host/db (defensive; should not happen in practice
    since Settings.database_url is a required field validated at process startup).
    """
    parsed = urlparse(database_url.replace("+asyncpg", "", 1))
    host = parsed.hostname
    dbname = parsed.path.lstrip("/")
    if not host or not dbname:
        # Credentials are redacted: this message is logged AND returned to the caller.
        raise _DsnParseError(
            f"database_url is missing host or database name: {_redact_dsn(database_url)!r}"
        )

    args = ["--host", host, "--dbname", dbname]
    if parsed.port:
        args += ["--port", str(parsed.port)]
    if parsed.username:
        args += ["--username", unquote(parsed.username)]

    env: dict[str, str] = {}
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    return args, env


def _archive_filename(vault_id: str, now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{_ARCHIVE_GLOB_PREFIX}{vault_id}-{stamp}{_ARCHIVE_SUFFIX}"


def _apply_retention(backup_dir: Path, vault_id: str, keep: int) -> int:
    """
    Delete archives for this vault beyond the newest `keep`. Returns count deleted.

    Candidates are enumerated with a vault-agnostic glob and then filtered through
    :func:`_archive_name_re`, so only THIS vault's archives can ever be pruned. Sorting by
    name stays chronological because every surviving name shares the identical
    ``synapse-<vault_id>-`` prefix and differs only in the fixed-width UTC stamp.
    """
    if keep < 0:
        keep = 0
    name_re = _archive_name_re(vault_id)
    archives = sorted(
        (
            p
            for p in backup_dir.glob(f"{_ARCHIVE_GLOB_PREFIX}*{_ARCHIVE_SUFFIX}")
            if name_re.match(p.name)
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    stale = archives[keep:]
    deleted = 0
    for path in stale:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:  # noqa: BLE001 — non-fatal cleanup (I7)
            logger.warning("backup: failed to prune stale archive %s: %s", path, exc)
    return deleted


async def run_backup(vault_id: str | None = None) -> BackupSummary:
    """
    Run one bounded `pg_dump` of the configured database, then apply retention.

    Never raises — every failure path returns `BackupSummary(stopped_reason="error", ...)`.
    """
    vid = vault_id or settings.vault_id
    backup_dir = settings.backup_root

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("backup: cannot create backup_dir %s: %s", backup_dir, exc)
        return BackupSummary(stopped_reason="error", error_message=f"mkdir failed: {exc}")

    try:
        pg_args, extra_env = _pg_dump_args(settings.database_url)
    except _DsnParseError as exc:
        logger.error("backup: %s", exc)
        return BackupSummary(stopped_reason="error", error_message=str(exc))

    archive_name = _archive_filename(vid, datetime.now(UTC))
    archive_path = backup_dir / archive_name

    cmd = [
        settings.pg_dump_path,
        *pg_args,
        "--format=custom",  # -Fc: compressed, supports selective pg_restore
        "--no-password",  # never prompt interactively; auth via PGPASSWORD/.pgpass only
        "--file",
        str(archive_path),
    ]

    import os  # noqa: PLC0415 — deferred; only needed for the subprocess env merge

    env = {**os.environ, **extra_env}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.backup_timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error(
                "backup: pg_dump timed out after %ss (vault=%s)",
                settings.backup_timeout_seconds,
                vid,
            )
            archive_path.unlink(missing_ok=True)
            return BackupSummary(
                stopped_reason="error",
                error_message=f"pg_dump timed out after {settings.backup_timeout_seconds}s",
            )

        if proc.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace")[:500]
            logger.error("backup: pg_dump exited %s (vault=%s): %s", proc.returncode, vid, msg)
            archive_path.unlink(missing_ok=True)
            return BackupSummary(
                stopped_reason="error",
                error_message=f"pg_dump exited {proc.returncode}: {msg}",
            )
    except FileNotFoundError:
        logger.error("backup: pg_dump binary not found (%s)", settings.pg_dump_path)
        return BackupSummary(
            stopped_reason="error",
            error_message=f"pg_dump binary not found: {settings.pg_dump_path!r}",
        )
    except Exception as exc:  # noqa: BLE001 — never crash the caller (I7)
        logger.error("backup: unexpected error running pg_dump (vault=%s): %s", vid, exc)
        return BackupSummary(stopped_reason="error", error_message=str(exc))

    try:
        archive_bytes = archive_path.stat().st_size
    except OSError:
        archive_bytes = 0

    deleted = _apply_retention(backup_dir, vid, settings.backup_retention_count)

    logger.info(
        "backup: complete vault=%s archive=%s bytes=%d pruned=%d",
        vid,
        archive_path,
        archive_bytes,
        deleted,
    )
    return BackupSummary(
        stopped_reason="complete",
        archive_path=str(archive_path),
        archive_bytes=archive_bytes,
        deleted_count=deleted,
    )
