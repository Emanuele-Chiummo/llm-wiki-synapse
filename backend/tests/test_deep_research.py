"""
Deep Research unit tests (AC-F10-1..7, ADR-0024).

Coverage:
  T-DR-001  all 6 pipeline steps execute in order (AC-F10-1)
  T-DR-002  max_iter_reached: loop stops at exactly max_iter (AC-F10-2a, EC-M5-5 MANDATORY)
  T-DR-003  status is max_iter_reached (not running) after loop (AC-F10-2b)
  T-DR-004  no further provider calls after max_iter (AC-F10-2c)
  T-DR-005  budget_exhausted before an unaffordable round
  T-DR-006  converged path ends after 1 round when sufficient (AC-F10-7b)
  T-DR-007  concurrency never exceeds 3 (semaphore, AC-F10-2e, Do-NOT #4)
  T-DR-008  sufficiency assessed before refine (Do-NOT #8, CLAUDE.md §7)
  T-DR-009  synthesis routed through ingest_file, not direct write (AC-F10-1/7d, AQ-v0.5-3)
  T-DR-010  never leaves status "running" on exception → "error" (AC-F10-2b, Do-NOT #7)
  T-DR-011  3 SearXNG hits → exactly 3 fetch calls (AC-F10-7a)
  T-DR-012  max_queries_per_iter not exceeded (AC-F10-2d)
  T-DR-013  I9 static guard: no tavily/ddg/duckduckgo/googlesearch/serpapi in ops/

All tests mock SearXNG HTTP and the provider — no network, no DB required.
Test-isolation: patch app.db.async_session_factory at the top-level scope; deep_research.py
reads get_session (which reads async_session_factory at call time).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_mock_provider(
    *,
    always_sufficient: bool = False,
    insufficient_count: int = 99,
    query_responses: list[str] | None = None,
) -> Any:
    """
    Build a mock InferenceProvider that:
    - chat() returns an async generator yielding a single string
    - Tracks how many times it is called (call_count)
    """
    provider = MagicMock()

    # Track chat calls
    _chat_calls: list[int] = [0]

    async def _chat_gen(prompt: str) -> AsyncIterator[str]:
        yield prompt

    async def mock_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        _chat_calls[0] += 1
        content = messages[0].content if messages else ""

        # Determine response based on the phase (detected from prompt content)
        if "Generate" in content and "search queries" in content:
            # query generation — return up to max_queries lines
            if query_responses:
                response = "\n".join(query_responses)
            else:
                response = "query one\nquery two"
        elif "evaluating whether" in content:
            # sufficiency assessment
            if always_sufficient:
                response = "SUFFICIENT"
            elif _chat_calls[0] > insufficient_count * 3:
                response = "SUFFICIENT"
            else:
                response = "INSUFFICIENT\nneed more information"
        else:
            # synthesis
            response = "# Synthesis\n\nThis is the synthesized content."

        async def _gen() -> AsyncIterator[str]:
            yield response

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    provider._accumulator = None

    def bind_acc(acc: Any) -> None:
        provider._accumulator = acc
        provider._bound_acc = acc

    provider.bind_accumulator.side_effect = bind_acc

    # Expose call counter
    provider._chat_calls = _chat_calls

    return provider


def _make_search_hits(n: int = 3) -> list[Any]:
    from app.ops.searxng import SearchHit

    return [
        SearchHit(url=f"https://example.com/page-{i}", title=f"Page {i}", snippet=f"snippet {i}")
        for i in range(n)
    ]


@asynccontextmanager
async def _null_session():  # type: ignore[return]
    """A no-op session context manager for DB mocking."""
    sess = AsyncMock()
    sess.add = MagicMock()
    sess.flush = AsyncMock()
    sess.commit = AsyncMock()
    sess.rollback = AsyncMock()
    sess.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    yield sess


@pytest.fixture(autouse=True)
def _patch_db_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Patch get_session on ALL import paths that deep_research.py touches.
    This is the paranoid isolation pattern (recurring bug class note in task spec).
    """
    monkeypatch.setattr("app.db.get_session", _null_session)
    monkeypatch.setattr("app.ops.deep_research.get_session", _null_session)


