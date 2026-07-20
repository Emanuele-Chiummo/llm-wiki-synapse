"""
ADR-0078 "aggregate ownership" — overview.md ownership contract.

overview.md is NOT regenerated per-document by the ingest pipeline (§3 ownership).
It IS regenerated ONCE per queue-drain via the on_drained callback in app.main
(ADR-0078 refinement, v1.7.0) and on demand via POST /ops/overview/regenerate.
After a successful overwrite data_version is bumped (post-2.1.1; see test_overview_regen.py).

Coverage:
  OWN-01  pipeline.py source contains zero active calls to _update_overview (static).
  OWN-02  pipeline.py _run_orchestrated_blocks source has no _update_overview call (static).
          (2.0.0/ADR-0076: _run_orchestrated is gone; _run_orchestrated_blocks is the only path.)
  OWN-03  POST /ops/overview/regenerate returns 200 with a 'status' field.
  OWN-04  POST /ops/overview/regenerate calls app.ops.overview.regenerate_overview exactly once.
  OWN-05  POST /ops/overview/regenerate is degrade-safe: provider failure → status=degraded,
          no 5xx.
  OWN-06  ops.overview.regenerate_overview delegates to orch._update_overview (queue-drain
          path end-to-end: the thin delegation layer works with origin_source="queue-drain").

Static tests (OWN-01/02) read the source files directly via importlib.util to avoid the
circular import that exists between pipeline.py and orchestrator.py at module initialisation
time.  Runtime tests (OWN-03/04/05/06) use the shared api_client / api_env fixtures from
test_api.py and patch app.ops.overview / app.ingest.orchestrator targets directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ── OWN-01 / OWN-02: static source checks ─────────────────────────────────────


def _pipeline_source() -> str:
    """Return pipeline.py source text without triggering the circular import."""
    spec = importlib.util.find_spec("app.ingest.pipeline")
    assert spec is not None and spec.origin is not None, "app.ingest.pipeline not found"
    return Path(spec.origin).read_text(encoding="utf-8")


def _orchestrator_source() -> str:
    """Return orchestrator.py source text without triggering the circular import."""
    spec = importlib.util.find_spec("app.ingest.orchestrator")
    assert spec is not None and spec.origin is not None, "app.ingest.orchestrator not found"
    return Path(spec.origin).read_text(encoding="utf-8")


def _active_call_sites(source: str, fn_name: str) -> list[str]:
    """Return non-comment, non-docstring lines that call fn_name(…)."""
    token = f"{fn_name}("
    return [
        line.strip()
        for line in source.splitlines()
        if token in line and not line.strip().startswith("#")
    ]


def test_pipeline_source_has_no_update_overview_call() -> None:
    """OWN-01: pipeline.py must not call _update_overview() (ADR-0078 code ownership)."""
    calls = _active_call_sites(_pipeline_source(), "_update_overview")
    assert calls == [], (
        "pipeline.py must not call _update_overview() (ADR-0078).\n"
        "Found active call sites:\n" + "\n".join(calls)
    )


def test_orchestrated_path_source_has_no_update_overview_call() -> None:
    """OWN-02: pipeline.py _run_orchestrated_blocks must not call _update_overview() (ADR-0078).

    2.0.0 / ADR-0076: _run_orchestrated (JSON loop) is gone.  _run_orchestrated_blocks is the
    only orchestrated ingest path.  ADR-0078 ownership still requires it to not call
    _update_overview(); overview.md is the sole responsibility of ops/overview.py.
    """
    source = _pipeline_source()
    # Locate the _run_orchestrated_blocks function body.
    start = source.find("async def _run_orchestrated_blocks(")
    assert start != -1, "_run_orchestrated_blocks must exist in pipeline.py (ADR-0076)"
    rest = source[start:]
    next_fn = rest.find("\nasync def ", 1)
    if next_fn == -1:
        next_fn = rest.find("\ndef ", 1)
    fn_body = rest[:next_fn] if next_fn != -1 else rest

    calls = _active_call_sites(fn_body, "_update_overview")
    assert calls == [], (
        "_run_orchestrated_blocks must not call _update_overview() (ADR-0078).\n"
        "Found active call sites:\n" + "\n".join(calls)
    )


# ── OWN-03 / OWN-04 / OWN-05: endpoint tests ─────────────────────────────────

from tests.test_api import api_client, api_env  # noqa: F401


@pytest.mark.asyncio
async def test_ops_overview_regenerate_endpoint_returns_200(
    api_client: Any,
) -> None:
    """OWN-03: POST /ops/overview/regenerate → 200 with a 'status' field."""
    with patch("app.ops.overview.regenerate_overview", new=AsyncMock()):
        resp = await api_client.post("/ops/overview/regenerate")

    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body, f"expected 'status' in response body, got: {body}"


@pytest.mark.asyncio
async def test_ops_overview_regenerate_calls_regenerate_overview(
    api_client: Any,
) -> None:
    """OWN-04: POST /ops/overview/regenerate calls app.ops.overview.regenerate_overview once."""
    call_log: list[dict[str, Any]] = []

    async def spy_regen(analysis: Any = None, origin_source: str = "") -> None:
        call_log.append({"analysis": analysis, "origin_source": origin_source})

    with patch("app.ops.overview.regenerate_overview", new=spy_regen):
        resp = await api_client.post("/ops/overview/regenerate")

    assert resp.status_code == 200
    assert (
        len(call_log) == 1
    ), f"regenerate_overview must be called exactly once, got {len(call_log)} calls"
    assert call_log[0]["origin_source"] == "manual/ops-overview"


@pytest.mark.asyncio
async def test_ops_overview_regenerate_degrade_safe(
    api_client: Any,
) -> None:
    """OWN-05: POST /ops/overview/regenerate returns 200 status=degraded on provider failure."""

    async def raising_regen(analysis: Any = None, origin_source: str = "") -> None:
        raise RuntimeError("provider boom (test)")

    with patch("app.ops.overview.regenerate_overview", new=raising_regen):
        resp = await api_client.post("/ops/overview/regenerate")

    assert resp.status_code == 200, f"endpoint must never return 5xx; got {resp.status_code}"
    body = resp.json()
    assert (
        body.get("status") == "degraded"
    ), f"expected status=degraded on provider failure, got: {body}"


# ── OWN-06: queue-drain path end-to-end ──────────────────────────────────────

from tests.test_api import api_env  # noqa: F401,F811


@pytest.mark.asyncio
async def test_queue_drain_overview_regen_delegates_to_update_overview(
    api_env: dict[str, Any],
) -> None:
    """OWN-06: ops.overview.regenerate_overview delegates to orch._update_overview, and the
    delegation works correctly when called with origin_source='queue-drain' (the live path
    in app.main._queue_drain_sweep — ADR-0078 refinement).

    This tests the full thin-delegation chain WITHOUT the provider layer:
      regenerate_overview(origin_source="queue-drain") → orch._update_overview(None, "queue-drain")

    We monkeypatch orch._update_overview to capture the call and verify both the delegation
    and the origin_source label are forwarded correctly.
    """
    import app.ingest.orchestrator as _orch
    from app.ops.overview import regenerate_overview

    captured: list[dict[str, Any]] = []

    async def _spy_update_overview(
        analysis: Any,
        origin_source: str,
    ) -> None:
        captured.append({"analysis": analysis, "origin_source": origin_source})

    import unittest.mock as _mock

    with _mock.patch.object(_orch, "_update_overview", side_effect=_spy_update_overview):
        await regenerate_overview(analysis=None, origin_source="queue-drain")

    assert len(captured) == 1, (
        f"regenerate_overview must call orch._update_overview exactly once; "
        f"got {len(captured)} calls"
    )
    assert (
        captured[0]["origin_source"] == "queue-drain"
    ), f"origin_source must be forwarded unchanged; got {captured[0]['origin_source']!r}"
    assert captured[0]["analysis"] is None, "analysis=None must be forwarded (queue-drain path)"
