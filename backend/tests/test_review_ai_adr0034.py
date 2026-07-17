"""
F9 HITL Review Queue — ADR-0034 [AI] (ai-agent-engineer) scope tests.

Covers the three filled AI seams in app/ops/review.py:
  _llm_propose_reviews  (§4.3)  — single bounded provider call, capped, degrade-on-timeout.
  _llm_sweep_judge      (§6.3)  — conservative default-to-keep; never resolves `confirm`.
  _run_generation       (§5)    — capability-aware (I6): orchestrated route returns a
                                  GenerationOutcome(wiki_page=...) the caller writes via
                                  write_wiki_page; delegated (agentic) route returns
                                  GenerationOutcome(created_page_id=...) — the agent already
                                  wrote via MCP write_page, caller skips the write (I1).

Tests (ADR-0034 §11.2 [AI]):
  T-AI-001  anti-spam gate skips the LLM call on a trivial run (zero proposals, zero cost)
  T-AI-002  propose call is bounded to exactly one provider call
  T-AI-003  propose output is capped at REVIEW_PROPOSE_MAX_ITEMS (truncated)
  T-AI-004  propose degrades to [] on timeout (never raises)
  T-AI-005  propose returns [] when no provider resolves (I6 — no silent default)
  T-AI-006  Create runs the bounded loop and returns a WikiPage written via write_wiki_page
  T-AI-007  Create degrades / item stays pending on loop failure (502, no partial write)
  T-AI-008  sweep Pass-2 keeps ALL pending on parse ambiguity (default-to-keep)
  T-AI-009  sweep Pass-2 NEVER resolves `confirm` items (filtered before + after)
  T-AI-010  sweep Pass-2 returns set() when REVIEW_SWEEP_LLM_ENABLED is false
  T-AI-011  _resolve_create_page_type heuristic (§5.2): cues + entity shape + never 'source'

The provider is always mocked — no real Ollama/API call (reuses the deep-research mock shape).
Reuses the ADR-0034 SQLite fixtures (review_env_0034) from test_review_adr0034.py.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text as sa_text

# Reuse the ADR-0034 fixtures + DB helpers (same SQLite proposal-model schema).
from tests.test_review_adr0034 import (  # noqa: F401  (fixtures imported for pytest discovery)
    _insert_page,
    _insert_proposal,
    review_client_0034,
    review_env_0034,
)

# ── Provider mock (chat-based seams) ─────────────────────────────────────────


def _make_chat_provider(response: str, *, sleep_forever: bool = False) -> Any:
    """
    Build a mock InferenceProvider whose complete() returns *response* once.

    Matches the real call shape used by review.py::_chat_collect, which now uses the single-turn
    complete() seam (not the agentic chat() loop) so the CLI provider does not hang — ADR-0076.
    Tracks provider call count on provider._chat_calls[0].
    If sleep_forever, complete() hangs so asyncio.wait_for trips a TimeoutError.
    """
    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_complete(system: str, prompt: str, *, max_tokens: int) -> str:
        provider._chat_calls[0] += 1
        if sleep_forever:
            import asyncio

            await asyncio.sleep(60)
        return response

    provider.complete = mock_complete

    def bind_acc(acc: Any) -> None:
        provider._bound_acc = acc

    provider.bind_accumulator = MagicMock(side_effect=bind_acc)
    return provider


def _fake_config_row(*, max_iter: int = 3, token_budget: int = 60_000) -> Any:
    row = MagicMock()
    row.max_iter = max_iter
    row.token_budget = token_budget
    row.model_id = "test-model"
    row.provider_type = "local"
    return row


def _patch_resolve(provider: Any, config_row: Any | None = None) -> Any:
    """Patch resolve_operation_provider to return (provider, config_row)."""
    cfg = config_row or _fake_config_row()
    return patch(
        "app.ops.review.resolve_operation_provider",
        new=AsyncMock(return_value=(provider, cfg)),
    )


# ── T-AI-001: anti-spam gate skips the LLM call on a trivial run ─────────────


class TestAntiSpamGate:
    async def test_trivial_run_skips_llm_call_zero_cost(
        self,
        review_env_0034: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        T-AI-001: a run below ALL gate conditions must NOT invoke the provider — zero cost.

        v1.5.2 lowered the default review_propose_min_pages 4 → 1, so this test sets the threshold
        explicitly to exercise the gate-SKIP mechanism itself (independent of the shipped default):
        one tiny page < min_pages, tiny body < min_chars, no dangling links, no unwritten
        suggestion → gate short-circuits before the LLM.
        """
        from app.config import settings as _cfg
        from app.ops import review as review_mod

        monkeypatch.setattr(_cfg, "review_propose_min_pages", 4)

        # One tiny written page → below the (explicit) MIN_PAGES=4 and MIN_CHARS (10k); no dangling.
        page = MagicMock()
        page.id = uuid.uuid4()
        page.title = "Tiny"
        page.page_type = "concept"
        page.file_path = "wiki/concepts/tiny.md"  # non-existent → gate falls back to title length

        provider = _make_chat_provider("{}")

        # If the LLM seam is reached it would call the provider; assert it never is.
        with (
            _patch_resolve(provider),
            patch.object(
                review_mod, "_llm_propose_reviews", wraps=review_mod._llm_propose_reviews
            ) as spy,
        ):
            await review_mod.propose_reviews(
                vault_id="test-vault",
                analysis=None,
                written_pages=[page],
                origin_source="raw/sources/tiny.md",
            )

        # Gate must short-circuit before the LLM stub is invoked.
        assert spy.call_count == 0, "anti-spam gate must skip the LLM call on a trivial run"
        assert provider._chat_calls[0] == 0, "no provider call → zero cost"

        async with review_env_0034["session_factory"]() as sess:
            count = (
                await sess.execute(
                    sa_text("SELECT COUNT(*) FROM review_items WHERE vault_id='test-vault'")
                )
            ).scalar_one()
        assert count == 0, "trivial run emits zero proposals"


