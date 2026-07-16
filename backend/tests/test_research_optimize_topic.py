"""
B5/D3 — Deep Research pre-run topic optimization tests.

Covers ops.deep_research.optimize_topic (unit) + POST /research/optimize-topic (API):

  T-OPT-001  optimize_topic returns {optimized_topic, queries} from a mocked provider chat
  T-OPT-002  no-provider graceful fallback → {optimized_topic: topic, queries: [topic]}
  T-OPT-003  bounds: SINGLE provider.chat() call (no loop), asyncio.wait_for timeout → fallback
  T-OPT-004  provider error → graceful fallback (never raises)
  T-OPT-005  garbled/empty response → naive fallback
  T-OPT-006  queries clamped to <= _OPTIMIZE_MAX_QUERIES and padded to >= _OPTIMIZE_MIN_QUERIES
  T-OPT-007  cost logged from the run-scoped accumulator (I7)
  T-OPT-008  POST /research/optimize-topic 200 {optimized_topic, queries} (mocked provider)
  T-OPT-009  POST /research/optimize-topic no-provider → 200 echo fallback (NOT 500/503)
  T-OPT-010  POST /research/optimize-topic 422 for empty topic
  T-OPT-011  POST /research/start accepts optional queries → seed_queries passthrough (bounded)

NEVER hits a real LLM: the provider chat() is always a mock async generator.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Provider mock helper ──────────────────────────────────────────────────────


def _make_optimize_provider(response: str, *, call_counter: list[int] | None = None) -> Any:
    """
    Mock InferenceProvider whose chat() yields `response` in one chunk and counts calls.

    Mirrors the mock shape used in test_deep_research.py so optimize_topic can bind an
    accumulator and iterate the async generator exactly as the real seam does.
    """
    provider = MagicMock()
    counter = call_counter if call_counter is not None else [0]

    async def mock_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        counter[0] += 1

        async def _gen() -> AsyncIterator[str]:
            yield response

        return _gen()

    provider.chat = mock_chat
    provider._chat_calls = counter

    def bind_acc(acc: Any) -> None:
        provider._accumulator = acc
        provider._bound_acc = acc

    provider.bind_accumulator = MagicMock(side_effect=bind_acc)
    provider._accumulator = None
    return provider


# ── T-OPT-001: optimize_topic parses provider response ────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_parses_provider_response() -> None:
    """T-OPT-001: optimize_topic returns {optimized_topic, queries} from mocked chat."""
    from app.ops.deep_research import optimize_topic

    response = (
        "TOPIC: Kubernetes container networking with Calico\n"
        "QUERIES:\n"
        "Calico CNI BGP mode\n"
        "Kubernetes NetworkPolicy Calico\n"
        "Calico vs Cilium eBPF\n"
    )
    provider = _make_optimize_provider(response)

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch(
            "app.ops.deep_research._load_research_vault_context",
            return_value="# purpose.md\nHomelab knowledge base.",
        ),
    ):
        result = await optimize_topic(vault_id="v", topic="networking")

    assert result.optimized_topic == "Kubernetes container networking with Calico"
    assert result.queries == [
        "Calico CNI BGP mode",
        "Kubernetes NetworkPolicy Calico",
        "Calico vs Cilium eBPF",
    ]
    # Single bounded call (I7)
    assert provider._chat_calls[0] == 1


# ── T-OPT-002: no-provider graceful fallback ──────────────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_no_provider_fallback() -> None:
    """T-OPT-002: no provider → naive fallback echoing the seed topic (never raises)."""
    from app.ops.deep_research import optimize_topic

    with patch(
        "app.ops.deep_research.resolve_operation_provider", new=AsyncMock(return_value=None)
    ):
        result = await optimize_topic(vault_id="v", topic="  homelab backups  ")

    assert result.optimized_topic == "homelab backups"
    assert result.queries == ["homelab backups"]


# ── T-OPT-003: single call + timeout → fallback ───────────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_timeout_falls_back() -> None:
    """T-OPT-003: I7 — provider timeout degrades to fallback, no exception, single call."""
    from app import config as cfg
    from app.ops.deep_research import optimize_topic

    provider = MagicMock()

    async def slow_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        import asyncio

        async def _gen() -> AsyncIterator[str]:
            await asyncio.sleep(5)  # far longer than the patched 0.01s timeout
            yield "TOPIC: never\nQUERIES:\nnever"

        return _gen()

    provider.chat = slow_chat
    provider.bind_accumulator = MagicMock()
    provider._accumulator = None

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
        patch.object(cfg.settings, "deep_research_optimize_timeout_seconds", 0.01),
    ):
        result = await optimize_topic(vault_id="v", topic="ceph tuning")

    assert result.optimized_topic == "ceph tuning"
    assert result.queries == ["ceph tuning"]


# ── T-OPT-004: provider error → fallback ──────────────────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_provider_error_falls_back() -> None:
    """T-OPT-004: provider raising inside chat → graceful fallback (never propagates)."""
    from app.ops.deep_research import optimize_topic

    provider = MagicMock()

    async def boom_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        raise RuntimeError("provider exploded")

    provider.chat = boom_chat
    provider.bind_accumulator = MagicMock()
    provider._accumulator = None

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
    ):
        result = await optimize_topic(vault_id="v", topic="zfs snapshots")

    assert result.optimized_topic == "zfs snapshots"
    assert result.queries == ["zfs snapshots"]


# ── T-OPT-005: garbled/empty response → naive fallback ────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_empty_response_falls_back() -> None:
    """T-OPT-005: empty provider response degrades to the naive fallback."""
    from app.ops.deep_research import optimize_topic

    provider = _make_optimize_provider("   \n  \n")

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
    ):
        result = await optimize_topic(vault_id="v", topic="grafana dashboards")

    assert result.optimized_topic == "grafana dashboards"
    assert result.queries == ["grafana dashboards"]


# ── T-OPT-006: query bounds (clamp high, pad low) ─────────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_clamps_too_many_queries() -> None:
    """T-OPT-006a: > _OPTIMIZE_MAX_QUERIES returned → clamped to the max."""
    from app.ops.deep_research import _OPTIMIZE_MAX_QUERIES, optimize_topic

    lines = "\n".join(f"query {i}" for i in range(12))
    provider = _make_optimize_provider(f"TOPIC: big topic\nQUERIES:\n{lines}")

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
    ):
        result = await optimize_topic(vault_id="v", topic="seed")

    assert len(result.queries) == _OPTIMIZE_MAX_QUERIES


@pytest.mark.asyncio
async def test_optimize_topic_pads_too_few_queries() -> None:
    """T-OPT-006b: < _OPTIMIZE_MIN_QUERIES returned → padded up to the minimum."""
    from app.ops.deep_research import _OPTIMIZE_MIN_QUERIES, optimize_topic

    provider = _make_optimize_provider("TOPIC: refined topic\nQUERIES:\nonly one query")

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
    ):
        result = await optimize_topic(vault_id="v", topic="seed")

    assert len(result.queries) >= _OPTIMIZE_MIN_QUERIES
    assert result.queries[0] == "only one query"


# ── T-OPT-007: cost logged from accumulator (I7) ──────────────────────────────


@pytest.mark.asyncio
async def test_optimize_topic_binds_accumulator_and_logs_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T-OPT-007: I7 — an accumulator is bound and total cost is logged."""
    import logging

    from app.ops.deep_research import optimize_topic

    provider = _make_optimize_provider("TOPIC: t\nQUERIES:\nq1\nq2\nq3")

    with (
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
        patch("app.ops.deep_research._load_research_vault_context", return_value=""),
        caplog.at_level(logging.INFO, logger="app.ops.deep_research"),
    ):
        await optimize_topic(vault_id="v", topic="seed")

    # accumulator was bound before the call (I7)
    provider.bind_accumulator.assert_called_once()
    assert any(
        "cost_usd=" in rec.getMessage() for rec in caplog.records
    ), "optimize_topic must log total_cost_usd (I7)"


