"""
Unit tests for the watcher ingest concurrency cap (I7, INGEST_MAX_CONCURRENCY).

INFRA-FREE: the ingest seam is monkeypatched with a slow fake that records how many
runs execute at the same instant. No Postgres / Qdrant / embedding service is touched.

Guards the fix for the bulk-drop flood: dropping N files into raw/sources/ must NOT
launch N simultaneous ingests — the semaphore in _MarkdownHandler._run bounds them to
settings.ingest_max_concurrency, parking the surplus until a slot frees (ADR-0001 / I7).
"""

from __future__ import annotations

import asyncio

import pytest
from app.watcher import _MarkdownHandler


class _IngestResultStub:
    def __init__(self) -> None:
        self.status = "completed"
        self.page_id = "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_run_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """At most settings.ingest_max_concurrency _run coroutines execute at once."""
    from app import watcher as watcher_mod

    # Force a small, deterministic cap regardless of env.
    monkeypatch.setattr(watcher_mod.settings, "ingest_max_concurrency", 3, raising=False)

    live = 0
    peak = 0

    async def _fake_ingest_file(_path: str) -> _IngestResultStub:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            # Hold the slot long enough that, without the semaphore, all tasks would
            # overlap and peak would equal the number of files (10).
            await asyncio.sleep(0.05)
        finally:
            live -= 1
        return _IngestResultStub()

    # Patch the seam symbol imported lazily inside _on_ingest.
    monkeypatch.setattr("app.ingest.orchestrator.ingest_file", _fake_ingest_file, raising=True)

    loop = asyncio.get_running_loop()
    handler = _MarkdownHandler(loop)
    # Rebuild the semaphore now that the cap is patched (constructed in __init__).
    handler._sem = asyncio.Semaphore(3)

    # Simulate 10 distinct files arriving "at once".
    paths = [f"/vault/raw/sources/f{i}.md" for i in range(10)]
    tasks = [asyncio.create_task(handler._run(p, "ingest")) for p in paths]
    await asyncio.gather(*tasks)

    assert peak <= 3, f"concurrency exceeded cap: peak={peak}"
    assert peak >= 2, f"expected genuine overlap up to the cap, got peak={peak}"


@pytest.mark.asyncio
async def test_run_releases_inflight_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run clears the per-path in-flight guard even under the semaphore (no leak)."""

    async def _fake_ingest_file(_path: str) -> _IngestResultStub:
        return _IngestResultStub()

    monkeypatch.setattr("app.ingest.orchestrator.ingest_file", _fake_ingest_file, raising=True)

    loop = asyncio.get_running_loop()
    handler = _MarkdownHandler(loop)
    path = "/vault/raw/sources/only.md"
    handler._inflight.add(path)

    await handler._run(path, "ingest")

    assert path not in handler._inflight


def test_semaphore_coerced_to_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured cap of 0/negative is coerced to a usable value (>= 1)."""
    from app import watcher as watcher_mod

    monkeypatch.setattr(watcher_mod.settings, "ingest_max_concurrency", 0, raising=False)

    class _DummyLoop:
        pass

    handler = _MarkdownHandler(_DummyLoop())  # type: ignore[arg-type]
    # Semaphore with 1 permit is acquirable once without blocking.
    assert handler._sem._value >= 1
