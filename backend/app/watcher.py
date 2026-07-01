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
from app.ingest.queue_manager import ingest_queue

# Single source of truth for accepted extensions (shared with upload.py, import_scheduler.py).
# Import lazily to avoid circular imports at module load; accessed only in _is_text_file().
logger = logging.getLogger(__name__)

# Per-path debounce window. A single save commonly emits a burst of FS events
# (create + several modifies; Docker bind-mounts on macOS amplify this), and an
# atomic-rename save adds a moved event. Without coalescing, each event launched a
# separate ingest → concurrent runs racing on uix_pages_vault_file_path_live (I1).
# Each event re-arms the timer; exactly one ingest fires after the quiet period.
_DEBOUNCE_SECONDS = float(os.environ.get("WATCH_DEBOUNCE_SECONDS", "1.5"))

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
        # All three maps are touched ONLY on the loop thread (watchdog callbacks hop
        # over via call_soon_threadsafe → _arm), so no lock is needed.
        # path → pending debounce timer (collapses a same-burst event storm).
        self._pending: dict[str, asyncio.TimerHandle] = {}
        # paths with an ingest/delete currently executing (in-flight guard).
        self._inflight: set[str] = set()
        # path → latest action that arrived WHILE an ingest was in-flight; replayed
        # exactly once when that ingest finishes. macOS↔Docker bind-mounts deliver
        # fsnotify events in waves seconds apart — beyond the debounce window but
        # during the (slow, provider-bound) ingest. Without this guard each wave
        # started a concurrent run racing on uix_pages_vault_file_path_live (I1).
        self._dirty: dict[str, str] = {}

    # ── watchdog callbacks (called from watchdog thread) ──────────────────────

    def on_created(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._debounce(src, "ingest")

    def on_modified(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._debounce(src, "ingest")

    def on_deleted(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        if not event.is_directory and _is_text_file(src):
            self._debounce(src, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle atomic-rename saves (many editors use rename-on-save)."""
        if not isinstance(event, FileMovedEvent):
            return
        # Treat dst as a modified file; treat src (now gone) as deleted
        dst = str(event.dest_path)
        src = str(event.src_path)
        if _is_text_file(dst):
            self._debounce(dst, "ingest")
        if _is_text_file(src):
            self._debounce(src, "delete")

    # ── Per-path debounce (coalesces FS event bursts into one action) ─────────

    def _debounce(self, path: str, action: str) -> None:
        """Re-arm the per-path timer (from watchdog thread → hop to loop thread)."""
        self._loop.call_soon_threadsafe(self._arm, path, action)

    def _arm(self, path: str, action: str) -> None:
        """Cancel any pending timer for *path* and schedule the latest action."""
        existing = self._pending.pop(path, None)
        if existing is not None:
            existing.cancel()
        self._pending[path] = self._loop.call_later(_DEBOUNCE_SECONDS, self._fire, path, action)

    def _fire(self, path: str, action: str) -> None:
        """Quiet period elapsed — launch the coalesced ingest/delete once per path."""
        self._pending.pop(path, None)

        # ── ADR-0046 §3: cancel-suppression check ────────────────────────────
        # Drop FS events for paths currently in the cancel-suppression window
        # (post-cancel cascade_delete mutations / editor re-touch).
        if ingest_queue.should_skip(path):
            logger.debug("watcher: suppressing event for cancelled path %s", path)
            return

        # ── ADR-0046 §4: pause gate ───────────────────────────────────────────
        # If the queue is paused, admit() parks the event and returns False.
        if not ingest_queue.admit(path, action):
            logger.debug("watcher: admit returned False (paused) — parked path=%s", path)
            return

        if path in self._inflight:
            # An ingest for this path is still running; remember the latest action
            # and replay it exactly once when that run finishes (coalesced trailing).
            self._dirty[path] = action
            return
        self._inflight.add(path)
        self._loop.create_task(self._run(path, action))

    async def _run(self, path: str, action: str) -> None:
        """Execute one action, then replay a single trailing action if one queued."""
        try:
            if action == "delete":
                await self._on_delete(path)
            else:
                await self._on_ingest(path)
        finally:
            self._inflight.discard(path)
            queued = self._dirty.pop(path, None)
            if queued is not None:
                self._arm(path, queued)

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
        # ── ADR-0046 §4/§5: register handler so queue_manager can call _arm for resume/retry ──
        ingest_queue.set_watcher_handler(handler)

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
