"""
F9 HITL Review Queue — ADR-0034 [AI] (ai-agent-engineer) scope tests.

Covers the three filled AI seams in app/ops/review.py:
  _llm_propose_reviews  (§4.3)  — single bounded provider call, capped, degrade-on-timeout.
  _llm_sweep_judge      (§6.3)  — conservative default-to-keep; never resolves `confirm`.
  _run_generation       (§5)    — bounded run_orchestrated_loop; returns a WikiPage written
                                  through write_wiki_page (I1).

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
from collections.abc import AsyncIterator
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
    Build a mock InferenceProvider whose chat() yields *response* once.

    Matches the real call shape used by review.py::_chat_collect:
        async for chunk in await provider.chat(messages=..., retrieval_context=...)
    Tracks chat call count on provider._chat_calls[0].
    If sleep_forever, chat() hangs so asyncio.wait_for trips a TimeoutError.
    """
    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            if sleep_forever:
                import asyncio

                await asyncio.sleep(60)
            yield response

        return _gen()

    provider.chat = mock_chat

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
    """Patch _resolve_review_provider to return (provider, config_row)."""
    cfg = config_row or _fake_config_row()
    return patch(
        "app.ops.review._resolve_review_provider",
        new=AsyncMock(return_value=(provider, cfg)),
    )


# ── T-AI-001: anti-spam gate skips the LLM call on a trivial run ─────────────


class TestAntiSpamGate:
    async def test_trivial_run_skips_llm_call_zero_cost(
        self,
        review_env_0034: dict[str, Any],
    ) -> None:
        """
        T-AI-001: a trivial run (1 tiny page, no dangling links, no unwritten suggestion)
        must NOT invoke the provider — zero proposals, zero cost.
        """
        from app.ops import review as review_mod

        # One tiny written page → below MIN_PAGES (4) and MIN_CHARS (10k); no dangling links.
        page = MagicMock()
        page.id = uuid.uuid4()
        page.title = "Tiny"
        page.page_type = "concept"

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
            "app.ops.review._resolve_review_provider",
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
        provider.capabilities = MagicMock(return_value=MagicMock(name="prov", mode="local"))
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
            result = await review_mod.create_page_from_review(uuid.UUID(item_id_str))

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
        T-AI-007: provider/loop failure → HTTPException(502); item left pending; no write
        (Do-NOT #5 / §5.3 — no partial create).
        """
        from app.ops import review as review_mod
        from fastapi import HTTPException

        item_id_str = await _insert_proposal(
            review_env_0034,
            item_type="missing-page",
            proposed_title="Will Fail",
            proposed_page_type="concept",
        )

        provider = MagicMock()
        provider.bind_accumulator = MagicMock()
        provider.capabilities = MagicMock(return_value=MagicMock(name="prov", mode="local"))
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
            with pytest.raises(HTTPException) as exc_info:
                await review_mod.create_page_from_review(uuid.UUID(item_id_str))

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
            "app.ops.review._resolve_review_provider",
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
    def test_heuristic_and_never_source(self) -> None:
        """T-AI-011: §5.2 heuristic — cues, entity shape, default concept, never source."""
        from app.ingest.schemas import PageType
        from app.ops.review import _resolve_create_page_type

        # Explicit valid type honored.
        assert _resolve_create_page_type("Anything", "comparison", None) == PageType.COMPARISON
        # 'source' explicit → dropped to heuristic (never returns SOURCE).
        assert _resolve_create_page_type("Some Document", "source", None) != PageType.SOURCE
        # Comparison cue.
        assert _resolve_create_page_type("Docker vs Podman", None, None) == PageType.COMPARISON
        # Synthesis cue.
        assert _resolve_create_page_type("Overview of Containers", None, None) == PageType.SYNTHESIS
        # Proper-noun entity shape.
        assert _resolve_create_page_type("Linus Torvalds", None, None) == PageType.ENTITY
        # Default → concept.
        assert _resolve_create_page_type("entropy", None, None) == PageType.CONCEPT