@pytest.fixture(autouse=True)
def _patch_ingest_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ingest_file so no real filesystem/DB writes happen."""
    from app.ingest.pipeline import IngestResult

    mock_page_id = uuid.uuid4()

    async def _mock_ingest(path: Any) -> IngestResult:
        return IngestResult(page_id=mock_page_id, status="completed")

    monkeypatch.setattr("app.ingest.pipeline.ingest_file", _mock_ingest)


@pytest.fixture(autouse=True)
def _patch_resolve_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch provider resolution so no DB calls needed."""

    # Returns None by default — deep_research falls through to mechanical path
    async def _no_provider(vault_id: str) -> None:
        return None

    monkeypatch.setattr("app.ops.deep_research.resolve_operation_provider", _no_provider)


@pytest.fixture(autouse=True)
def _patch_vault_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Patch vault_path so vault_root (derived property) resolves to a temp dir."""
    from app import config as cfg

    # vault_root is a computed @property from vault_path — patch the underlying field.
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))


# ── Helpers for controlled runs ──────────────────────────────────────────────


async def _run_with_provider(
    provider: Any,
    *,
    topic: str = "test topic",
    max_iter: int = 2,
    token_budget: int = 100_000,
    searxng_hits: list[Any] | None = None,
) -> Any:
    """
    Run run_deep_research with a mock provider + mock SearXNG hits.

    Patches:
    - searxng_search_many to return searxng_hits
    - _resolve_provider to return provider
    - _fetch_and_extract to return lightweight FetchedSource objects
    - All DB helpers to no-ops
    """
    from app.ops.deep_research import FetchedSource, run_deep_research
    from app.ops.searxng import SearchHit

    hits = searxng_hits if searxng_hits is not None else _make_search_hits(3)

    async def _mock_searxng(queries: list[str]) -> list[SearchHit]:
        return hits

    async def _mock_fetch(hits_in: list[SearchHit], *, iteration: int = 1) -> list[FetchedSource]:
        return [
            FetchedSource(
                url=h.url,
                title=h.title,
                content_md=f"Content for {h.url}",
                iteration=iteration,
            )
            for h in hits_in
        ]

    async def _mock_resolve(vault_id: str) -> Any:
        return (provider, None)

    async def _mock_create_run(**kwargs: Any) -> Any:
        from app.models import DeepResearchRun

        run = MagicMock(spec=DeepResearchRun)
        run.id = uuid.uuid4()
        run.max_iter = max_iter
        run.token_budget = token_budget
        run.vault_id = kwargs.get("vault_id", "test")
        run.topic = kwargs.get("topic", topic)
        return run

    with (
        patch("app.ops.deep_research._search_searxng", side_effect=_mock_searxng),
        patch("app.ops.deep_research._fetch_and_extract", side_effect=_mock_fetch),
        patch("app.ops.deep_research.resolve_operation_provider", side_effect=_mock_resolve),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
    ):
        return await run_deep_research(
            vault_id="test-vault",
            topic=topic,
            max_iter=max_iter,
            token_budget=token_budget,
        )


# ── T-DR-014: C1 regression — caller-provided run_id reuses the row ──────────


async def _run_loop_patched(*, run_id: uuid.UUID | None, create_spy: AsyncMock) -> Any:
    """Run the loop with all internals mocked; spy on _create_run_row."""
    from app.ops.deep_research import FetchedSource, run_deep_research
    from app.ops.searxng import SearchHit

    hits = _make_search_hits(2)

    async def _mock_searxng(queries: list[str]) -> list[SearchHit]:
        return hits

    async def _mock_fetch(hits_in: list[SearchHit], *, iteration: int = 1) -> list[FetchedSource]:
        return [
            FetchedSource(url=h.url, title=h.title, content_md="x", iteration=iteration)
            for h in hits_in
        ]

    async def _mock_resolve(vault_id: str) -> Any:
        return _make_mock_provider(always_sufficient=True)

    with (
        patch("app.ops.deep_research._search_searxng", side_effect=_mock_searxng),
        patch("app.ops.deep_research._fetch_and_extract", side_effect=_mock_fetch),
        patch("app.ops.deep_research.resolve_operation_provider", side_effect=_mock_resolve),
        patch("app.ops.deep_research._create_run_row", create_spy),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
    ):
        return await run_deep_research(
            vault_id="test-vault",
            topic="t",
            max_iter=1,
            token_budget=100_000,
            run_id=run_id,
        )


@pytest.mark.asyncio
async def test_provided_run_id_skips_row_creation() -> None:
    """
    C1 regression (ADR-0024 §8.1): the endpoint pre-INSERTs the row and passes its
    run_id; run_deep_research MUST reuse it and NOT create a second row — otherwise
    the client polls a row the loop never finalizes (stuck "running" forever).
    """
    given = uuid.uuid4()
    create_spy = AsyncMock()
    result = await _run_loop_patched(run_id=given, create_spy=create_spy)

    create_spy.assert_not_called()  # no second row minted
    assert result.run_id == given  # loop finalizes the caller's row


@pytest.mark.asyncio
async def test_no_run_id_still_creates_row() -> None:
    """Companion: a direct call (run_id=None) still mints + INSERTs its own row."""
    minted = uuid.uuid4()

    async def _mock_create(**kwargs: Any) -> Any:
        from app.models import DeepResearchRun

        run = MagicMock(spec=DeepResearchRun)
        run.id = minted
        run.max_iter = kwargs["max_iter"]
        run.token_budget = kwargs["token_budget"]
        return run

    create_spy = AsyncMock(side_effect=_mock_create)
    await _run_loop_patched(run_id=None, create_spy=create_spy)

    create_spy.assert_called_once()


# ── T-DR-001: all 6 steps execute in order ───────────────────────────────────


@pytest.mark.asyncio
async def test_all_six_steps_execute() -> None:
    """T-DR-001: AC-F10-1 — all 6 pipeline steps execute."""
    steps_executed: list[str] = []
    provider = _make_mock_provider(always_sufficient=True)

    from app.ops.deep_research import (
        FetchedSource,
        run_deep_research,
    )

    async def _mock_generate(
        p: Any, topic: str, prior_context: Any, *, max_queries: int
    ) -> list[str]:
        steps_executed.append("generate_queries")
        return ["query1", "query2"]

    async def _mock_search(queries: list[str]) -> list[Any]:
        steps_executed.append("search_searxng")
        return _make_search_hits(2)

    async def _mock_fetch(hits: list[Any], *, iteration: int = 1) -> list[FetchedSource]:
        steps_executed.append("fetch_and_extract")
        return [
            FetchedSource(url=h.url, title=h.title, content_md="content", iteration=iteration)
            for h in hits
        ]

    async def _mock_assess(p: Any, topic: str, collected: list[Any]) -> Any:
        from app.ops.deep_research import Sufficiency

        steps_executed.append("assess_sufficiency")
        return Sufficiency(sufficient=True, gaps=[])

    async def _mock_synthesize(p: Any, topic: str, collected: list[Any]) -> str:
        steps_executed.append("synthesize")
        return "# Synthesis\n\nContent."

    async def _mock_ingest_synthesis(run_id: Any, vault_id: str, md: str, topic: str) -> uuid.UUID:
        steps_executed.append("ingest_synthesis")
        return uuid.uuid4()

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 3
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", side_effect=_mock_generate),
        patch("app.ops.deep_research._search_searxng", side_effect=_mock_search),
        patch("app.ops.deep_research._fetch_and_extract", side_effect=_mock_fetch),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_mock_assess),
        patch("app.ops.deep_research._synthesize", side_effect=_mock_synthesize),
        patch("app.ops.deep_research._ingest_synthesis", side_effect=_mock_ingest_synthesis),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
    ):
        result = await run_deep_research(
            vault_id="test", topic="docker networking", max_iter=3, token_budget=100_000
        )

    assert "generate_queries" in steps_executed, "Step 1 (generate queries) must execute"
    assert "search_searxng" in steps_executed, "Step 2 (SearXNG search) must execute"
    assert "fetch_and_extract" in steps_executed, "Step 3 (fetch) must execute"
    assert "assess_sufficiency" in steps_executed, "Step 4 (assess) must execute"
    assert "synthesize" in steps_executed, "Step 5 (synthesize) must execute"
    assert "ingest_synthesis" in steps_executed, "Step 6 (ingest_file) must execute"
    assert result.status == "converged"


# ── T-DR-002: max_iter_reached — loop stops at exactly max_iter ───────────────


@pytest.mark.asyncio
async def test_max_iter_reached_terminates_at_exactly_max_iter() -> None:
    """
    T-DR-002: AC-F10-2a, EC-M5-5 MANDATORY.
    Always-insufficient provider → loop must stop at exactly max_iter rounds.
    """
    assess_calls: list[int] = [0]
    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _always_insufficient(p: Any, topic: str, collected: list[Any]) -> Sufficiency:
        assess_calls[0] += 1
        return Sufficiency(sufficient=False, gaps=["always needs more"])

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 3
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q1"])),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(2)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[
                    FetchedSource(url="http://x.com", title="t", content_md="c", iteration=1)
                ]
            ),
        ),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_always_insufficient),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="synth")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=3, token_budget=100_000
        )

    # AC-F10-2a: loop terminates at exactly max_iter
    assert (
        assess_calls[0] == 3
    ), f"assess_sufficiency must be called exactly max_iter=3 times; got {assess_calls[0]}"
    # AC-F10-2b: status is max_iter_reached (not running)
    assert (
        result.status == "max_iter_reached"
    ), f"status must be 'max_iter_reached'; got {result.status!r}"


# ── T-DR-002b: zero sources → NO synthesis, NO page created (B2 regression) ───


@pytest.mark.asyncio
async def test_zero_sources_skips_synthesis_and_page() -> None:
    """
    B2 regression: when the loop collects zero sources (e.g. SearXNG returns
    nothing / is unreachable), the terminal step MUST NOT synthesize or ingest a
    page — otherwise the degraded synthesis prompt yields a conversational
    non-answer that gets ingested as a junk wiki page.
    """
    synth_calls: list[int] = [0]
    ingest_calls: list[int] = [0]

    from app.ops.deep_research import Sufficiency

    async def _mock_synth(p: Any, topic: str, collected: list[Any]) -> str:
        synth_calls[0] += 1
        return "synthesis"

    async def _mock_ingest(run_id: Any, vault_id: str, md: str, topic: str) -> uuid.UUID:
        ingest_calls[0] += 1
        return uuid.uuid4()

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 3
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q1"])),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(0)),
        ),
        # No sources fetched at any iteration → collected stays empty.
        patch("app.ops.deep_research._fetch_and_extract", new=AsyncMock(return_value=[])),
        patch(
            "app.ops.deep_research._assess_sufficiency",
            new=AsyncMock(return_value=Sufficiency(sufficient=False, gaps=["need more"])),
        ),
        patch("app.ops.deep_research._synthesize", side_effect=_mock_synth),
        patch("app.ops.deep_research._ingest_synthesis", side_effect=_mock_ingest),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=3, token_budget=100_000
        )

    assert result.sources_fetched == 0
    assert synth_calls[0] == 0, "must NOT synthesize when zero sources collected"
    assert ingest_calls[0] == 0, "must NOT ingest a page when zero sources collected"
    assert result.synthesis_page_id is None, "no page must be created on zero sources"


# ── T-DR-003: status is set correctly (not "running") ─────────────────────────


@pytest.mark.asyncio
async def test_status_not_running_after_loop() -> None:
    """T-DR-003: AC-F10-2b — status is never left as 'running' after loop."""
    result = await _run_with_provider(_make_mock_provider(always_sufficient=True), max_iter=2)
    assert (
        result.status != "running"
    ), f"status must not be 'running' after loop; got {result.status!r}"
    assert result.status in {"converged", "max_iter_reached", "budget_exhausted", "error"}


# ── T-DR-004: no further provider calls after max_iter ────────────────────────


@pytest.mark.asyncio
async def test_no_provider_calls_after_max_iter() -> None:
    """T-DR-004: AC-F10-2c — no InferenceProvider calls after max_iter is reached."""
    provider = _make_mock_provider(always_sufficient=False, insufficient_count=99)
    assess_calls: list[int] = [0]
    synth_calls: list[int] = [0]
    query_calls: list[int] = [0]

    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _mock_assess(p: Any, topic: str, collected: list[Any]) -> Sufficiency:
        assess_calls[0] += 1
        return Sufficiency(sufficient=False, gaps=["need more"])

    async def _mock_synth(p: Any, topic: str, collected: list[Any]) -> str:
        synth_calls[0] += 1
        return "synthesis"

    async def _mock_generate(p: Any, topic: str, prior: Any, *, max_queries: int) -> list[str]:
        query_calls[0] += 1
        return ["q"]

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 2
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", side_effect=_mock_generate),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(1)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_mock_assess),
        patch("app.ops.deep_research._synthesize", side_effect=_mock_synth),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(provider, None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=2, token_budget=100_000
        )

    # AC-F10-2c: only the single terminal synthesize is allowed after max_iter
    # assess_calls should be exactly max_iter=2
    assert assess_calls[0] == 2, f"assess called {assess_calls[0]} times, expected 2"
    # Synthesize called exactly once (terminal)
    assert synth_calls[0] == 1, f"synthesize called {synth_calls[0]} times, expected 1"
    assert result.status == "max_iter_reached"


# ── T-DR-005: budget_exhausted before unaffordable round ─────────────────────


@pytest.mark.asyncio
async def test_budget_exhausted_before_round() -> None:
    """T-DR-005: budget gate fires at TOP of round before spending."""
    from app.ingest.provider.base import UsageAccumulator

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 5
        run.token_budget = 10  # tiny budget — exhausted immediately
        return run

    assess_calls: list[int] = [0]
    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _mock_assess(p: Any, topic: str, collected: list[Any]) -> Sufficiency:
        assess_calls[0] += 1
        return Sufficiency(sufficient=False, gaps=["need more"])

    # We need to pre-fill the accumulator BEFORE the loop runs
    # Simulate this by patching UsageAccumulator.__init__ to set tokens high
    original_init = UsageAccumulator.__init__

    def _patched_init(self: UsageAccumulator) -> None:
        original_init(self)
        self.input_tokens = 100  # exceed the tiny budget of 10

    with (
        patch.object(UsageAccumulator, "__init__", _patched_init),
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q"])),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(1)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_mock_assess),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="synth")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=5, token_budget=10
        )

    assert (
        result.status == "budget_exhausted"
    ), f"status must be 'budget_exhausted' when tokens >= budget; got {result.status!r}"
    # Budget gate fires before spending — assess should NOT have been called
    assert assess_calls[0] == 0, (
        f"assess_sufficiency must not be called when budget exhausted at round start; "
        f"got {assess_calls[0]} calls"
    )


# ── T-DR-006: converged path ends after 1 round ───────────────────────────────


@pytest.mark.asyncio
async def test_converged_after_first_round() -> None:
    """T-DR-006: AC-F10-7b — sufficient on first assessment → converged after 1 round."""
    assess_calls: list[int] = [0]
    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _assess_once(p: Any, topic: str, collected: list[Any]) -> Sufficiency:
        assess_calls[0] += 1
        return Sufficiency(sufficient=True, gaps=[])  # sufficient immediately

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 5  # large max_iter — should stop at 1
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q"])),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(2)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_assess_once),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="synth")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=5, token_budget=100_000
        )

    assert result.status == "converged", f"expected converged; got {result.status!r}"
    assert (
        assess_calls[0] == 1
    ), f"assess called {assess_calls[0]} times; expected exactly 1 on sufficient-first-round"
    assert result.iterations_used == 1, f"iterations_used must be 1; got {result.iterations_used}"


# ── T-DR-007: concurrency never exceeds 3 ────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_ceiling_is_3() -> None:
    """T-DR-007: AC-F10-2e — semaphore is Semaphore(3); concurrent count never exceeds 3."""
    from app.ops.searxng import CONCURRENCY, _semaphore

    assert CONCURRENCY == 3, f"CONCURRENCY must be 3; got {CONCURRENCY}"
    assert (
        _semaphore._value == 3 or _semaphore._value == CONCURRENCY
    ), f"Semaphore value must equal CONCURRENCY=3; got {_semaphore._value}"


# ── T-DR-008: assessment before refine ───────────────────────────────────────


@pytest.mark.asyncio
async def test_assessment_before_refine() -> None:
    """
    T-DR-008: Do-NOT #8 — assessment is always done BEFORE generating follow-up queries.
    Verify order: generate→search→fetch→assess→[refine if not last]→[next round...].
    """
    order: list[str] = []
    from app.ops.deep_research import FetchedSource, Sufficiency

    call_num: list[int] = [0]

    async def _track_generate(p: Any, topic: str, prior: Any, *, max_queries: int) -> list[str]:
        order.append(f"generate-{call_num[0]}")
        call_num[0] += 1
        return ["q"]

    async def _track_assess(p: Any, topic: str, collected: list[Any]) -> Sufficiency:
        order.append(f"assess-{call_num[0]}")
        return Sufficiency(sufficient=False, gaps=["gap"])

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 2
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", side_effect=_track_generate),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(1)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch("app.ops.deep_research._assess_sufficiency", side_effect=_track_assess),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="synth")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        await run_deep_research(vault_id="test", topic="topic", max_iter=2, token_budget=100_000)

    # First generate (index 0) happens before any assess
    # Then assess happens before the next generate
    assert order[0].startswith("generate"), f"First call must be generate; got {order}"
    for i, step in enumerate(order):
        if step.startswith("generate-") and i > 0:
            # A generate after the initial one must be preceded by an assess
            prev = order[i - 1]
            assert prev.startswith("assess"), (
                f"generate at position {i} must be preceded by assess; "
                f"got '{prev}' before '{step}'. Order: {order}"
            )


# ── T-DR-009: synthesis goes through ingest_file ─────────────────────────────


@pytest.mark.asyncio
async def test_synthesis_routed_through_ingest_file() -> None:
    """T-DR-009: AC-F10-1/7d, AQ-v0.5-3 — synthesis via ingest_file, not direct write."""
    ingest_calls: list[Any] = []
    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _track_ingest(path: Any) -> Any:
        from app.ingest.pipeline import IngestResult

        ingest_calls.append(path)
        return IngestResult(page_id=uuid.uuid4(), status="completed")

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 1
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q"])),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(1)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch(
            "app.ops.deep_research._assess_sufficiency",
            new=AsyncMock(return_value=Sufficiency(sufficient=True, gaps=[])),
        ),
        patch(
            "app.ops.deep_research._synthesize",
            new=AsyncMock(return_value="# Synthesis\n\nContent."),
        ),
        patch("app.ingest.pipeline.ingest_file", side_effect=_track_ingest),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="docker networking", max_iter=1, token_budget=100_000
        )

    assert (
        len(ingest_calls) == 1
    ), f"ingest_file must be called exactly once for synthesis; got {len(ingest_calls)}"
    # The path must be in raw/sources/ (not vault/wiki/) — AQ-v0.5-3 / Do-NOT #5
    path_str = str(ingest_calls[0])
    assert (
        "raw/sources" in path_str or "deep-research-" in path_str
    ), f"ingest_file path must be under raw/sources/; got {path_str!r}"
    assert result.status == "converged"


# ── T-DR-010: exception → "error" status, never leaves "running" ──────────────


@pytest.mark.asyncio
async def test_exception_sets_error_status_never_running() -> None:
    """T-DR-010: AC-F10-2b, Do-NOT #7 — exception → status='error', never leaves 'running'."""
    finalize_calls: list[dict[str, Any]] = []

    async def _mock_finalize(**kwargs: Any) -> None:
        finalize_calls.append(kwargs)

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 3
        run.token_budget = 100_000
        return run

    async def _exploding_generate(p: Any, topic: str, prior: Any, *, max_queries: int) -> list[str]:
        raise RuntimeError("simulated provider failure")

    with (
        patch("app.ops.deep_research._generate_queries", side_effect=_exploding_generate),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._finalize_run_row", side_effect=_mock_finalize),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        result = await run_deep_research(
            vault_id="test", topic="topic", max_iter=3, token_budget=100_000
        )

    assert result.status == "error", f"status must be 'error' on exception; got {result.status!r}"
    assert result.error_message is not None, "error_message must be set on exception"
    assert "simulated provider failure" in result.error_message

    # finalize was called with status="error" (the finally block ran)
    assert finalize_calls, "finalize_run_row must be called even on exception"
    assert (
        finalize_calls[0]["status"] == "error"
    ), f"finalize called with status={finalize_calls[0]['status']!r}; expected 'error'"
    # Never left "running"
    assert finalize_calls[0]["status"] != "running", "Must never finalize as 'running'"


