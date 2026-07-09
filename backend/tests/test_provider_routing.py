"""
Capability-aware routing tests (I6, ADR-0007 §3). The orchestrator MUST route on
capabilities().supports_agentic_loop ONLY — never on class/type/name.

The key test uses a `CustomAgentic` provider that is NOT CliAgentProvider but reports
supports_agentic_loop=True: the orchestrator must STILL delegate (proving routing is by
capability, not by class). And a `CustomLocal` (supports_agentic_loop=False) must run the
orchestrated loop. No live providers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import app.ingest.orchestrator as orch
import pytest
from app.ingest.provider.base import InferenceProvider
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

# ── Fake providers (not the shipped classes — proves routing is class-agnostic) ─


class _CustomAgentic(InferenceProvider):
    """An agentic provider that is NOT CliAgentProvider (AC-K2-4)."""

    def __init__(self) -> None:
        self.delegated = False

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="cli",
            supports_tools=True,
            supports_agentic_loop=True,  # <- the only routing signal
            max_context=200_000,
            name="CustomAgentic",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise AssertionError("analyze() must NOT be called on the delegated route")

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str
    ) -> list[WikiPage]:
        raise AssertionError("generate() must NOT be called on the delegated route")

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError

    async def delegate_ingest(self, **kwargs: object) -> object:
        self.delegated = True

        class _R:
            converged = True

        return _R()


class _CustomLocal(InferenceProvider):
    """A non-agentic provider that produces one valid page (orchestrated route)."""

    def __init__(self, origin: str) -> None:
        self._origin = origin
        self.analyze_calls = 0
        self.generate_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,
            supports_agentic_loop=False,
            max_context=8192,
            name="CustomLocal",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        self._record_usage(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0))
        return Analysis(
            topics=["t"],
            entities=[],
            language="en",
            suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
        )

    async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]:
        self.generate_calls += 1
        self._record_usage(Usage(input_tokens=20, output_tokens=10, total_cost_usd=0.0))
        return [
            WikiPage(
                title="P",
                type=PageType.CONCEPT,
                content="body",
                frontmatter=WikiFrontmatter(
                    type=PageType.CONCEPT, title="P", sources=[self._origin], lang="en"
                ),
            )
        ]

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


class _Row:
    """Minimal duck-typed provider_config row."""

    def __init__(self, provider_type: str) -> None:
        self.provider_type = provider_type
        self.model_id = "dummy-model"
        self.base_url = None
        self.max_iter = 3
        self.token_budget = 60_000
        self.is_fallback = False


@pytest.fixture()
def _patch_persistence(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Stub out all I/O so routing can be tested infra-free (ADR-0046: uses open/finalize)."""
    import asyncio as _asyncio
    import uuid as _uuid

    written: list = []
    runs: list = []

    async def fake_write_wiki_page(session, page, origin, *, provider=None):  # type: ignore[no-untyped-def]
        written.append(page)

        # Return a stub with .id so record_written() doesn't fail (ADR-0046)
        class _PageStub:
            id = _uuid.uuid4()

        return _PageStub()

    async def fake_update_overview(analysis, origin):  # type: ignore[no-untyped-def]
        return None

    async def fake_open_ingest_run(**kwargs):  # type: ignore[no-untyped-def]
        return _uuid.uuid4()

    async def fake_finalize_ingest_run(**kwargs):  # type: ignore[no-untyped-def]
        runs.append(kwargs)

    def fake_vault_context() -> str:
        return ""

    # Patch queue_manager.open_run / finalize to avoid asyncio.Event needing a loop
    from app.ingest.queue_manager import IngestQueueManager

    class _FakeHandle:
        run_id = _uuid.uuid4()
        source_path = "raw/sources/x.md"
        cancel_event = _asyncio.Event()
        written_page_ids: list = []
        status = "running"

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

    monkeypatch.setattr(orch, "ingest_queue", fake_queue)
    monkeypatch.setattr(orch, "write_wiki_page", fake_write_wiki_page)
    monkeypatch.setattr(orch, "_update_overview", fake_update_overview)
    monkeypatch.setattr(orch, "_open_ingest_run", fake_open_ingest_run)
    monkeypatch.setattr(orch, "_finalize_ingest_run", fake_finalize_ingest_run)
    monkeypatch.setattr(orch, "_load_vault_context", fake_vault_context)
    return {"written": written, "runs": runs}


@pytest.mark.asyncio
async def test_agentic_provider_is_delegated_by_capability_not_class(
    monkeypatch: pytest.MonkeyPatch, _patch_persistence: dict[str, list]
) -> None:
    provider = _CustomAgentic()
    monkeypatch.setattr(orch, "resolve_provider", lambda row: provider)

    result = await orch.run_ingest_pipeline(
        provider_config_row=_Row("cli"),
        source_text="hello",
        origin_source="raw/sources/x.md",
    )

    assert provider.delegated is True
    assert result.route == "delegated"
    # ingest_runs row written even on the delegated path (I7).
    assert len(_patch_persistence["runs"]) == 1
    assert _patch_persistence["runs"][0]["route"] == "delegated"


@pytest.mark.asyncio
async def test_non_agentic_provider_runs_orchestrated_loop(
    monkeypatch: pytest.MonkeyPatch, _patch_persistence: dict[str, list]
) -> None:
    provider = _CustomLocal(origin="raw/sources/x.md")
    monkeypatch.setattr(orch, "resolve_provider", lambda row: provider)

    result = await orch.run_ingest_pipeline(
        provider_config_row=_Row("local"),
        source_text="hello",
        origin_source="raw/sources/x.md",
    )

    assert result.route == "orchestrated"
    assert result.converged is True
    assert provider.analyze_calls == 1  # analyze ONCE (AQ-v0.2-1)
    assert provider.generate_calls == 1
    # The provider emits a single CONCEPT page and NO source page, so the ADR-0063 §2.4 mandatory
    # source-page guarantee appends one → 2 written (concept + synthesized source summary).
    written = _patch_persistence["written"]
    assert len(written) == 2
    types = {p.type for p in written}
    assert PageType.CONCEPT in types
    assert PageType.SOURCE in types
    source_page = next(p for p in written if p.type is PageType.SOURCE)
    assert "raw/sources/x.md" in source_page.frontmatter.sources
    assert len(_patch_persistence["runs"]) == 1


@pytest.mark.asyncio
async def test_routing_has_no_class_or_type_check_in_source() -> None:
    """
    Static guardrail (CI condition 1): the routing region of run_ingest_pipeline must not use
    isinstance/type()/class-name literals. We assert the routing branch reads the capability
    attribute and contains no forbidden constructs.
    """
    import inspect

    src = inspect.getsource(orch.run_ingest_pipeline)
    assert "supports_agentic_loop" in src
    assert "isinstance(" not in src
    assert "type(" not in src
    assert "CliAgentProvider" not in src
    assert "provider_type ==" not in src


def test_resolve_provider_has_no_hardcoded_default() -> None:
    """resolve_provider must reject a missing/unknown row, never default a backend (I6)."""
    from app.ingest.provider import resolve_provider

    with pytest.raises(ValueError):
        resolve_provider(None)
    with pytest.raises(ValueError):
        resolve_provider(_Row("bogus"))
