"""
Bounded-loop tests (I7, ADR-0009). Infra-free — fake providers only.

Coverage:
  - non-converging provider stops at EXACTLY max_iter=3 (no overrun); analyze called ONCE;
    generate called max_iter times; Usage accumulated; converged=False.
  - token_budget stops the loop before a call it cannot afford.
  - cost-anomaly ($1) inline WARNING is emitted and cost_anomaly=True is recorded.
  - provider fallback is bounded to exactly ONE attempt.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import app.ingest.orchestrator as orch
import httpx
import pytest
from app.ingest.loop import run_orchestrated_loop
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    Message,
    PageType,
    ProviderCapabilities,
    SuggestedPage,
    Usage,
    WikiFrontmatter,
    WikiPage,
)

ORIGIN = "raw/sources/x.md"


def _analysis() -> Analysis:
    return Analysis(
        topics=["t"],
        entities=[],
        language="en",
        suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
    )


class _NonConverging(InferenceProvider):
    """Always returns an INVALID batch (empty sources[]) → never converges."""

    def __init__(self) -> None:
        self.analyze_calls = 0
        self.generate_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("local", False, False, 8192, "NonConverging")

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        self._record_usage(Usage(input_tokens=5, output_tokens=5, total_cost_usd=0.0))
        return _analysis()

    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        self.generate_calls += 1
        self._record_usage(Usage(input_tokens=10, output_tokens=10, total_cost_usd=0.0))
        # Bypass WikiFrontmatter's own non-empty check to produce a page that FAILS the
        # validator's origin-path rule (sources present but missing the origin path).
        return [
            WikiPage(
                title="P",
                type=PageType.CONCEPT,
                content="body",
                frontmatter=WikiFrontmatter(
                    type=PageType.CONCEPT, title="P", sources=["unrelated.md"], lang="en"
                ),
            )
        ]

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_non_converging_stops_at_max_iter_no_overrun() -> None:
    provider = _NonConverging()
    acc = UsageAccumulator()
    result = await run_orchestrated_loop(
        provider=provider,
        accumulator=acc,
        source_text="s",
        vault_context="",
        retrieval_context="",
        origin_source=ORIGIN,
        max_iter=3,
        token_budget=1_000_000,  # high so max_iter is the binding bound
    )
    assert result.converged is False
    assert result.stop_reason == "max_iter"
    assert result.iterations == 3  # EXACTLY max_iter — no overrun (AC-K2-5)
    assert provider.analyze_calls == 1  # analyze ONCE (AQ-v0.2-1)
    assert provider.generate_calls == 3  # generate == max_iter
    # Usage accumulated across analyze + 3 generates.
    assert acc.calls == 4
    assert acc.total_tokens == (5 + 5) + 3 * (10 + 10)
    # 1.9.1 W5 (NC-1): the last iteration's validation errors survive into diagnostics() so a
    # converged_false run explains itself instead of a bare "Non convergito" label.
    diag = result.diagnostics()
    assert diag["stop_reason"] == "max_iter"
    assert diag["iterations"] == 3
    assert diag["last_errors"] != []
    assert diag["tokens_used"] == acc.total_tokens
    assert diag["token_budget"] == 1_000_000


@pytest.mark.asyncio
async def test_token_budget_stops_loop_before_unaffordable_call() -> None:
    provider = _NonConverging()
    acc = UsageAccumulator()
    # analyze spends 10 tokens; budget 15 → after iter 1 (spends 20) total=30 >= 15 → stop.
    result = await run_orchestrated_loop(
        provider=provider,
        accumulator=acc,
        source_text="s",
        vault_context="",
        retrieval_context="",
        origin_source=ORIGIN,
        max_iter=10,
        token_budget=15,
    )
    assert result.converged is False
    assert result.stop_reason == "token_budget"
    # generate ran at most a couple of times, NOT all 10 (budget is the binding bound).
    assert provider.generate_calls < 10
    # 1.9.1 W5 (NC-1): diagnostics reports the binding bound + last-seen validation errors.
    diag = result.diagnostics()
    assert diag["stop_reason"] == "token_budget"
    assert diag["token_budget"] == 15


class _ConvergingCostly(InferenceProvider):
    """Converges in one pass but reports a cost > $1 to trip the anomaly check."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("api", True, False, 200_000, "CostlyApi")

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self._record_usage(Usage(input_tokens=100, output_tokens=50, total_cost_usd=0.60))
        return _analysis()

    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        self._record_usage(Usage(input_tokens=200, output_tokens=100, total_cost_usd=0.50))
        return [
            WikiPage(
                title="P",
                type=PageType.CONCEPT,
                content="body",
                frontmatter=WikiFrontmatter(
                    type=PageType.CONCEPT, title="P", sources=[ORIGIN], lang="en"
                ),
            )
        ]

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


class _Row:
    def __init__(self) -> None:
        self.provider_type = "api"
        self.model_id = "dummy-model"
        self.base_url = None
        self.max_iter = 3
        self.token_budget = 60_000
        self.is_fallback = False