# ── T-DR-011: 3 hits → exactly 3 fetch calls ─────────────────────────────────


@pytest.mark.asyncio
async def test_three_hits_three_fetch_calls() -> None:
    """T-DR-011: AC-F10-7a — 3 SearXNG results → 3 fetch calls."""

    fetch_calls: list[list[Any]] = []

    async def _track_fetch(hits: list[Any], *, iteration: int = 1) -> list[Any]:
        from app.ops.deep_research import FetchedSource

        fetch_calls.append(hits)
        return [
            FetchedSource(url=h.url, title=h.title, content_md="c", iteration=iteration)
            for h in hits
        ]

    three_hits = _make_search_hits(3)

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 1
        run.token_budget = 100_000
        return run

    from app.ops.deep_research import Sufficiency

    with (
        patch("app.ops.deep_research._generate_queries", new=AsyncMock(return_value=["q"])),
        patch("app.ops.deep_research._search_searxng", new=AsyncMock(return_value=three_hits)),
        patch("app.ops.deep_research._fetch_and_extract", side_effect=_track_fetch),
        patch(
            "app.ops.deep_research._assess_sufficiency",
            new=AsyncMock(return_value=Sufficiency(sufficient=True, gaps=[])),
        ),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="s")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        await run_deep_research(vault_id="test", topic="topic", max_iter=1, token_budget=100_000)

    # fetch_and_extract was called once with all 3 hits
    total_hits = sum(len(b) for b in fetch_calls)
    assert total_hits == 3, f"expected 3 total hits fetched; got {total_hits}"


