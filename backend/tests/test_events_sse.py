"""
Tests for GET /events — the SSE push channel (1.9.3 W1, FE-RT-2).

Coverage:
  T-EVT-001  event_stream() emits a data_version event as the very first thing (baseline
             resync on connect/reconnect — no history needed for current-state signals).
  T-EVT-002  event_stream() emits a queue event alongside the baseline data_version event.
  T-EVT-003  SSE frame format: `id:` / `event:` / `data:` lines, blank-line terminated,
             `data:` is valid JSON.
  T-EVT-004  No further event is emitted while nothing changes; a `: heartbeat` comment
             appears instead once the heartbeat interval elapses.
  T-EVT-005  A data_version bump between ticks produces a new data_version event with an
             incremented sequence id and the new value.
  T-EVT-006  The generator ends cleanly (no exception) once request.is_disconnected()
             flips True (I7 — bounded, no orphaned task).
  T-EVT-007  The generator ends cleanly once EVENTS_MAX_STREAM_SECONDS elapses, even
             with no disconnect (I7 — every loop is bounded).
  T-EVT-008  GET /events (full ASGI route) returns 200 + text/event-stream and the first
             SSE frame is well-formed, using the api_env/api_client fixtures.

Approach: T-EVT-001..007 drive `event_stream()` directly against a minimal fake Request
(no ASGI transport needed) with tiny monkeypatched poll/heartbeat/max-stream settings —
fast, deterministic, no timing flakiness. T-EVT-008 is a thin integration check against
the real route via httpx streaming, reusing the api_env/api_client fixtures from
test_api.py (SQLite in-memory, no live infra).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from app.routers.events import event_stream

# Re-use the shared fixtures from test_api.py (auto-discovered by conftest.py)
from tests.test_api import api_client, api_env  # noqa: F401

# ── Fake Request ────────────────────────────────────────────────────────────────


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only `is_disconnected()` is used."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        if self._disconnect_after is None:
            return False
        return self._calls > self._disconnect_after


def _parse_frame(frame: str) -> dict[str, Any]:
    """Parse one SSE frame into {id, event, data} — asserts the required lines are present."""
    lines = [ln for ln in frame.split("\n") if ln != ""]
    parsed: dict[str, Any] = {}
    for line in lines:
        if line.startswith("id: "):
            parsed["id"] = line[len("id: ") :]
        elif line.startswith("event: "):
            parsed["event"] = line[len("event: ") :]
        elif line.startswith("data: "):
            parsed["data"] = json.loads(line[len("data: ") :])
    return parsed


async def _collect(agen: Any, n: int) -> list[str]:
    """Collect exactly n yielded frames from the async generator."""
    out: list[str] = []
    async for frame in agen:
        out.append(frame)
        if len(out) >= n:
            break
    return out


# ── T-EVT-001/002/003: baseline resync + frame format ───────────────────────────


class TestBaselineResync:
    async def test_first_two_frames_are_data_version_then_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-EVT-001/002: on connect, both signals are sent immediately (nothing withheld)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 0.05)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([3]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        frames = await _collect(event_stream(req), 2)  # type: ignore[arg-type]

        f0 = _parse_frame(frames[0])
        f1 = _parse_frame(frames[1])
        assert f0["event"] == "data_version"
        assert f0["data"] == {"data_version": 3}
        assert f1["event"] == "queue"
        assert f1["data"] == _QUEUE_BASE

    async def test_frame_format_ids_and_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-EVT-003: every frame has id/event/data lines, and data is valid JSON."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 0.05)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([7]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        frames = await _collect(event_stream(req), 1)  # type: ignore[arg-type]
        frame = frames[0]

        assert frame.endswith("\n\n")
        parsed = _parse_frame(frame)
        assert "id" in parsed and ":" in parsed["id"]
        assert parsed["event"] == "data_version"
        assert isinstance(parsed["data"], dict)


# ── T-EVT-004: heartbeat while idle ──────────────────────────────────────────────


class TestHeartbeat:
    async def test_heartbeat_emitted_when_nothing_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-EVT-004: after the baseline pair, a stable heartbeat comment appears — no
        duplicate data_version/queue events while nothing changed."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.005)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 0.05)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([9]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        # baseline data_version + queue, then wait for a heartbeat.
        frames = await _collect(event_stream(req), 3)  # type: ignore[arg-type]
        assert frames[0].startswith("id: ") and "event: data_version" in frames[0]
        assert frames[1].startswith("id: ") and "event: queue" in frames[1]
        assert frames[2] == ": heartbeat\n\n"


# ── T-EVT-005: change detection on data_version bump ────────────────────────────


class TestChangeDetection:
    async def test_data_version_bump_emits_new_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-EVT-005: a later tick's changed data_version produces a fresh event."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 5.0)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([1, 1, 1, 2]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        # frame0: data_version=1, frame1: queue baseline, frame2: data_version=2 (bump)
        frames = await _collect(event_stream(req), 3)  # type: ignore[arg-type]
        f0 = _parse_frame(frames[0])
        f2 = _parse_frame(frames[2])
        assert f0["data"] == {"data_version": 1}
        assert f2["event"] == "data_version"
        assert f2["data"] == {"data_version": 2}
        # sequence portion of the id must have advanced.
        assert f2["id"] != f0["id"]


# ── T-EVT-006/007: bounded termination ──────────────────────────────────────────


class TestBoundedTermination:
    async def test_ends_cleanly_on_disconnect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-EVT-006: generator returns (no exception) once is_disconnected() → True."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 5.0)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([1]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        # Disconnect flips True after the first is_disconnected() check (before any frame).
        req = _FakeRequest(disconnect_after=0)
        frames: list[str] = []
        async for frame in event_stream(req):  # type: ignore[arg-type]
            frames.append(frame)
        assert frames == []

    async def test_ends_cleanly_after_max_stream_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-EVT-007: bounded overall duration (I7) — the stream self-terminates."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 5.0)
        # Effectively zero — the very first loop iteration's elapsed-time check must trip.
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", -1.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([1]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        frames: list[str] = []
        async for frame in event_stream(req):  # type: ignore[arg-type]
            frames.append(frame)
        assert frames == []

    async def test_cancellation_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cancelling the consuming task must propagate CancelledError, not swallow it
        (I7 — no orphaned task; the route relies on this to unwind cleanly)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 1.0)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 5.0)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 30.0)
        monkeypatch.setattr("app.routers.events._read_data_version", _make_dv_reader([1]))
        monkeypatch.setattr("app.routers.events._queue_counts", lambda: _QUEUE_BASE.copy())

        req = _FakeRequest()
        agen = event_stream(req)  # type: ignore[arg-type]

        async def _consume() -> None:
            async for _ in agen:
                pass

        task = asyncio.ensure_future(_consume())
        await asyncio.sleep(0)  # let it start and yield the baseline frames
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── T-EVT-008: full-route integration smoke test ────────────────────────────────


class TestEventsRouteIntegration:
    async def test_get_events_returns_sse_stream(
        self, api_client: Any, api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-EVT-008: GET /events returns 200, text/event-stream, well-formed first frame."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "events_poll_interval_seconds", 0.01)
        monkeypatch.setattr(cfg.settings, "events_heartbeat_interval_seconds", 5.0)
        monkeypatch.setattr(cfg.settings, "events_max_stream_seconds", 5.0)

        seen_event = False
        async with api_client.stream("GET", "/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                if "\n\n" in buf:
                    frame, _, _rest = buf.partition("\n\n")
                    parsed = _parse_frame(frame + "\n\n")
                    assert parsed.get("event") in {"data_version", "queue"}
                    assert "data" in parsed
                    seen_event = True
                    break
        assert seen_event


# ── Shared helpers/fixtures used across the classes above ──────────────────────

_QUEUE_BASE: dict[str, Any] = {
    "paused": False,
    "pending": 0,
    "processing": 0,
    "failed": 0,
    "completed_since_idle": 0,
    "total": 0,
}


def _make_dv_reader(values: list[int]):
    """Returns an async function that yields successive values from `values`, then
    repeats the last one forever (so tests don't need to know exactly how many ticks
    a generator loop performs internally)."""
    state = {"i": 0}

    async def _reader() -> int:
        i = min(state["i"], len(values) - 1)
        state["i"] += 1
        return values[i]

    return _reader