@pytest.mark.asyncio
async def test_cost_anomaly_warning_and_flag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # This test exercises the JSON loop's cost-anomaly path via a JSON fake (no complete()); pin
    # the rollback format so the 1.7.0 "blocks" default doesn't route it through the block loop.
    import app.config_overrides as config_overrides

    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "json")

    provider = _ConvergingCostly()
    runs: list = []

    import uuid as _uuid_mod
    from contextlib import asynccontextmanager

    async def fake_write_wiki_page(session, page, origin, **kwargs):  # type: ignore[no-untyped-def]
        # Return a stub with .id so record_written() doesn't fail (ADR-0046)
        class _PageStub:
            id = _uuid_mod.uuid4()

        return _PageStub()

    async def fake_update_overview(analysis, origin):  # type: ignore[no-untyped-def]
        return None

    @asynccontextmanager
    async def fake_get_session():  # type: ignore[no-untyped-def]
        # BE-PERF-2: run_ingest_pipeline now builds the wikilink resolver maps + calls
        # update_index/bump_version ONCE per document directly (not through the mocked
        # write_wiki_page above) — those DB-touching calls are stubbed below, but the
        # `async with orch.get_session()` wrapper around the resolver-maps query still runs,
        # so it needs an infra-free stand-in here (this test is otherwise infra-free).
        yield None

    async def fake_build_resolver_maps(session, vault_id):  # type: ignore[no-untyped-def]
        return None

    async def fake_update_index_once(session, vault_path):  # type: ignore[no-untyped-def]
        return None

    async def fake_open_ingest_run(**kwargs):  # type: ignore[no-untyped-def]
        return _uuid_mod.uuid4()

    async def fake_finalize_ingest_run(**kwargs):  # type: ignore[no-untyped-def]
        runs.append(kwargs)

    # ADR-0046: queue_manager.open_run / finalize are called from run_ingest_pipeline;
    # patch them to no-ops so the test remains infra-free.
    import asyncio as _asyncio

    from app.ingest.queue_manager import IngestQueueManager

    class _FakeHandle:
        run_id = _uuid_mod.uuid4()
        source_path = ORIGIN
        cancel_event = _asyncio.Event()
        written_page_ids: list = []
        status = "running"

    async def _noop_acquire_capability_slot(mode: str) -> None:  # type: ignore[no-untyped-def]
        return None

    fake_queue = IngestQueueManager.__new__(IngestQueueManager)
    fake_queue._active = {}  # type: ignore[attr-defined]
    fake_queue._run_id_to_path = {}  # type: ignore[attr-defined]
    fake_queue._pending = {}  # type: ignore[attr-defined]
    fake_queue._retry_counts = {}  # type: ignore[attr-defined]
    fake_queue._recent_failed = {}  # type: ignore[attr-defined]
    fake_queue._paused = False  # type: ignore[attr-defined]
    fake_queue._completed_since_idle = 0  # type: ignore[attr-defined]
    fake_queue._suppress = {}  # type: ignore[attr-defined]
    fake_queue._watcher_handler = None  # type: ignore[attr-defined]
    fake_queue.open_run = lambda run_id, source_path: _FakeHandle()  # type: ignore[attr-defined]
    fake_queue.finalize = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.get_retry_count = lambda path: 0  # type: ignore[attr-defined]
    fake_queue.record_written = lambda *a, **kw: None  # type: ignore[attr-defined]
    # BE-QUEUE-1/2 (1.9.4 W3): run_ingest_pipeline now gates on the capability semaphore and
    # touches the rate-limit ladder on both terminal paths — stub them as no-ops.
    fake_queue.acquire_capability_slot = _noop_acquire_capability_slot  # type: ignore[attr-defined]
    fake_queue.release_capability_slot = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.pause_for_rate_limit = lambda *a, **kw: 0.0  # type: ignore[attr-defined]
    fake_queue.reset_rate_limit_backoff = lambda *a, **kw: None  # type: ignore[attr-defined]

    monkeypatch.setattr(orch, "ingest_queue", fake_queue)
    monkeypatch.setattr(orch, "resolve_provider", lambda row: provider)
    monkeypatch.setattr(orch, "write_wiki_page", fake_write_wiki_page)
    monkeypatch.setattr(orch, "_update_overview", fake_update_overview)
    monkeypatch.setattr(orch, "_open_ingest_run", fake_open_ingest_run)
    monkeypatch.setattr(orch, "_finalize_ingest_run", fake_finalize_ingest_run)
    monkeypatch.setattr(orch, "_load_vault_context", lambda: "")
    monkeypatch.setattr(orch, "get_session", fake_get_session)
    monkeypatch.setattr(orch, "bump_version", AsyncMock())
    monkeypatch.setattr("app.wiki.links.build_resolver_maps", fake_build_resolver_maps)
    monkeypatch.setattr("app.wiki.index.update_index", fake_update_index_once)

    with caplog.at_level(logging.WARNING):
        result = await orch.run_ingest_pipeline(
            provider_config_row=_Row(),
            source_text="s",
            origin_source=ORIGIN,
        )

    assert result.total_cost_usd > 1.00
    assert result.cost_anomaly is True
    # The ingest_runs row carries cost_anomaly=True (written BEFORE the warning, ADR-0009 §3).
    assert runs[0]["cost_anomaly"] is True
    assert any("COST ANOMALY" in r.message for r in caplog.records)