# ── T-DR-012: max_queries_per_iter not exceeded ───────────────────────────────


@pytest.mark.asyncio
async def test_max_queries_per_iter_not_exceeded() -> None:
    """T-DR-012: AC-F10-2d — max_queries_per_iter defaults to 5 and is never exceeded."""
    from app.ops.deep_research import MAX_QUERIES

    assert MAX_QUERIES >= 2, f"MAX_QUERIES must be at least 2; got {MAX_QUERIES}"
    assert MAX_QUERIES <= 10, f"MAX_QUERIES must be at most 10 (sane bound); got {MAX_QUERIES}"

    max_q_seen: list[int] = []

    from app.ops.deep_research import FetchedSource, Sufficiency

    async def _track_generate(p: Any, topic: str, prior: Any, *, max_queries: int) -> list[str]:
        max_q_seen.append(max_queries)
        return [f"q{i}" for i in range(max_queries)]  # return exactly max_queries queries

    async def _mock_create_run(**kwargs: Any) -> Any:
        run = MagicMock()
        run.id = uuid.uuid4()
        run.max_iter = 2
        run.token_budget = 100_000
        return run

    with (
        patch("app.ops.deep_research._generate_queries", side_effect=_track_generate),
        patch(
            "app.ops.deep_research._search_searxng",
            new=AsyncMock(return_value=_make_search_hits(1)),
        ),
        patch(
            "app.ops.deep_research._fetch_and_extract",
            new=AsyncMock(
                return_value=[FetchedSource(url="u", title="t", content_md="c", iteration=1)]
            ),
        ),
        patch(
            "app.ops.deep_research._assess_sufficiency",
            new=AsyncMock(return_value=Sufficiency(sufficient=False, gaps=["need more"])),
        ),
        patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="s")),
        patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=uuid.uuid4())),
        patch("app.ops.deep_research._create_run_row", side_effect=_mock_create_run),
        patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
        patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
        patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
        patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
        patch(
            "app.ops.deep_research.resolve_operation_provider",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
    ):
        from app.ops.deep_research import run_deep_research

        await run_deep_research(vault_id="test", topic="topic", max_iter=2, token_budget=100_000)

    for seen in max_q_seen:
        assert (
            seen <= MAX_QUERIES
        ), f"max_queries arg {seen} exceeds MAX_QUERIES={MAX_QUERIES} (AC-F10-2d)"


