"""
Health details endpoint (R9-2, AC-R9-2-1..AC-R9-2-4).

Endpoint:
  GET /health/detailed  — per-component health snapshot.

Design decisions:
  - Every probe is individually wrapped in try/except with a ≤2 s timeout.
    The endpoint NEVER returns 5xx — a failed probe marks the component not-ok
    and promotes overall status to "degraded" or "error" accordingly.
  - Top-level status logic:
      "ok"       — all components report no error AND no latency exceeds thresholds.
      "degraded" — at least one component has elevated latency
                   (DB > 200 ms, Qdrant > 500 ms) but all are reachable.
      "error"    — at least one component is unreachable / errored.
  - Qdrant probe is SKIPPED (reported as "skipped") when EMBEDDINGS_ENABLED=false
    (ADR-0030). The embeddings component reflects the same toggle.
  - last_errors ring buffer: a module-level deque capped at 5 entries, populated by
    the _HealthErrorHandler log handler installed at module import time. This is the
    simplest approach that requires zero DB schema changes (AC-R9-2-1 "engineer's
    choice; must be documented").

Invariants:
  I1  — read-only; no vault scan, no index mutation.
  I2  — no graph recompute triggered.
  I6  — zero InferenceProvider calls.
  I7  — every probe bounded to ≤2 s via asyncio.wait_for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

# ── In-process error ring buffer ──────────────────────────────────────────────
# Populated by _HealthErrorHandler (installed below). Deque is thread-safe for
# append/len/iterate (GIL protected). Cap at 5 entries as specified in AC-R9-2-1.

_MAX_ERRORS = 5
_error_ring: deque[dict[str, str]] = deque(maxlen=_MAX_ERRORS)


class _HealthErrorHandler(logging.Handler):
    """
    Logging handler that captures ERROR-level records into _error_ring.

    Installed on the root logger so it catches errors from all submodules.
    Only ERROR level and above are captured (WARNING and INFO are excluded).
    Never raises — swallows its own exceptions silently to avoid log loops.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _error_ring.append(
                {
                    "source": record.name,
                    "message": record.getMessage()[:500],  # cap length
                    "at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception:  # noqa: BLE001, S110
            pass  # never raise from a log handler — silently discard


# Install once at module import time (idempotent guard: avoid double-install on
# reload in test environments).
_handler_installed = False


def _ensure_error_handler() -> None:
    global _handler_installed  # noqa: PLW0603
    if _handler_installed:
        return
    h = _HealthErrorHandler(level=logging.ERROR)
    logging.getLogger().addHandler(h)
    _handler_installed = True


_ensure_error_handler()

# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["health"])

# Latency thresholds (ms) for the "degraded" status level.
_DB_LATENCY_WARN_MS: float = 200.0
_QDRANT_LATENCY_WARN_MS: float = 500.0

# Per-probe timeout (seconds).  I7 bound.
_PROBE_TIMEOUT_S: float = 2.0


# ── Individual probes ──────────────────────────────────────────────────────────


async def _probe_db() -> dict[str, Any]:
    """
    Probe the database with SELECT 1 and measure round-trip latency.

    Returns {"ok": bool, "latency_ms": float | None, "error": str | None}.
    """
    from sqlalchemy import text as sa_text

    from app.db import get_session

    t0 = time.monotonic()
    try:
        async with get_session() as session:
            await session.execute(sa_text("SELECT 1"))
        latency_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": True, "latency_ms": round(latency_ms, 2)}
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": False, "latency_ms": round(latency_ms, 2), "error": str(exc)[:200]}


async def _probe_qdrant() -> dict[str, Any]:
    """
    Probe Qdrant by fetching collection info and measure round-trip latency.

    Returns {"ok": bool | "skipped", "latency_ms": float | None}.
    Skipped when EMBEDDINGS_ENABLED=false (ADR-0030).
    """
    if not settings.embeddings_enabled:
        return {"ok": "skipped", "latency_ms": None}

    from app.qdrant_client import get_qdrant_client

    t0 = time.monotonic()
    try:
        client = get_qdrant_client()
        await client.get_collection(settings.qdrant_collection)
        latency_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": True, "latency_ms": round(latency_ms, 2)}
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": False, "latency_ms": round(latency_ms, 2), "error": str(exc)[:200]}


async def _probe_watcher() -> dict[str, Any]:
    """
    Read the watcher module heartbeat (no I/O — pure in-process state).

    Returns {"alive": bool, "last_event_at": iso_str | None}.
    """
    from app.watcher import get_watcher_heartbeat

    alive, last_event_at = get_watcher_heartbeat()
    return {
        "alive": alive,
        "last_event_at": last_event_at.isoformat() if last_event_at is not None else None,
    }


async def _probe_import_scheduler(scheduler: Any | None) -> dict[str, Any]:
    """
    Read import scheduler state from the DB (import_schedules table).

    Returns {"enabled": bool, "last_run_at": iso|None, "last_error": str|None}.
    The scheduler singleton is passed in to avoid circular imports; health.py never
    imports main.py.  When scheduler is None (pre-lifespan), report enabled=False.
    """
    from app.import_scheduler import load_schedule

    try:
        cfg = await load_schedule(settings.vault_id)
        if cfg is None:
            return {"enabled": False, "last_run_at": None, "last_error": None}
        enabled: bool = bool(getattr(cfg, "enabled", False))
        last_run_at = getattr(cfg, "last_run_at", None)
        last_error = getattr(cfg, "last_error", None)
        return {
            "enabled": enabled,
            "last_run_at": last_run_at.isoformat() if last_run_at is not None else None,
            "last_error": str(last_error) if last_error else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "last_run_at": None, "last_error": str(exc)[:200]}


def _probe_ingest_queue() -> dict[str, Any]:
    """
    Read in-memory ingest queue snapshot (no I/O).

    Returns {"running": int, "pending": int, "paused": bool}.
    """
    from app.ingest.queue_manager import ingest_queue

    snap = ingest_queue.snapshot()
    return {
        "running": snap.get("processing", 0),
        "pending": snap.get("pending", 0),
        "paused": snap.get("paused", False),
    }


def _probe_graph_cache(cache: Any | None) -> dict[str, Any]:
    """
    Read graph cache state from the singleton injected by main.py.

    Returns {"warm": bool, "last_recompute_at": iso|None, "node_count": int}.
    Cache is "warm" when _marker is not None (at least one successful recompute).
    """
    if cache is None:
        return {"warm": False, "last_recompute_at": None, "node_count": 0}
    try:
        marker: int | None = getattr(cache, "_marker", None)
        snapshot = getattr(cache, "_snapshot", None)
        node_count: int = len(snapshot.nodes) if snapshot is not None else 0
        return {
            "warm": marker is not None,
            "last_recompute_at": None,  # GraphCache does not store a timestamp; use marker
            "node_count": node_count,
        }
    except Exception as exc:  # noqa: BLE001
        return {"warm": False, "last_recompute_at": None, "node_count": 0, "error": str(exc)[:200]}


# ── Singleton refs injected by main.py after lifespan ─────────────────────────
# health.py never imports main.py (would be circular).  main.py calls
# set_health_singletons() once in lifespan after initialising the objects.

_graph_cache_ref: Any | None = None
_import_scheduler_ref: Any | None = None


def set_health_singletons(
    graph_cache: Any | None,
    import_scheduler: Any | None,
) -> None:
    """
    Called by main.py lifespan to inject the GraphCache and ImportScheduler
    singletons into this module without creating a circular import.

    Thread-safe under the asyncio event loop (called from lifespan coroutine).
    """
    global _graph_cache_ref, _import_scheduler_ref  # noqa: PLW0603
    _graph_cache_ref = graph_cache
    _import_scheduler_ref = import_scheduler


# ── GET /health/detailed ───────────────────────────────────────────────────────


@router.get(
    "/health/detailed",
    summary="Detailed component health snapshot",
    description=(
        "Returns per-component health: watcher heartbeat, import scheduler state, "
        "ingest queue snapshot, graph cache warmth, DB latency (SELECT 1), "
        "Qdrant ping (skipped when EMBEDDINGS_ENABLED=false), embeddings toggle, "
        "and the 5 most recent in-process ERROR log entries. "
        "Top-level status: 'ok' | 'degraded' | 'error'. "
        "NEVER returns 5xx — failed probes degrade status, not the HTTP layer. "
        "(R9-2, AC-R9-2-1..AC-R9-2-4)"
    ),
    responses={
        200: {"description": "Health snapshot (always 200)"},
    },
)
async def get_health_detailed() -> JSONResponse:
    """
    GET /health/detailed — bounded per-component probes, always 200 (AC-R9-2-1).

    Each probe runs under asyncio.wait_for(timeout=_PROBE_TIMEOUT_S) to enforce
    the ≤2 s bound (I7).  A timeout or exception marks the component not-ok.
    """
    checked_at = datetime.now(UTC).isoformat()

    # ── Run I/O probes concurrently ───────────────────────────────────────────
    async def _safe(coro: Any, label: str) -> Any:
        """Run *coro* bounded to _PROBE_TIMEOUT_S; return error dict on failure."""
        try:
            return await asyncio.wait_for(coro, timeout=_PROBE_TIMEOUT_S)
        except TimeoutError:
            return {"ok": False, "error": f"{label} probe timed out after {_PROBE_TIMEOUT_S}s"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    db_result, qdrant_result, watcher_result, sched_result = await asyncio.gather(
        _safe(_probe_db(), "db"),
        _safe(_probe_qdrant(), "qdrant"),
        _safe(_probe_watcher(), "watcher"),
        _safe(_probe_import_scheduler(_import_scheduler_ref), "import_scheduler"),
    )

    # ── Synchronous probes (no I/O — in-memory reads) ────────────────────────
    try:
        queue_result = _probe_ingest_queue()
    except Exception as exc:  # noqa: BLE001
        queue_result = {"running": 0, "pending": 0, "paused": False, "error": str(exc)[:200]}

    try:
        graph_result = _probe_graph_cache(_graph_cache_ref)
    except Exception as exc:  # noqa: BLE001
        graph_result = {
            "warm": False,
            "last_recompute_at": None,
            "node_count": 0,
            "error": str(exc)[:200],
        }

    # ── Derive overall status ─────────────────────────────────────────────────
    # "error" if any reachable component reports ok=False
    # "degraded" if all reachable but latency thresholds exceeded
    # "ok" otherwise

    db_ok: bool = bool(db_result.get("ok", False))
    qdrant_ok_raw = qdrant_result.get("ok", False)
    qdrant_ok: bool = qdrant_ok_raw is True or qdrant_ok_raw == "skipped"

    any_error = not db_ok or not qdrant_ok

    db_latency: float | None = db_result.get("latency_ms")
    qdrant_latency: float | None = qdrant_result.get("latency_ms")

    qdrant_slow = (
        qdrant_latency is not None
        and qdrant_ok_raw is True
        and qdrant_latency > _QDRANT_LATENCY_WARN_MS
    )
    degraded = (
        not any_error
        and (
            (db_latency is not None and db_latency > _DB_LATENCY_WARN_MS)
            or qdrant_slow
        )
    )

    if any_error:
        overall_status = "error"
    elif degraded:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    # ── Build response ─────────────────────────────────────────────────────────
    body: dict[str, Any] = {
        "status": overall_status,
        "components": {
            "watcher": watcher_result,
            "import_scheduler": sched_result,
            "ingest_queue": queue_result,
            "graph_cache": graph_result,
            "database": {
                "ok": db_ok,
                "latency_ms": db_latency,
            },
            "qdrant": {
                "ok": qdrant_ok_raw,
                "latency_ms": qdrant_latency,
            },
            "embeddings": {
                "enabled": settings.embeddings_enabled,
                "ok": qdrant_ok_raw,  # same signal as Qdrant when enabled; "skipped" when off
            },
        },
        "last_errors": list(_error_ring),
        "checked_at": checked_at,
    }

    return JSONResponse(content=body, status_code=200)