# ── API-level tests ───────────────────────────────────────────────────────────


@pytest.fixture()
async def optimize_client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """Minimal ASGI client with lifespan bypassed and default vault_id set."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ── T-OPT-008: endpoint 200 with optimized topic + queries ────────────────────


@pytest.mark.asyncio
async def test_endpoint_returns_optimized_topic(optimize_client: AsyncClient) -> None:
    """T-OPT-008: POST /research/optimize-topic → 200 {optimized_topic, queries}."""
    from app.ops.deep_research import OptimizedTopic

    async def _mock_optimize(*, vault_id: str, topic: str) -> OptimizedTopic:
        return OptimizedTopic(
            optimized_topic="Optimized: " + topic,
            queries=["q one", "q two", "q three"],
        )

    with patch("app.ops.deep_research.optimize_topic", side_effect=_mock_optimize):
        resp = await optimize_client.post(
            "/research/optimize-topic",
            json={"topic": "networking", "vault_id": "test-vault"},
        )

    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["optimized_topic"] == "Optimized: networking"
    assert data["queries"] == ["q one", "q two", "q three"]


# ── T-OPT-009: endpoint no-provider → 200 echo fallback (NOT 500/503) ─────────


@pytest.mark.asyncio
async def test_endpoint_no_provider_echo_fallback(optimize_client: AsyncClient) -> None:
    """T-OPT-009: no provider configured → 200 with the seed echoed (never 500/503)."""
    # Real optimize_topic runs, but provider resolution returns None → naive fallback.
    with patch(
        "app.ops.deep_research.resolve_operation_provider", new=AsyncMock(return_value=None)
    ):
        resp = await optimize_client.post(
            "/research/optimize-topic",
            json={"topic": "offline topic"},
        )

    assert resp.status_code == 200, f"must be 200 on no-provider; got {resp.status_code}"
    data = resp.json()
    assert data["optimized_topic"] == "offline topic"
    assert data["queries"] == ["offline topic"]


# ── T-OPT-010: endpoint 422 for empty topic ───────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_422_for_empty_topic(optimize_client: AsyncClient) -> None:
    """T-OPT-010: empty topic → 422 (min_length=1)."""
    resp = await optimize_client.post("/research/optimize-topic", json={"topic": ""})
    assert resp.status_code == 422, f"expected 422 for empty topic; got {resp.status_code}"


# ── T-OPT-011: /research/start optional queries → seed_queries passthrough ─────


@pytest.mark.asyncio
async def test_start_passes_edited_queries_as_seed_queries(
    optimize_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    T-OPT-011: POST /research/start with optional `queries` forwards them to
    run_deep_research as seed_queries (verbatim, bounded). Empty entries are stripped.
    """
    import asyncio

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

    captured: dict[str, Any] = {}

    async def _noop(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.ops.deep_research.run_deep_research", _noop)

    # Swallow the pre-insert DB write + scheduled task without a real DB / loop.
    @asynccontextmanager
    async def _null_session():  # type: ignore[return]
        sess = AsyncMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        sess.commit = AsyncMock()
        yield sess

    monkeypatch.setattr("app.main.get_session", _null_session)
    monkeypatch.setattr("app.db.get_session", _null_session)

    original_create_task = asyncio.create_task

    def _run_now(coro: Any, **kwargs: Any) -> Any:
        # Execute the coroutine synchronously enough to capture kwargs, then return a done future.
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        try:
            coro.send(None)
        except StopIteration:
            pass
        except Exception:
            if hasattr(coro, "close"):
                coro.close()
        fut.set_result(None)
        return fut

    monkeypatch.setattr(asyncio, "create_task", _run_now)

    try:
        resp = await optimize_client.post(
            "/research/start",
            json={
                "vault_id": "test-vault",
                "topic": "container networking",
                "queries": ["Calico BGP", "  ", "Cilium eBPF"],
            },
        )
    finally:
        asyncio.create_task = original_create_task

    assert resp.status_code == 202, f"got {resp.status_code}: {resp.text}"
    # empty/whitespace query stripped by the validator; verbatim order preserved
    assert captured.get("seed_queries") == ["Calico BGP", "Cilium eBPF"]


@pytest.mark.asyncio
async def test_start_without_queries_passes_none(
    optimize_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OPT-011b: omitting `queries` → seed_queries=None (unchanged generate-from-scratch path)."""
    import asyncio

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

    captured: dict[str, Any] = {}

    async def _noop(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.ops.deep_research.run_deep_research", _noop)

    @asynccontextmanager
    async def _null_session():  # type: ignore[return]
        sess = AsyncMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        sess.commit = AsyncMock()
        yield sess

    monkeypatch.setattr("app.main.get_session", _null_session)
    monkeypatch.setattr("app.db.get_session", _null_session)

    original_create_task = asyncio.create_task

    def _run_now(coro: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        try:
            coro.send(None)
        except StopIteration:
            pass
        except Exception:
            if hasattr(coro, "close"):
                coro.close()
        fut.set_result(None)
        return fut

    monkeypatch.setattr(asyncio, "create_task", _run_now)

    try:
        resp = await optimize_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "container networking"},
        )
    finally:
        asyncio.create_task = original_create_task

    assert resp.status_code == 202, f"got {resp.status_code}: {resp.text}"
    assert captured.get("seed_queries") is None
