"""
Unit tests for the mtime-then-hash skip logic (ADR-0001, AC-WATCH-2, AC-K4-3).

These tests are INFRA-FREE — they call pure Python functions and do NOT touch
Postgres, Qdrant, or the embedding service.  They exercise the change-detection
logic that lives in the ingest seam.

Tested logic (from orchestrator.py):
  - mtime unchanged  → fast-path SKIP (no hash computation)
  - mtime changed, hash same → touch-mtime SKIP
  - mtime changed, hash different → proceed to upsert (exit this unit's scope)
  - new file (no existing row) → always proceeds
"""

from __future__ import annotations

import hashlib

# ── Pure helpers extracted from orchestrator for unit-test isolation ──────────


def _sha256(data: bytes) -> str:
    """Same hash function as orchestrator._sha256."""
    return hashlib.sha256(data).hexdigest()


class _FakePage:
    """Minimal stand-in for a Page ORM row (infra-free)."""

    def __init__(
        self,
        page_id: str = "00000000-0000-0000-0000-000000000001",
        source_mtime_ns: int | None = None,
        content_hash: str = "",
    ) -> None:
        self.id = page_id
        self.source_mtime_ns = source_mtime_ns
        self.content_hash = content_hash


def _should_skip_mtime(existing: _FakePage | None, current_mtime_ns: int) -> bool:
    """
    Reproduce the mtime fast-path from orchestrator.ingest_file (ADR-0001 step 1).

    Returns True if the mtime is unchanged (fast-path skip).
    """
    return (
        existing is not None
        and existing.source_mtime_ns is not None
        and existing.source_mtime_ns == current_mtime_ns
    )


def _should_skip_hash(existing: _FakePage | None, current_hash: str) -> bool:
    """
    Reproduce the hash check from orchestrator.ingest_file (ADR-0001 step 2b).

    Returns True if mtime differs but hash is identical (touch-mtime-only skip).
    """
    return existing is not None and existing.content_hash == current_hash


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMtimeFastPath:
    """ADR-0001 step 1 — mtime unchanged → skip immediately, no hash read."""

    def test_mtime_unchanged_returns_skip(self) -> None:
        """If stored mtime_ns == current mtime_ns, we skip without reading the file."""
        mtime = 1_700_000_000_000_000_000  # nanosecond timestamp
        existing = _FakePage(source_mtime_ns=mtime, content_hash="abc")
        assert _should_skip_mtime(existing, mtime) is True

    def test_mtime_changed_does_not_skip(self) -> None:
        """If mtime differs, we must NOT skip on the fast path."""
        existing = _FakePage(source_mtime_ns=1_000, content_hash="abc")
        assert _should_skip_mtime(existing, 2_000) is False

    def test_no_existing_row_does_not_skip(self) -> None:
        """A brand-new file (no DB row) must always be indexed."""
        assert _should_skip_mtime(None, 1_000) is False

    def test_existing_row_with_null_mtime_does_not_fast_skip(self) -> None:
        """If stored source_mtime_ns is NULL, fall through to hash check."""
        existing = _FakePage(source_mtime_ns=None, content_hash="abc")
        assert _should_skip_mtime(existing, 1_000) is False

    def test_mtime_zero_unchanged(self) -> None:
        """Edge case: mtime_ns == 0 (old filesystem or test fixture)."""
        existing = _FakePage(source_mtime_ns=0, content_hash="abc")
        assert _should_skip_mtime(existing, 0) is True