# ── T-DR-013: I9 static guard — no forbidden imports in ops/ ─────────────────


def test_no_forbidden_search_imports() -> None:
    """
    T-DR-013: AC-F10-3 / I9 — no tavily/ddg/duckduckgo/googlesearch/serpapi in ops/.

    Static scan of all .py files under backend/app/ops/, EXCEPT the sanctioned multi-provider
    web-search seam ``ops/web_search/`` (ADR-0066 amends I9: SearXNG stays the default/bundled
    backend, but Tavily/SerpApi/Firecrawl/Brave/Ollama-Web are ALLOWED as opt-in, off-by-default
    adapters). Everywhere ELSE these names must not appear — nobody may sneak an alternative
    backend outside the seam.
    """
    import re
    from pathlib import Path

    ops_dir = Path(__file__).parent.parent / "app" / "ops"
    assert ops_dir.exists(), f"ops/ directory not found at {ops_dir}"

    # ADR-0066/ADR-0070: the web_search/ package is the ONE place these provider names may appear.
    seam_dir = ops_dir / "web_search"

    forbidden_pattern = re.compile(
        r"\b(tavily|duckduckgo|ddg|googlesearch|serpapi|google[-_]search[-_]results)\b",
        re.IGNORECASE,
    )

    violations: list[str] = []
    for py_file in ops_dir.rglob("*.py"):
        if seam_dir in py_file.parents:
            continue  # sanctioned multi-provider seam (ADR-0066)
        content = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), 1):
            if forbidden_pattern.search(line):
                violations.append(
                    f"{py_file.relative_to(ops_dir.parent.parent)}:{lineno}: {line.strip()}"
                )

    assert not violations, (
        "I9 violation: forbidden web-search library name found OUTSIDE the ops/web_search/ seam:\n"
        + "\n".join(violations)
        + "\nAlternative backends live ONLY in ops/web_search/ (ADR-0066); "
        "elsewhere only SearXNG is permitted."
    )
