"""
Per-domain APIRouter: GET /events — Server-Sent Events (SSE) push channel.

1.9.3 W1 (FE-RT-2, ADR audit v2.0 finding). Additive, non-breaking: this is a SECOND
channel that runs alongside the existing REST pollers (/status, /ingest/queue, run-lists,
importScheduleStore, researchStore, ingest-all, ConvertPanel, HomeDashboard synthesize
status) — none of those are removed or disabled server-side. The frontend decides whether
to slow its own poll cadence while this stream is connected and healthy (fallback story
lives in ``frontend/src/store/eventsStore.ts``).

Design (I1/I2/I3/I7):
  * The generator polls INTERNAL, lightweight server-side state on a bounded cadence
    (``EVENTS_POLL_INTERVAL_SECONDS``) — one indexed Postgres row read for ``data_version``
    (VaultState) and one in-memory dict read for the ingest queue counters
    (``app.ingest.queue_manager.ingest_queue.snapshot()``, no DB scan — ADR-0046 §6). This is
    NOT a full vault rescan (I1) and does not run any heavy computation (I2/I7).
  * An SSE event is emitted ONLY when a watched value changed since the last one sent —
    this is a change-driven push, never a continuous blast.
  * A periodic SSE comment heartbeat (``: heartbeat\\n\\n``) keeps the connection alive
    through proxies/tunnels that drop idle connections. The real deployment sits behind
    BOTH Cloudflare Access and Tailscale — both are documented assumptions below.
  * ``Last-Event-ID`` is accepted (SSE auto-resend on reconnect) though this channel carries
    current-state signals (data_version / queue counters), not a durable event log: the
    first tick after (re)connect always sends the current values of both signals
    regardless of what changed, so a reconnecting client is immediately resynced without
    needing to replay history.
  * Bounded (I7): the connection has a hard wall-clock cap (``EVENTS_MAX_STREAM_SECONDS``,
    default 30 min) after which the generator ends cleanly; the browser's ``EventSource``
    reconnects automatically (with ``Last-Event-ID``), so this is an invisible periodic
    reconnect, not an outage. The generator also exits promptly on client disconnect
    (``request.is_disconnected()`` polled each tick) and propagates
    ``asyncio.CancelledError`` — no orphaned background task survives the connection.

Event shapes (see docs/api/openapi.json + docs/sequences for the diagram):
    id: <data_version>:<seq>
    event: data_version
    data: {"data_version": <int>}

    id: <data_version>:<seq>
    event: queue
    data: {"paused": <bool>, "pending": <int>, "processing": <int>,
           "failed": <int>, "completed_since_idle": <int>, "total": <int>}

Auth: gated by the same app-level ``SynapseAuthMiddleware`` bearer-token gate as every
other non-exempt route (ADR-0052) — no per-route ``Depends``, by construction (see
``app/auth.py`` Do-NOTs §9).

Proxy/tunnel assumptions worth verifying at live-test time (flagged per the sprint brief):
  * Cloudflare Access / Cloudflare Tunnel must not buffer the response — ``X-Accel-Buffering:
    no`` is sent (the same header the existing NDJSON chat stream sends) but Cloudflare's own
    edge can still buffer SSE for some plans/configurations; if events appear delayed in
    bursts rather than as they occur, check Cloudflare's buffering/streaming settings.
  * Cloudflare Access session/idle timeouts and Tailscale's own connection idle behaviour are
    both mitigated by the heartbeat, but neither has been exercised against the real tunnel
    in this change — only against the FastAPI test client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import StreamingResponse

from app import runtime_state
from app.config import settings
from app.ingest.queue_manager import ingest_queue
from app.models import VaultState

logger = logging.getLogger(__name__)

router = APIRouter()

# The subset of ingest_queue.snapshot() fields relevant to a lightweight "is anything
# happening" signal. Deliberately excludes `tasks` (list, changes shape on every reorder)
# and ETA (requires a DB read over ingest_runs) — those stay on the existing GET
# /ingest/queue poller, which the frontend can slow down but not remove (fallback story).
_QUEUE_KEYS: tuple[str, ...] = (
    "paused",
    "pending",
    "processing",
    "failed",
    "completed_since_idle",
    "total",
)


def _queue_counts() -> dict[str, Any]:
    """Cheap in-memory queue snapshot — no DB scan, no ETA computation (I1/I7/ADR-0046 §6)."""
    snap = ingest_queue.snapshot()
    return {k: snap[k] for k in _QUEUE_KEYS}


async def _read_data_version() -> int:
    """Single indexed row read — mirrors GET /status (I1: no vault scan)."""
    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState.data_version).where(VaultState.vault_id == settings.vault_id)
        )
        value = row.scalar_one_or_none()
        return int(value) if value is not None else 0


def _sse(event: str, event_id: str, payload: dict[str, Any]) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(payload)}\n\n"


async def event_stream(request: Request) -> AsyncGenerator[str, None]:
    """
    Bounded (I7), change-driven SSE generator — see module docstring for the full contract.

    Split out from the route function so it can be unit-tested directly (with a fake
    ``Request``) without spinning up the full ASGI transport.
    """
    seq = 0
    started = time.monotonic()
    last_heartbeat = started
    last_data_version: int | None = None
    last_queue: dict[str, Any] | None = None

    try:
        while True:
            if await request.is_disconnected():
                logger.debug("GET /events: client disconnected — ending stream")
                return
            if time.monotonic() - started > settings.events_max_stream_seconds:
                logger.debug(
                    "GET /events: EVENTS_MAX_STREAM_SECONDS reached — ending stream "
                    "(client EventSource will reconnect)"
                )
                return

            changed = False

            try:
                dv = await _read_data_version()
            except Exception:  # noqa: BLE001 — degrade-safe: skip this tick, stream stays alive
                logger.debug("GET /events: data_version read failed this tick", exc_info=True)
                dv = None
            if dv is not None and dv != last_data_version:
                last_data_version = dv
                seq += 1
                yield _sse("data_version", f"{dv}:{seq}", {"data_version": dv})
                changed = True

            try:
                qc = _queue_counts()
            except Exception:  # noqa: BLE001
                logger.debug("GET /events: queue snapshot failed this tick", exc_info=True)
                qc = None
            if qc is not None and qc != last_queue:
                last_queue = qc
                seq += 1
                yield _sse("queue", f"{last_data_version or 0}:{seq}", qc)
                changed = True

            now = time.monotonic()
            if changed:
                last_heartbeat = now
            elif now - last_heartbeat >= settings.events_heartbeat_interval_seconds:
                last_heartbeat = now
                yield ": heartbeat\n\n"

            await asyncio.sleep(settings.events_poll_interval_seconds)
    except asyncio.CancelledError:
        logger.debug("GET /events: stream cancelled (client disconnect or server shutdown)")
        raise


@router.get(
    "/events",
    summary="Server-Sent Events push channel — data_version + ingest queue state",
    description=(
        "SSE stream (text/event-stream) pushing `data_version` bumps and ingest-queue "
        "counter changes as they happen (1.9.3 W1, FE-RT-2). Change-driven, not a "
        "continuous blast; periodic comment heartbeats keep the connection alive through "
        "proxies/tunnels. Supports `Last-Event-ID` for resume-on-reconnect (the first tick "
        "after connect always resends current state). Bounded to "
        "EVENTS_MAX_STREAM_SECONDS per connection (I7); EventSource reconnects "
        "automatically. Existing REST pollers (/status, /ingest/queue, etc.) remain the "
        "permanent fallback and are never disabled by this endpoint."
    ),
    responses={200: {"description": "SSE stream", "content": {"text/event-stream": {}}}},
)
async def get_events(request: Request) -> StreamingResponse:
    """GET /events — see module docstring for the full contract."""
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={
            # Mirrors the existing NDJSON chat stream's anti-buffering headers
            # (app/routers/chat.py) — same rationale, SSE proxies/tunnels included.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