# ── T-AI-002..005: the single bounded proposal call ──────────────────────────


class TestProposeBounded:
    @staticmethod
    def _analysis_with_suggested(title: str) -> Any:
        """A real Analysis whose suggested_pages[0] is NOT in the written set → gate passes."""
        from app.ingest.schemas import Analysis, PageType, SuggestedPage

        return Analysis(
            topics=["t"],
            entities=[],
            language="en",
            suggested_pages=[SuggestedPage(title=title, type=PageType.CONCEPT)],
            summary="s",
        )

    async def test_propose_makes_exactly_one_provider_call(self) -> None:
        """T-AI-002: _llm_propose_reviews makes exactly ONE chat() call (no loop, no retry)."""
        from app.ops import review as review_mod

        provider = _make_chat_provider(
            '{"proposals": [{"type": "suggestion", "proposed_title": "X", ' '"rationale": "gap"}]}'
        )
        with _patch_resolve(provider):
            out = await review_mod._llm_propose_reviews(
                vault_id="test-vault",
                analysis=self._analysis_with_suggested("Unwritten Topic"),
                written_pages=[],
                existing_titles=["Existing"],
            )
        assert provider._chat_calls[0] == 1, "exactly one provider call (I7)"
        assert len(out) == 1
        assert out[0].item_type == "suggestion"

    async def test_propose_caps_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-AI-003: output is truncated to REVIEW_PROPOSE_MAX_ITEMS (Do-NOT #9)."""
        from app import config as cfg
        from app.ops import review as review_mod

        monkeypatch.setattr(cfg.settings, "review_propose_max_items", 3)

        # Model returns 10 valid proposals.
        items = ",".join(
            f'{{"type": "suggestion", "proposed_title": "P{i}", "rationale": "r"}}'
            for i in range(10)
        )
        provider = _make_chat_provider(f'{{"proposals": [{items}]}}')

        with _patch_resolve(provider):
            out = await review_mod._llm_propose_reviews(
                vault_id="test-vault",
                analysis=self._analysis_with_suggested("Unwritten"),
                written_pages=[],
                existing_titles=[],
            )
        assert len(out) == 3, "must truncate to REVIEW_PROPOSE_MAX_ITEMS"

    async def test_propose_degrades_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-AI-004: a timed-out provider call → [] (degrade, never raise)."""
        from app import config as cfg
        from app.ops import review as review_mod

        monkeypatch.setattr(cfg.settings, "review_propose_timeout_seconds", 0.05)
        provider = _make_chat_provider("{}", sleep_forever=True)

        with _patch_resolve(provider):
            out = await review_mod._llm_propose_reviews(
                vault_id="test-vault",
                analysis=self._analysis_with_suggested("Unwritten"),
                written_pages=[],
                existing_titles=[],
            )
        assert out == [], "timeout must degrade to empty list, not raise"

    async def test_propose_no_provider_returns_empty(self) -> None:
        """T-AI-005: no provider resolves → [] (I6 — no silent default backend)."""
        from app.ops import review as review_mod

        with patch(
            "app.ops.review.resolve_operation_provider",
            new=AsyncMock(return_value=None),
        ):
            out = await review_mod._llm_propose_reviews(
                vault_id="test-vault",
                analysis=self._analysis_with_suggested("Unwritten"),
                written_pages=[],
                existing_titles=[],
            )
        assert out == []


# ── T-AI-006..007: the Create on-demand generation ───────────────────────────


class TestCreateGeneration:
    async def test_corpus_review_create_preserves_generation_identity(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """Accepted review-only corpus pages remain idempotent in later auto runs."""
        from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
        from app.ops import review as review_mod

        key = "corpus:comparison:" + "a" * 64
        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="suggestion",
            proposed_title="Comparison of Alpha and Beta",
            proposed_page_type="comparison",
            rationale="Compare these entities using shared evidence.",
            proposal_origin="corpus",
            content_key=key,
        )
        origin = f"review:{item_id_str}"
        provider_page = WikiPage(
            title="Alpha and Beta",
            type=PageType.CONCEPT,  # provider drift must be corrected at the boundary
            content="Comparison grounded in [[Alpha]] and [[Beta]].",
            frontmatter=WikiFrontmatter(
                type=PageType.CONCEPT,
                title="Alpha and Beta",
                sources=[origin],
                lang="en",
            ),
        )
        analysis = MagicMock(language="en", summary="s")
        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        caps = MagicMock(mode="local", supports_agentic_loop=False)
        caps.name = "prov"
        provider.capabilities = MagicMock(return_value=caps)
        provider.analyze = AsyncMock(return_value=analysis)
        provider.generate = AsyncMock(return_value=[provider_page])
        captured: dict[str, Any] = {}

        async def fake_write(session: Any, page: Any, origin_source: str) -> Any:
            captured["page"] = page
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=_fake_config_row()),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=provider),
            patch("app.ingest.orchestrator.write_wiki_page", new=fake_write),
        ):
            # mode="generate" — testing the LLM generation path (ADR-0079 §2: stub is now default).
            await review_mod.create_page_from_review(uuid.UUID(item_id_str), mode="generate")

        assert captured["page"].type is PageType.COMPARISON
        assert captured["page"].frontmatter.type is PageType.COMPARISON
        assert captured["page"].frontmatter.synapse_generation_key == key

    async def test_create_runs_loop_and_writes_via_write_wiki_page(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """
        T-AI-006: Create runs the BOUNDED orchestrated loop (mocked provider) and the produced
        WikiPage is written through write_wiki_page (I1). Item → status=created.
        """
        from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
        from app.ops import review as review_mod

        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="missing-page",
            proposed_title="Quantum Computing",
            proposed_page_type="concept",
            rationale="Referenced but absent",
        )

        # Mock provider for run_orchestrated_loop: analyze() then generate() one valid page.
        origin = f"review:{item_id_str}"
        wiki_page = WikiPage(
            title="Quantum Computing",
            type=PageType.CONCEPT,
            content="Body of the page.",
            frontmatter=WikiFrontmatter(
                type=PageType.CONCEPT,
                title="Quantum Computing",
                sources=[origin],
                lang="en",
            ),
        )
        analysis = MagicMock()
        analysis.language = "en"
        analysis.summary = "s"

        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        # Orchestrated route (I6): supports_agentic_loop is False → analyze→generate loop.
        _caps = MagicMock(mode="local", supports_agentic_loop=False)
        _caps.name = "prov"
        provider.capabilities = MagicMock(return_value=_caps)
        provider.analyze = AsyncMock(return_value=analysis)
        provider.generate = AsyncMock(return_value=[wiki_page])

        captured: dict[str, Any] = {}

        async def fake_write(session: Any, page: Any, origin_source: str) -> Any:
            captured["page"] = page
            captured["origin"] = origin_source
            written = MagicMock()
            written.id = uuid.uuid4()
            return written

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=_fake_config_row()),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=provider),
            patch("app.ingest.orchestrator.write_wiki_page", new=fake_write),
        ):
            # mode="generate" — testing the BOUNDED orchestrated loop (ADR-0079 §2).
            result = await review_mod.create_page_from_review(
                uuid.UUID(item_id_str), mode="generate"
            )

        # The bounded loop ran (analyze once, generate at least once).
        provider.analyze.assert_awaited_once()
        assert provider.generate.await_count >= 1
        # The produced WikiPage was written via write_wiki_page (I1).
        assert "page" in captured, "write_wiki_page must be called with the produced page"
        assert captured["page"].title == "Quantum Computing"
        assert result.status == "created"
        assert result.resolution == "created"

    async def test_create_502_on_loop_failure_item_stays_pending(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """
        T-AI-007: provider/loop failure → UpstreamError (→ HTTP 502); item left pending; no
        write (Do-NOT #5 / §5.3 — no partial create).
        """
        from app.errors import SynapseError
        from app.ops import review as review_mod

        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="missing-page",
            proposed_title="Will Fail",
            proposed_page_type="concept",
        )

        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        # Orchestrated route (I6): supports_agentic_loop is False → analyze→generate loop.
        _caps = MagicMock(mode="local", supports_agentic_loop=False)
        _caps.name = "prov"
        provider.capabilities = MagicMock(return_value=_caps)
        provider.analyze = AsyncMock(side_effect=RuntimeError("provider exploded"))

        write_called = {"n": 0}

        async def fake_write(session: Any, page: Any, origin_source: str) -> Any:
            write_called["n"] += 1
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=_fake_config_row()),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=provider),
            patch("app.ingest.orchestrator.write_wiki_page", new=fake_write),
        ):
            with pytest.raises(SynapseError) as exc_info:
                # mode="generate" — testing provider failure on LLM path (ADR-0079 §2).
                await review_mod.create_page_from_review(uuid.UUID(item_id_str), mode="generate")

        assert exc_info.value.status_code == 502
        assert write_called["n"] == 0, "no partial write on loop failure (§5.3)"

        async with review_env_0034["session_factory"]() as sess:
            row = (
                await sess.execute(
                    sa_text("SELECT status FROM review_items WHERE id=:id"),
                    {"id": item_id_str},
                )
            ).one()
        assert row.status == "pending", "item must stay pending on failure (§5.3)"

    async def test_create_delegated_uses_agent_written_id_skips_write(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """
        T-AI-006b: an AGENTIC provider (capabilities().supports_agentic_loop=True) routes to the
        DELEGATED path (I6). The agent already wrote the page via MCP write_page; _run_generation
        resolves the created id from the written ids (preferring the title match) and the caller
        MUST NOT call write_wiki_page again (I1 — one write per page).
        """
        from app.ops import review as review_mod

        # The page the delegated agent "wrote" via MCP write_page (title == proposed title).
        page_id = await _insert_page(review_env_0034, title="Quantum Computing")

        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="missing-page",
            proposed_title="Quantum Computing",
            proposed_page_type="concept",
            rationale="Referenced but absent",
        )

        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        # Delegated route (I6): supports_agentic_loop is True → delegate the whole ingest.
        _caps = MagicMock(mode="cli", supports_agentic_loop=True)
        _caps.name = "CliAgentProvider"
        provider.capabilities = MagicMock(return_value=_caps)

        # _delegate_ingest returns (converged, pages_written, written_page_ids).
        delegate_mock = AsyncMock(return_value=(True, 1, [page_id]))

        write_called = {"n": 0}

        async def fake_write(session: Any, page: Any, origin_source: str) -> Any:
            write_called["n"] += 1
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=_fake_config_row()),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=provider),
            patch("app.ingest.orchestrator._delegate_ingest", new=delegate_mock),
            patch("app.ingest.orchestrator.write_wiki_page", new=fake_write),
        ):
            # mode="generate" — testing the DELEGATED (agentic) path (ADR-0079 §2).
            result = await review_mod.create_page_from_review(
                uuid.UUID(item_id_str), mode="generate"
            )

        # Delegated path was taken (analyze/generate never touched — they'd raise on a CLI provider).
        delegate_mock.assert_awaited_once()
        # The caller did NOT double-write — the agent already wrote via MCP write_page (I1).
        assert write_called["n"] == 0, "delegated path must NOT call write_wiki_page (I1)"
        assert result.status == "created"
        assert result.resolution == "created"
        # created_page_id is the agent-written page (title match preferred).
        assert str(result.created_page_id) == page_id

    async def test_create_delegated_empty_writes_502_item_pending(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """
        T-AI-007b: a delegated run where the agent writes NOTHING (empty written_page_ids) →
        UpstreamError (→ HTTP 502); item left pending; no write in the caller (§5.3 — no
        partial create).
        """
        from app.errors import SynapseError
        from app.ops import review as review_mod

        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="missing-page",
            proposed_title="Nothing Written",
            proposed_page_type="concept",
        )

        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        _caps = MagicMock(mode="cli", supports_agentic_loop=True)
        _caps.name = "CliAgentProvider"
        provider.capabilities = MagicMock(return_value=_caps)

        # Agent ran but wrote no pages → empty written_page_ids.
        delegate_mock = AsyncMock(return_value=(False, 0, []))

        write_called = {"n": 0}

        async def fake_write(session: Any, page: Any, origin_source: str) -> Any:
            write_called["n"] += 1
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=_fake_config_row()),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=provider),
            patch("app.ingest.orchestrator._delegate_ingest", new=delegate_mock),
            patch("app.ingest.orchestrator.write_wiki_page", new=fake_write),
        ):
            with pytest.raises(SynapseError) as exc_info:
                # mode="generate" — testing empty delegated write on LLM path (ADR-0079 §2).
                await review_mod.create_page_from_review(uuid.UUID(item_id_str), mode="generate")

        assert exc_info.value.status_code == 502
        assert write_called["n"] == 0, "no write when the delegated agent produced nothing (§5.3)"

        async with review_env_0034["session_factory"]() as sess:
            row = (
                await sess.execute(
                    sa_text("SELECT status FROM review_items WHERE id=:id"),
                    {"id": item_id_str},
                )
            ).one()
        assert row.status == "pending", "item must stay pending on delegated failure (§5.3)"


# ── T-AI-008..010: sweep Pass-2 conservative judgment ────────────────────────


class TestSweepJudge:
    @staticmethod
    def _items(*types_titles: tuple[str, str]) -> list[Any]:
        items = []
        for item_type, title in types_titles:
            it = MagicMock()
            it.id = str(uuid.uuid4())
            it.item_type = item_type
            it.proposed_title = title
            it.rationale = "r"
            items.append(it)
        return items

    async def test_keeps_all_on_parse_ambiguity(self) -> None:
        """T-AI-008: an unparseable verdict → set() (keep ALL pending, default-to-keep)."""
        from app.ops import review as review_mod

        items = self._items(("missing-page", "A"), ("suggestion", "B"))
        provider = _make_chat_provider("I am not JSON, just prose with no verdict.")

        with _patch_resolve(provider):
            out = await review_mod._llm_sweep_judge(
                vault_id="test-vault",
                candidate_items=items,
                existing_titles=["A", "B"],
            )
        assert out == set(), "parse ambiguity must keep all pending (Do-NOT #7)"

    async def test_never_resolves_confirm(self) -> None:
        """
        T-AI-009: even if the model returns a confirm item's id, it is NEVER in the resolve set
        (filtered before the call and again after — Do-NOT #7).
        """
        from app.ops import review as review_mod

        items = self._items(("confirm", "C"), ("missing-page", "M"))
        confirm_id = items[0].id
        missing_id = items[1].id

        # Model maliciously returns BOTH ids.
        provider = _make_chat_provider(f'{{"resolve": ["{confirm_id}", "{missing_id}"]}}')
        with _patch_resolve(provider):
            out = await review_mod._llm_sweep_judge(
                vault_id="test-vault",
                candidate_items=items,
                existing_titles=["M"],
            )
        assert confirm_id not in out, "confirm must NEVER be auto-resolved (Do-NOT #7)"
        assert missing_id in out, "the confident missing-page id may be resolved"

    async def test_disabled_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-AI-010: REVIEW_SWEEP_LLM_ENABLED=false → set() without any provider call."""
        from app import config as cfg
        from app.ops import review as review_mod

        monkeypatch.setattr(cfg.settings, "review_sweep_llm_enabled", False)
        provider = _make_chat_provider('{"resolve": ["x"]}')

        with _patch_resolve(provider):
            out = await review_mod._llm_sweep_judge(
                vault_id="test-vault",
                candidate_items=self._items(("missing-page", "A")),
                existing_titles=["A"],
            )
        assert out == set()
        assert provider._chat_calls[0] == 0, "disabled gate must make no provider call"

    async def test_no_provider_returns_empty(self) -> None:
        """no provider resolves → set() (keep all pending, I6)."""
        from app.ops import review as review_mod

        with patch(
            "app.ops.review.resolve_operation_provider",
            new=AsyncMock(return_value=None),
        ):
            out = await review_mod._llm_sweep_judge(
                vault_id="test-vault",
                candidate_items=self._items(("missing-page", "A")),
                existing_titles=["A"],
            )
        assert out == set()


# ── T-AI-011: Create page-type heuristic (§5.2) ──────────────────────────────


class TestCreatePageTypeHeuristic:
    def test_llmwiki_detectpagetype_parity(self) -> None:
        """
        T-AI-011: type derivation is 1:1 with nashsu/llm_wiki detectPageType
        (review-create-page.ts:57-67). Owner decision 2026-07-12: default → query.
        """
        from app.ingest.schemas import PageType
        from app.ops.review import _resolve_create_page_type as r

        # Explicit valid type is a hint when no stronger textual cue exists.
        assert r("Anything", "comparison", None) == PageType.COMPARISON
        # 'source' explicit → dropped to text rules (never returns SOURCE); default → query.
        assert r("Some Document", "source", None) == PageType.QUERY
        # entity/concept keyword paths (D4).
        assert r("Missing entity Foo", None, None) == PageType.ENTITY
        assert r("Add a concept page for entropy", None, None) == PageType.CONCEPT
        # Comparison cue (substring 'compare'/'comparison'/比较).
        assert r("Comparison of Docker and Podman", None, None) == PageType.COMPARISON
        assert r("Compare containers", None, None) == PageType.COMPARISON
        # Synthesis cue (synthesis/综合).
        assert r("Synthesis of container tech", None, None) == PageType.SYNTHESIS
        # Text cues are authoritative over a conflicting provider hint.
        assert r("Comparison of Docker and Podman", "entity", None) == PageType.COMPARISON
        assert r("Synthesis of container tech", "comparison", None) == PageType.SYNTHESIS
        # Structural cues must also win when generic nouns appear in realistic rationales.
        assert r("Container options", None, "Compare these entities") == PageType.COMPARISON
        assert r("Container landscape", None, "Synthesis of related concepts") == PageType.SYNTHESIS
        # missing-page item_type → concept (rule 5).
        assert r("Kubernetes", None, None, "missing-page") == PageType.CONCEPT
        # suggestion / contradiction / plain fallback → query (llm_wiki default).
        assert r("entropy", None, None) == PageType.QUERY
        assert r("Some open question", None, None, "suggestion") == PageType.QUERY
        assert r("Conflicting claims about X", None, None, "contradiction") == PageType.QUERY
        # llm_wiki does NOT treat bare "vs" / "overview of" as cues → query (faithful parity).
        assert r("Docker vs Podman", None, None) == PageType.QUERY


class TestProposalMergeCaps:
    @staticmethod
    def _proposal(title: str, item_type: str = "suggestion", rationale: str = "r") -> Any:
        from app.ops.review import ProposalDTO

        return ProposalDTO(
            item_type=item_type,
            proposed_title=title,
            proposed_page_type="query",
            rationale=rationale,
        )

    def test_rule_and_ai_have_separate_caps_so_ai_cannot_be_starved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import settings
        from app.ops.review import _merge_proposals_bounded

        monkeypatch.setattr(settings, "review_rule_propose_max_items", 8, raising=False)
        monkeypatch.setattr(settings, "review_propose_max_items", 12)
        rules = [self._proposal(f"Rule {i}", "missing-page") for i in range(20)]
        ai = [self._proposal(f"AI {i}") for i in range(20)]

        merged = _merge_proposals_bounded(rules, ai)

        assert len(merged) == 20
        assert sum(p.item_type == "missing-page" for p in merged) == 8
        assert sum(p.item_type == "suggestion" for p in merged) == 12

    def test_merge_deduplicates_stably_without_consuming_ai_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import settings
        from app.ops.review import _merge_proposals_bounded

        monkeypatch.setattr(settings, "review_rule_propose_max_items", 1, raising=False)
        monkeypatch.setattr(settings, "review_propose_max_items", 2)
        rules = [self._proposal("Duplicate", "missing-page", "thin rule rationale")]
        ai = [
            self._proposal("Duplicate", "missing-page", "rich AI rationale and queries"),
            self._proposal("AI One"),
            self._proposal("AI One"),
            self._proposal("AI Two"),
        ]

        merged = _merge_proposals_bounded(rules, ai)

        assert [(p.item_type, p.proposed_title) for p in merged] == [
            ("missing-page", "Duplicate"),
            ("suggestion", "AI One"),
            ("suggestion", "AI Two"),
        ]
        assert merged[0] is ai[0]
        assert merged[0].rationale == "rich AI rationale and queries"

    def test_config_cannot_raise_global_hard_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings
        from app.ops.review import _merge_proposals_bounded

        monkeypatch.setattr(settings, "review_rule_propose_max_items", 999, raising=False)
        monkeypatch.setattr(settings, "review_propose_max_items", 999)
        rules = [self._proposal(f"Rule {i}", "missing-page") for i in range(30)]
        ai = [self._proposal(f"AI {i}") for i in range(30)]

        merged = _merge_proposals_bounded(rules, ai)

        assert len(merged) == 20
        assert sum(p.item_type == "missing-page" for p in merged) == 8
        assert sum(p.item_type == "suggestion" for p in merged) == 12


class TestCleanCandidateTitle:
    def test_llmwiki_clean_candidate_title_parity(self) -> None:
        """D7: title cleaning mirrors nashsu/llm_wiki cleanCandidateTitle (review-create-page.ts)."""
        from app.ops.review import _clean_candidate_title as c

        assert c("Missing page: Kubernetes") == "Kubernetes"
        assert c("Create: Cost Model") == "Cost Model"
        assert c("Add - Ingress Controller") == "Ingress Controller"
        assert c('"Docker"  ') == "Docker"
        assert c("missing Kafka") == "Kafka"
        assert c("Entropy concept page") == "Entropy"
        assert c("Container entities") == "Container"
        assert c("  [Payments] .") == "Payments"
        # A clean title is returned unchanged.
        assert c("Multi-Cloud Cost Extraction") == "Multi-Cloud Cost Extraction"
