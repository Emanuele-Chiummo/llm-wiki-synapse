"""
Tests for app/ops/backup.py — scheduled Postgres backup (1.9.1 W4, SEC-OPS-2).

Infra-free: `pg_dump` is never actually invoked — `asyncio.create_subprocess_exec` is
patched. No real DB, no real subprocess. Retention pruning uses a real tmp_path directory
(cheap, deterministic, no mocking needed for filesystem ops).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.ops.backup import (
    BackupSummary,
    _apply_retention,
    _archive_filename,
    _pg_dump_args,
    run_backup,
)

# ── _pg_dump_args ─────────────────────────────────────────────────────────────


def test_pg_dump_args_full_dsn() -> None:
    """A full asyncpg DSN yields host/port/user/dbname args + PGPASSWORD env."""
    # NOT a real credential — a low-entropy placeholder matching the
    # synapse/synapse convention used throughout this test suite and
    # docker-compose.ci.yml (avoids tripping secret scanners on fixture data).
    args, env = _pg_dump_args("postgresql+asyncpg://synapse:synapse-test-pw@dbhost:5433/synapse")
    assert "--host" in args and "dbhost" in args
    assert "--port" in args and "5433" in args
    assert "--username" in args and "synapse" in args
    assert "--dbname" in args and "synapse" in args
    assert env == {"PGPASSWORD": "synapse-test-pw"}


def test_pg_dump_args_no_password_no_env_key() -> None:
    """A DSN with no password produces no PGPASSWORD env override."""
    args, env = _pg_dump_args("postgresql+asyncpg://synapse@dbhost/synapse")
    assert env == {}
    assert "--username" in args


def test_pg_dump_args_missing_dbname_raises() -> None:
    """A DSN missing the database name is rejected (defensive parse guard)."""
    from app.ops.backup import _DsnParseError

    with pytest.raises(_DsnParseError):
        _pg_dump_args("postgresql+asyncpg://synapse:pw@dbhost/")


# ── _archive_filename / _apply_retention ──────────────────────────────────────


def test_archive_filename_format() -> None:
    import datetime as dt

    name = _archive_filename("myvault", dt.datetime(2026, 7, 16, 3, 0, 0, tzinfo=dt.UTC))
    assert name == "synapse-myvault-20260716T030000Z.dump"


def test_apply_retention_keeps_newest_n(tmp_path: Path) -> None:
    """Retention keeps the N most-recent archives (by filename, which sorts chronologically)."""
    names = [
        "synapse-v1-20260101T000000Z.dump",
        "synapse-v1-20260102T000000Z.dump",
        "synapse-v1-20260103T000000Z.dump",
        "synapse-v1-20260104T000000Z.dump",
    ]
    for n in names:
        (tmp_path / n).write_bytes(b"x")

    deleted = _apply_retention(tmp_path, "v1", keep=2)
    assert deleted == 2
    remaining = {p.name for p in tmp_path.glob("synapse-v1-*.dump")}
    assert remaining == {names[2], names[3]}  # newest 2 kept


def test_apply_retention_ignores_other_vaults(tmp_path: Path) -> None:
    """Retention for vault 'a' never touches archives belonging to vault 'b'."""
    (tmp_path / "synapse-a-20260101T000000Z.dump").write_bytes(b"x")
    (tmp_path / "synapse-b-20260101T000000Z.dump").write_bytes(b"x")

    deleted = _apply_retention(tmp_path, "a", keep=0)
    assert deleted == 1
    assert (tmp_path / "synapse-b-20260101T000000Z.dump").exists()


def test_apply_retention_negative_keep_clamped_to_zero(tmp_path: Path) -> None:
    (tmp_path / "synapse-v1-20260101T000000Z.dump").write_bytes(b"x")
    deleted = _apply_retention(tmp_path, "v1", keep=-5)
    assert deleted == 1


# ── run_backup — success/failure paths (subprocess mocked) ────────────────────


class _FakeProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_backup_success(tmp_path: Path) -> None:
    from app import config as cfg

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@h/db"),
        patch.object(cfg.settings, "backup_retention_count", 7),
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(returncode=0)),
        ),
    ):
        # Fake pg_dump actually writing the archive file (create_subprocess_exec is mocked,
        # so simulate the side effect of pg_dump having produced output).
        summary = await run_backup(vault_id="testvault")

    assert isinstance(summary, BackupSummary)
    assert summary.stopped_reason == "complete"
    assert summary.archive_path is not None
    assert summary.error_message is None


@pytest.mark.asyncio
async def test_run_backup_nonzero_exit_reports_error(tmp_path: Path) -> None:
    from app import config as cfg

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@h/db"),
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(returncode=1, stderr=b"connection refused")),
        ),
    ):
        summary = await run_backup(vault_id="testvault")

    assert summary.stopped_reason == "error"
    assert summary.error_message is not None
    assert "connection refused" in summary.error_message


@pytest.mark.asyncio
async def test_run_backup_pg_dump_missing_binary(tmp_path: Path) -> None:
    from app import config as cfg

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@h/db"),
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError())),
    ):
        summary = await run_backup(vault_id="testvault")

    assert summary.stopped_reason == "error"
    assert "pg_dump binary not found" in (summary.error_message or "")


@pytest.mark.asyncio
async def test_run_backup_timeout(tmp_path: Path) -> None:
    from app import config as cfg

    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=TimeoutError())
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@h/db"),
        patch.object(cfg.settings, "backup_timeout_seconds", 0.01),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        summary = await run_backup(vault_id="testvault")

    assert summary.stopped_reason == "error"
    assert "timed out" in (summary.error_message or "")
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_run_backup_bad_dsn_reports_error(tmp_path: Path) -> None:
    from app import config as cfg

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@/"),
    ):
        summary = await run_backup(vault_id="testvault")

    assert summary.stopped_reason == "error"
    assert summary.error_message is not None


@pytest.mark.asyncio
async def test_run_backup_prunes_after_success(tmp_path: Path) -> None:
    """A successful run applies retention on top of any pre-existing archives."""
    from app import config as cfg

    # Pre-seed 3 old archives for the same vault.
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        (tmp_path / f"synapse-testvault-{stamp}.dump").write_bytes(b"x")

    with (
        patch.object(cfg.settings, "backup_dir", str(tmp_path)),
        patch.object(cfg.settings, "database_url", "postgresql+asyncpg://u:p@h/db"),
        patch.object(cfg.settings, "backup_retention_count", 2),
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(returncode=0)),
        ),
    ):
        summary = await run_backup(vault_id="testvault")

    assert summary.stopped_reason == "complete"
    # 3 pre-seeded archives on disk (the mocked subprocess never actually writes the new
    # archive file — only the retention SIDE EFFECT is under test here) minus keep=2 → 1
    # deleted.
    assert summary.deleted_count == 1
    remaining = list(tmp_path.glob("synapse-testvault-*.dump"))
    assert len(remaining) == 2


# ── Regression: the DSN (hence the password) must never reach a message ───────
# The module contract is "the DSN itself is never logged". The parse-failure path broke it:
# the raw DSN was interpolated into _DsnParseError, and run_backup both logs that message at
# ERROR and returns it in BackupSummary.error_message (which system_update logs again).


def test_dsn_parse_error_redacts_the_password() -> None:
    """A DSN missing the host still reports usefully — without the credentials."""
    from app.ops.backup import _DsnParseError

    # A unix-socket DSN: ordinary operator config, no host → the reachable failure path.
    with pytest.raises(_DsnParseError) as excinfo:
        _pg_dump_args("postgresql+asyncpg://synapse:SuperSecret123@/synapse")

    message = str(excinfo.value)
    assert "SuperSecret123" not in message
    assert "***" in message
    # Still actionable: the operator can see which DSN was rejected.
    assert "synapse" in message


def test_redact_dsn_handles_awkward_passwords_and_no_password() -> None:
    """The netloc is rebuilt from parsed parts, so '@'/':' in a password cannot survive."""
    from app.ops.backup import _redact_dsn

    out = _redact_dsn("postgresql+asyncpg://user:p@ss:word@dbhost:5432/synapse")
    assert "p@ss:word" not in out
    assert out.startswith("postgresql+asyncpg://user:***@")

    # Nothing to redact → returned unchanged.
    plain = "postgresql+asyncpg://user@dbhost/synapse"
    assert _redact_dsn(plain) == plain


# ── Regression: retention is scoped to ONE vault, exactly ────────────────────
# `synapse-<vault_id>-*.dump` is not an identity test: '*' also swallows the '<suffix>-' of a
# vault whose id starts with this one plus '-', so vault 'home' matched vault 'home-lab''s
# archives — and, because 'l' > '2', sorted them FIRST, pruning all of 'home''s real dumps.


def test_apply_retention_ignores_vault_whose_id_shares_a_hyphen_prefix(tmp_path: Path) -> None:
    stamps = ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]
    for s in stamps:
        (tmp_path / f"synapse-home-{s}.dump").write_bytes(b"x")
        (tmp_path / f"synapse-home-lab-{s}.dump").write_bytes(b"x")

    # keep=3 and vault 'home' owns exactly 3 archives → nothing is due for pruning.
    deleted = _apply_retention(tmp_path, "home", keep=3)
    assert deleted == 0
    assert len({p.name for p in tmp_path.glob("synapse-home-2*.dump")}) == 3
    assert len({p.name for p in tmp_path.glob("synapse-home-lab-*.dump")}) == 3


def test_apply_retention_prunes_only_its_own_vault_when_over_keep(tmp_path: Path) -> None:
    for s in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (tmp_path / f"synapse-home-{s}.dump").write_bytes(b"x")
        (tmp_path / f"synapse-home-lab-{s}.dump").write_bytes(b"x")

    deleted = _apply_retention(tmp_path, "home", keep=1)
    assert deleted == 2
    assert {p.name for p in tmp_path.glob("synapse-home-2*.dump")} == {
        "synapse-home-20260103T000000Z.dump"
    }
    # The other vault is untouched.
    assert len({p.name for p in tmp_path.glob("synapse-home-lab-*.dump")}) == 3


def test_apply_retention_ignores_foreign_filenames(tmp_path: Path) -> None:
    """Only well-formed `synapse-<vault>-<stamp>.dump` names are ever considered."""
    (tmp_path / "synapse-v1-20260101T000000Z.dump").write_bytes(b"x")
    (tmp_path / "synapse-v1-notatimestamp.dump").write_bytes(b"x")
    (tmp_path / "synapse-v1-20260101T000000Z.dump.tmp").write_bytes(b"x")

    assert _apply_retention(tmp_path, "v1", keep=0) == 1
    assert (tmp_path / "synapse-v1-notatimestamp.dump").exists()
    assert (tmp_path / "synapse-v1-20260101T000000Z.dump.tmp").exists()