# ── Fallback bounded to exactly once (I7, ADR-0009 §4) ──────────────────────────


class _FailingProvider(InferenceProvider):
    def __init__(self, name: str) -> None:
        self._name = name
        self.analyze_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("api", True, False, 200_000, self._name)

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        raise TimeoutError("simulated provider timeout")

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_fallback_bounded_to_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = _FailingProvider("Primary")
    fallback = _FailingProvider("Fallback")
    fallback_row = _Row()

    # primary fails; fallback resolves once and also fails → IngestError, no chains.
    monkeypatch.setattr(orch, "_resolve_fallback_provider_config", _async_return(fallback_row))
    monkeypatch.setattr(
        orch, "resolve_provider", lambda row: fallback if row is fallback_row else primary
    )

    acc = UsageAccumulator()
    primary.bind_accumulator(acc)

    with pytest.raises(orch.IngestError):
        await orch._run_orchestrated(
            provider=primary,
            accumulator=acc,
            source_text="s",
            origin_source=ORIGIN,
            config_row=_Row(),
        )

    assert primary.analyze_calls == 1
    assert fallback.analyze_calls == 1  # exactly one fallback attempt — no chains


def _async_return(value: object):  # type: ignore[no-untyped-def]
    async def _inner() -> object:
        return value

    return _inner


# ── Fallback engages on HTTP 5xx, not on 4xx (NB-1, ADR-0009 §4) ────────────────


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError carrying *status_code* (e.g. a literal 503)."""
    request = httpx.Request("POST", "http://provider.local/api/chat")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"server returned {status_code}", request=request, response=response
    )


class _HttpStatusFailing(InferenceProvider):
    """analyze() raises httpx.HTTPStatusError with a configurable status code."""

    def __init__(self, name: str, status_code: int) -> None:
        self._name = name
        self._status_code = status_code
        self.analyze_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("api", True, False, 200_000, self._name)

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        raise _http_status_error(self._status_code)

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


class _Converging(InferenceProvider):
    """analyze + generate succeed with a valid batch (used as a healthy fallback)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.analyze_calls = 0
        self.generate_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("api", True, False, 200_000, self._name)

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        self._record_usage(Usage(input_tokens=5, output_tokens=5, total_cost_usd=0.0))
        return _analysis()

    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        self.generate_calls += 1
        self._record_usage(Usage(input_tokens=10, output_tokens=10, total_cost_usd=0.0))
        return [
            WikiPage(
                title="P",
                type=PageType.CONCEPT,
                content="body",
                frontmatter=WikiFrontmatter(
                    type=PageType.CONCEPT, title="P", sources=[ORIGIN], lang="en"
                ),
            )
        ]

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_http_503_engages_fallback_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A primary raising httpx.HTTPStatusError(503) engages the single bounded fallback once."""
    primary = _HttpStatusFailing("Primary", status_code=503)
    fallback = _Converging("Fallback")
    fallback_row = _Row()

    monkeypatch.setattr(orch, "_resolve_fallback_provider_config", _async_return(fallback_row))
    monkeypatch.setattr(
        orch, "resolve_provider", lambda row: fallback if row is fallback_row else primary
    )

    acc = UsageAccumulator()
    primary.bind_accumulator(acc)

    result = await orch._run_orchestrated(
        provider=primary,
        accumulator=acc,
        source_text="s",
        origin_source=ORIGIN,
        config_row=_Row(),
    )

    # 5xx → fallback engaged exactly once and the healthy fallback converged.
    assert primary.analyze_calls == 1
    assert fallback.analyze_calls == 1  # exactly one fallback attempt — no chains
    assert result.converged is True


@pytest.mark.asyncio
async def test_http_400_surfaces_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx (client error / bad request) must surface, NOT engage the fallback (NB-1)."""
    primary = _HttpStatusFailing("Primary", status_code=400)
    fallback = _Converging("Fallback")
    fallback_row = _Row()

    monkeypatch.setattr(orch, "_resolve_fallback_provider_config", _async_return(fallback_row))
    monkeypatch.setattr(
        orch, "resolve_provider", lambda row: fallback if row is fallback_row else primary
    )

    acc = UsageAccumulator()
    primary.bind_accumulator(acc)

    with pytest.raises(httpx.HTTPStatusError):
        await orch._run_orchestrated(
            provider=primary,
            accumulator=acc,
            source_text="s",
            origin_source=ORIGIN,
            config_row=_Row(),
        )

    assert primary.analyze_calls == 1
    assert fallback.analyze_calls == 0  # 4xx never engages the fallback