class TestHashGate:
    """ADR-0001 step 2b — mtime changed but hash identical → touch-mtime skip."""

    def test_identical_content_returns_skip(self) -> None:
        """Same bytes → same hash → skip (no DB/Qdrant/log write)."""
        content = b"# Hello World\n\ntype: entity\n"
        h = _sha256(content)
        existing = _FakePage(source_mtime_ns=1_000, content_hash=h)
        assert _should_skip_hash(existing, h) is True

    def test_different_content_does_not_skip(self) -> None:
        """Different bytes → different hash → proceed with upsert."""
        old_hash = _sha256(b"old content")
        new_hash = _sha256(b"new content")
        existing = _FakePage(source_mtime_ns=1_000, content_hash=old_hash)
        assert _should_skip_hash(existing, new_hash) is False

    def test_no_existing_row_does_not_skip(self) -> None:
        """New file has no existing row — hash skip cannot fire."""
        new_hash = _sha256(b"new file content")
        assert _should_skip_hash(None, new_hash) is False

    def test_hash_is_sha256_hex(self) -> None:
        """Hash is always a 64-character hex string (SHA-256)."""
        h = _sha256(b"test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_file_has_stable_hash(self) -> None:
        """Empty file produces a deterministic SHA-256 hash."""
        h1 = _sha256(b"")
        h2 = _sha256(b"")
        assert h1 == h2
        assert len(h1) == 64


class TestMtimeThenHashCombined:
    """
    Combined scenario: correct ordering of mtime → hash gates (ADR-0001 full flow).
    Simulates the decision tree without touching any I/O.
    """

    def test_full_flow_unchanged_mtime(self) -> None:
        """Fast-path: mtime same → skip (hash not even checked)."""
        mtime = 5_000_000_000
        content = b"content"
        existing = _FakePage(source_mtime_ns=mtime, content_hash=_sha256(content))

        skip_fast = _should_skip_mtime(existing, mtime)
        assert skip_fast is True
        # hash gate is not reached when fast-path fires

    def test_full_flow_mtime_changed_hash_same(self) -> None:
        """mtime changed (e.g. touch), but bytes identical → touch-mtime skip."""
        old_mtime = 1_000
        new_mtime = 2_000
        content = b"identical content"
        h = _sha256(content)
        existing = _FakePage(source_mtime_ns=old_mtime, content_hash=h)

        skip_fast = _should_skip_mtime(existing, new_mtime)
        assert skip_fast is False  # mtime differs → must check hash

        skip_hash = _should_skip_hash(existing, h)
        assert skip_hash is True  # hash same → touch-mtime only, no upsert

    def test_full_flow_mtime_changed_hash_different(self) -> None:
        """mtime changed AND bytes different → proceed with full ingest."""
        old_mtime = 1_000
        new_mtime = 2_000
        old_content = b"old"
        new_content = b"new"
        existing = _FakePage(source_mtime_ns=old_mtime, content_hash=_sha256(old_content))

        skip_fast = _should_skip_mtime(existing, new_mtime)
        assert skip_fast is False

        skip_hash = _should_skip_hash(existing, _sha256(new_content))
        assert skip_hash is False  # both gates fail → ingest proceeds

    def test_full_flow_new_file(self) -> None:
        """Brand-new file: no existing row → both gates pass through."""
        content = b"brand new"
        skip_fast = _should_skip_mtime(None, 1_000)
        assert skip_fast is False

        skip_hash = _should_skip_hash(None, _sha256(content))
        assert skip_hash is False


class TestFileHashProperties:
    """Properties of the SHA-256 hash used throughout the system."""

    def test_hash_is_deterministic(self) -> None:
        data = b"determinism test " * 100
        assert _sha256(data) == _sha256(data)

    def test_frontmatter_change_detected(self) -> None:
        """
        A frontmatter-only edit must change the hash (ADR-0001 — hash is over raw bytes
        including frontmatter).
        """
        body = b"# My Document\n\nContent here.\n"
        with_type_entity = b"---\ntype: entity\ntitle: Foo\n---\n" + body
        with_type_concept = b"---\ntype: concept\ntitle: Foo\n---\n" + body
        assert _sha256(with_type_entity) != _sha256(with_type_concept)

    def test_whitespace_change_detected(self) -> None:
        """Trailing newline change must be detected."""
        assert _sha256(b"text\n") != _sha256(b"text\n\n")

    def test_cp_preserves_byte_content(self) -> None:
        """
        A copy-with-identical-content (cp -p may change mtime) produces the same hash.
        This is the 'cp -p false-negative hole' described in ADR-0001.
        The hash gate correctly skips it.
        """
        content = b"Important document\n"
        h_original = _sha256(content)
        h_copy = _sha256(content)  # cp -p: bytes identical
        assert h_original == h_copy
        assert _should_skip_hash(_FakePage(content_hash=h_original), h_copy) is True
