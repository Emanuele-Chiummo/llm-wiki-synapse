"""
Watchdog-based incremental file watcher (I1, ADR-0001).

Watches vault/raw/sources/ for text file CREATE / MODIFY / DELETE events.
Accepted extensions are the SAME allow-list as the upload endpoint:
  app.upload._ALLOWED_EXTENSIONS  → {".md", ".txt", ".markdown"}
(single source of truth — never duplicate the list here).

On each event, delegates to the ingest seam (ADR-0003):
  CREATE/MODIFY → ingest_file(path)
  DELETE        → delete_file(path)

NEVER enumerates the watched directory (no os.listdir, no glob, no rglob).
Startup registers handlers ONLY — no startup rescan (I1, AQ-3 / ADR-0006).
Contains NO direct Postgres/Qdrant/embedding code — all writes go through the seam.

Startup INFO line (AQ-3 / ADR-0006): when raw/sources/ is non-empty at startup,
exactly one INFO log line is emitted stating that pre-existing files are NOT
auto-indexed and how to index them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from app.config import settings

# Single source of truth for accepted extensions (shared with upload.py, import_scheduler.py).
# Import lazily to avoid circular imports at module load; accessed only in _is_text_file().
logger = logging.getLogger(__name__)

# ── Event handler ──────────────────────────────────────────────────────────────


class _MarkdownHandler(FileSystemEventHandler):
    """
    Handle watchdog FS events for text files under vault/raw/sources/.

    Accepted extensions mirror app.upload._ALLOWED_EXTENSIONS: .md, .txt, .markdown.
    Uses the running asyncio event loop to schedule coroutines from the watchdog
    thread (watchdog runs in its own OS thread; asyncio runs in the main thread).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = loop

    # ── watchdog callbacks (called from watchdog thread) ──────────────────────

    def on_created(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._schedule(self._on_ingest(src))

    def on_modified(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._schedule(self._on_ingest(src))

    def on_deleted(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._schedule(self._on_delete(src))

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle atomic-rename saves (many editors use rename-on-save)."""
        if not isinstance(event, FileMovedEvent):
            return
        # Treat dst as a modified file; treat src (now gone) as deleted
        dst = str(event.dest_path)
        src = str(event.src_path)
        if _is_text_file(dst):
            self._schedule(self._on_ingest(dst))
        if _is_text_file(src):
            self._schedule(self._on_delete(src))

    # ── Async delegates ────────────────────────────────────────────────────────

    async def _on_ingest(self, src_path: str) -> None:
        """Delegate to the seam — no direct DB/Qdrant code here (ADR-0003)."""
        # Import here to avoid circular imports at module load time
        from app.ingest.orchestrator import ingest_file

        try:
            result = await ingest_file(src_path)
            logger.info(
                "watcher: %s page_id=%s path=%s",
                result.status,
                result.page_id,
                src_path,
            )
        except FileNotFoundError:
            # Race: file deleted between event and read — silently ignore
            logger.debug("watcher: file vanished before ingest %s", src_path)
        except Exception:  # noqa: BLE001
            logger.exception("watcher: ingest error for %s", src_path)

    async def _on_delete(self, src_path: str) -> None:
        """Delegate to the seam — no direct DB/Qdrant code here (ADR-0003)."""
        from app.ingest.orchestrator import delete_file

        try:
            await delete_file(src_path)
            logger.info("watcher: deleted %s", src_path)
        except Exception:  # noqa: BLE001
            logger.exception("watcher: delete error for %s", src_path)

    def _schedule(self, coro: object) -> None:
        """Thread-safely schedule a coroutine on the asyncio event loop."""
        import asyncio as _asyncio

        _asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]


# ── Observer lifecycle ─────────────────────────────────────────────────────────


class VaultWatcher:
    """
    Manages the watchdog Observer lifecycle.

    start() registers handlers only — never walks the directory (I1, AC-WATCH-5).
    stop() is called on application shutdown.
    """

    def __init__(self) -> None:
        self._observer: Any = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Start the watchdog observer on vault/raw/sources/.

        Emits the AQ-3 INFO line if the directory is non-empty at startup (ADR-0006).
        Does NOT enumerate files or trigger any ingest on startup (I1).
        """
        watch_dir = settings.raw_sources_dir
        watch_dir.mkdir(parents=True, exist_ok=True)

        # ── AQ-3 startup notice (ADR-0006) ────────────────────────────────────
        # We check only whether the directory has *any* entries (os.scandir
        # with next() is O(1) — NOT a full enumeration, just a non-empty check).
        self._emit_startup_notice(watch_dir)

        handler = _MarkdownHandler(loop)
        observer = Observer()
        observer.schedule(handler, str(watch_dir), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("watcher: observer started on %s", watch_dir)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("watcher: observer stopped")

    @staticmethod
    def _emit_startup_notice(watch_dir: Path) -> None:
        """
        Emit exactly one INFO line if vault/raw/sources/ is non-empty.

        Uses os.scandir + next() — O(1) existence check, NOT a directory walk (I1).
        The notice is informational only; no DB write, no ingest triggered (ADR-0006).
        """
        try:
            with os.scandir(watch_dir) as it:
                next(it)
                # Directory has at least one entry
                logger.info(
                    "startup: watching %s; pre-existing files are NOT auto-indexed (I1). "
                    "Use POST /ingest/trigger to index them.",
                    watch_dir,
                )
        except StopIteration:
            # Directory is empty — no notice needed
            pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_watcher = VaultWatcher()


def start_watcher(loop: asyncio.AbstractEventLoop) -> None:
    """Start the module-level watcher (called from main.py lifespan)."""
    _watcher.start(loop)


def stop_watcher() -> None:
    """Stop the module-level watcher (called from main.py lifespan shutdown)."""
    _watcher.stop()


# ── Utility ────────────────────────────────────────────────────────────────────


def _is_text_file(path: str) -> bool:
    """
    Return True if *path* has an extension in the upload allow-list.

    Imports app.upload._ALLOWED_EXTENSIONS as the single source of truth so
    upload.py, import_scheduler.py, and the watcher all share one definition.
    """
    from app.upload import _ALLOWED_EXTENSIONS  # lazy — avoids circular import

    return Path(path).suffix.lower() in _ALLOWED_EXTENSIONS
