"""
ADR-0078 "aggregate ownership" — overview.md must NOT be touched by ingest.

Coverage:
  OWN-01  pipeline.py source contains zero active calls to _update_overview (static).
  OWN-02  orchestrator.py _run_orchestrated source has no _update_overview call (static).
  OWN-03  POST /ops/overview/regenerate returns 200 with a 'status' field.
  OWN-04  POST /ops/overview/regenerate calls app.ops.overview.regenerate_overview exactly once.
  OWN-05  POST /ops/overview/regenerate is degrade-safe: provider failure → status=degraded,
          no 5xx.

Static tests (OWN-01/02) read the source files directly via importlib.util to avoid the
circular import that exists between pipeline.py and orchestrator.py at module initialisation
time.  Runtime tests (OWN-03/04/05) use the shared api_client fixture from test_api.py
and patch app.ops.overview.regenerate_overview directly.
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
    """OWN-02: orchestrator.py _run_orchestrated must not call _update_overview() (ADR-0078)."""
    source = _orchestrator_source()
    # Locate the _run_orchestrated function body (starts at 'async def _run_orchestrated').
    start = source.find("async def _run_orchestrated(")
    # Find the next top-level function after it (next 'async def ' or 'def ' at column 0).
    rest = source[start:]
    next_fn = rest.find("\nasync def ", 1)
    if next_fn == -1:
        next_fn = rest.find("\ndef ", 1)
    fn_body = rest[:next_fn] if next_fn != -1 else rest

    calls = _active_call_sites(fn_body, "_update_overview")
    assert calls == [], (
        "_run_orchestrated must not call _update_overview() (ADR-0078).\n"
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
