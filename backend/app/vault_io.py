"""
vault_io — the single place a file is published into the vault from a temp file (I5).

Every vault write in Synapse is crash-safe: the bytes go to a temp file in the SAME
directory and are then renamed over the destination, so a reader (Obsidian, LiveSync, the
watcher) never observes a truncated file. The rename is what this module owns.

Why this module exists — ``tempfile.mkstemp()`` creates its file with mode ``0600``, and
``os.replace()`` does not copy the destination's mode onto the incoming file: it unlinks the
destination inode and gives its NAME to the temp file, which keeps its own ``0600``. So a
temp-file write does not merely create owner-only files, it DOWNGRADES an existing ``0644``
vault file every time it is rewritten. The backend runs as uid 1000 inside the container
while ``vault/`` is a bind mount shared with Obsidian/LiveSync on the host (CLAUDE.md §1), so
an owner-only file is one the Obsidian side cannot read — a silent I5 violation produced by
an ordinary page edit.

``write_text()``/``write_bytes()`` do NOT have this problem (they create at the process
umask, i.e. ``0644``), which is why the sites that still use them — ``wiki/index.py`` — need
nothing from here, and why the sites converted from ``write_bytes()`` to ``mkstemp()`` for
streaming/atomicity are exactly the ones that did.

Use :func:`atomic_write_bytes` when the whole payload is in hand, and
:func:`publish_tmp_file` when the payload was STREAMED into a temp file the caller opened
itself (an upload body that must never be buffered whole).
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

# ── Mode every file published into the vault carries ─────────────────────────
# 0644: what write_text()/write_bytes() produced under the default umask, and what the
# vault's other readers (Obsidian, LiveSync, host-side backups) need. Named here so the
# literal lives in ONE place instead of being re-derived at each write site — the way it
# was missed at six of seven of them.
VAULT_FILE_MODE: int = 0o644


def _write_all(fd: int, data: bytes) -> None:
    """
    Write ALL of *data* to *fd*, looping until the kernel has accepted every byte.

    ``os.write()`` is a thin wrapper over ``write(2)``, which is allowed to accept FEWER bytes
    than it was offered and report how many it took. A single unchecked ``os.write()`` is
    therefore not "write this payload", it is "offer this payload" — and the bytes it did not
    take are simply lost. On a regular file that happens when the filesystem fills mid-write:
    the kernel writes into the blocks it could allocate and returns the short count instead of
    raising ``ENOSPC``. Nothing in the caller notices, and the next step is the ``os.replace``
    that publishes the temp file — so a truncated page gets committed over the good one,
    atomically and without an error anywhere.

    Raises ``OSError`` if the descriptor stops accepting bytes (a 0-byte return would
    otherwise spin here forever), so the caller's cleanup removes the temp file and the
    destination keeps its previous contents.
    """
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise OSError(
                errno.ENOSPC,
                f"short write: {offset} of {len(view)} bytes accepted before the "
                f"descriptor stopped taking data",
            )
        offset += written


def publish_tmp_file(tmp_name: str | Path, dst: Path) -> None:
    """
    Atomically move *tmp_name* onto *dst* with vault-readable permissions (I5).

    The chmod happens BEFORE the rename, so *dst* is never momentarily readable-by-owner
    only: the rename is the single atomic step that publishes both the bytes and the mode.

    Raises ``OSError`` on failure (the caller decides how to surface it); the temp file is
    left in place for the caller's own cleanup, mirroring what a bare ``os.replace`` did.
    """
    os.chmod(tmp_name, VAULT_FILE_MODE)  # noqa: S103 — vault files are user-readable by design
    os.replace(tmp_name, dst)


def atomic_write_bytes(dst: Path, data: bytes, *, suffix: str) -> None:
    """
    Write *data* to *dst* crash-safely and with vault-readable permissions (I5).

    Creates ``dst.parent`` if needed, writes *data* to a ``mkstemp`` file in that same
    directory (so the rename is same-filesystem and therefore atomic), then publishes it via
    :func:`publish_tmp_file`. *suffix* names the temp file so a leftover is attributable to
    its write site.

    The payload is written through :func:`_write_all`, so a SHORT write (the filesystem
    filling up mid-write) raises instead of silently publishing a truncated page.

    On ANY failure the temp file is removed and the exception propagates — ``dst`` keeps its
    previous contents. The descriptor is closed exactly ONCE: the previous per-site idiom
    closed it in the happy path and closed it AGAIN in the error path, so a failure in the
    rename (the one step that happens after the close) called ``os.close`` on a descriptor
    number the process may already have handed to an unrelated file or socket.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), suffix=suffix)
    closed = False
    try:
        _write_all(tmp_fd, data)
        os.close(tmp_fd)
        closed = True
        publish_tmp_file(tmp_name, dst)
    except Exception:
        if not closed:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        Path(tmp_name).unlink(missing_ok=True)
        raise
