"""
R9-3 (v0.9) — purpose.md scope-drift suggestions.

Covers app/ops/review.py::generate_purpose_suggestion + apply_purpose_suggestion and the
approve routing in create_page_from_review for the new `purpose-suggestion` ReviewItem type.

Tests:
  T-R93-001  drift detected → ONE purpose-suggestion ReviewItem emitted (mock provider)
  T-R93-002  in-scope verdict → NO ReviewItem emitted (no queue spam, single call)
  T-R93-003  throttle: an existing PENDING purpose-suggestion blocks a new one (zero cost)
  T-R93-004  throttle: fewer than N sources since last check → skip (zero cost)
  T-R93-005  approve applies the suggestion to purpose.md + bumps data_version + marks created
  T-R93-006  provider failure → NO item, and the call never raises (ingest still completes)
  T-R93-007  bounded: exactly ONE chat() call, no retry
  T-R93-008  `purpose-suggestion` is an accepted item_type (no migration — Text column)

The provider is always mocked — no real Ollama/API call. Reuses the ADR-0034 SQLite fixtures
(review_env_0034) which build the proposal-model schema and set settings.vault_path=tmp_path.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text as sa_text

# Reuse the ADR-0034 fixtures + DB helpers (same SQLite proposal-model schema + tmp vault).
from tests.test_review_adr0034 import (  # noqa: F401  (fixtures imported for pytest discovery)
    review_client_0034,
    review_env_0034,
)

# A model response that reports scope drift (valid drift verdict).
_DRIFT_JSON = (
    '{"in_scope": false, "theme": "Home Automation", '
    '"why": "The vault purpose is about ML research, but recurring home-automation content '
    'has appeared.", '
    '"addition": "## Home Automation\\n\\nThis vault also tracks home-automation notes and '
    'device integrations."}'
)
_IN_SCOPE_JSON = '{"in_scope": true}'


# ── Provider mock ────────────────────────────────────────────────────────────────


def _make_chat_provider(response: str, *, fail: bool = False) -> Any:
    """Mock InferenceProvider whose complete() returns *response* once (or raises if fail).

    review.py::_chat_collect now uses the single-turn complete() seam (ADR-0076) so the CLI
    provider does not hang on the agentic chat() loop.
    """
    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_complete(system: str, prompt: str, *, max_tokens: int) -> str:
        provider._chat_calls[0] += 1
        if fail:
            raise RuntimeError("provider boom")
        return response

    provider.complete = mock_complete
    provider.bind_accumulator = MagicMock()
    return provider


def _fake_config_row() -> Any:
    row = MagicMock()
    row.max_iter = 3
    row.token_budget = 60_000
    row.model_id = "test-model"
    row.provider_type = "local"
    return row


def _patch_resolve(provider: Any) -> Any:
    return patch(
        "app.ops.review.resolve_operation_provider",
        new=AsyncMock(return_value=(provider, _fake_config_row())),
    )


# ── DB helpers (source-page insertion with type + created_at control) ─────────────


async def _insert_source_page(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    title: str,
    created_at: str = "datetime('now')",
) -> str:
    """Insert a `type='source'` page. created_at is a raw SQL expression string."""
    page_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, content_hash, pinned, "
                " deleted_at, created_at, updated_at) "
                f"VALUES (:id, :vault_id, :fp, :title, 'source', :hash, 0, "
                f"NULL, {created_at}, datetime('now'))"
            ),
            {
                "id": page_id,
                "vault_id": vault_id,
                "fp": f"raw/sources/{title.lower().replace(' ', '_')}.md",
                "title": title,
                "hash": "aabbcc",
            },
        )
        await sess.commit()
    return page_id


def _written_page(title: str = "New Note") -> Any:
    page = MagicMock()
    page.id = uuid.uuid4()
    page.title = title
    page.page_type = "concept"
    return page


async def _count_purpose_suggestions(env: dict[str, Any], *, status: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM review_items WHERE item_type='purpose-suggestion'"
    if status is not None:
        q += f" AND status='{status}'"
    async with env["session_factory"]() as sess:
        return int((await sess.execute(sa_text(q))).scalar_one())


def _analysis(topics: list[str], summary: str) -> Any:
    from app.ingest.schemas import Analysis, PageType, SuggestedPage

    return Analysis(
        topics=topics,
        entities=[],
        language="en",
        suggested_pages=[SuggestedPage(title=topics[0], type=PageType.CONCEPT)],
        summary=summary,
    )


# ── T-R93-001: drift detected → item emitted ─────────────────────────────────────


class TestDriftDetection:
    async def test_drift_emits_one_item(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-001: a drift verdict emits exactly ONE purpose-suggestion (mock provider)."""
        from app.ops import review as review_mod

        # Seed enough source pages to pass throttle 2 (default N=3).
        for i in range(3):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_DRIFT_JSON)
        with _patch_resolve(provider):
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["home automation"], "smart-home notes"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is not None
        assert item.item_type == "purpose-suggestion"
        assert item.proposed_title == "Home Automation"
        assert provider._chat_calls[0] == 1
        assert await _count_purpose_suggestions(review_env_0034) == 1
        # The exact addition markdown is retrievable for the apply step.
        addition = review_mod._extract_purpose_addition(item.rationale)
        assert addition is not None and "Home Automation" in addition


# ── T-R93-002: in-scope → no item ────────────────────────────────────────────────


class TestInScope:
    async def test_in_scope_emits_nothing(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-002: an in-scope verdict creates no ReviewItem (no spam)."""
        from app.ops import review as review_mod

        for i in range(3):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_IN_SCOPE_JSON)
        with _patch_resolve(provider):
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["ml research"], "in scope"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 1, "call still happens; it just reports in-scope"
        assert await _count_purpose_suggestions(review_env_0034) == 0


# ── T-R93-003 / 004: throttle ────────────────────────────────────────────────────


class TestThrottle:
    async def test_existing_pending_blocks_new(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-003: a pending purpose-suggestion blocks a new one — zero provider cost."""
        from app.ops import review as review_mod

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        # Pre-existing pending purpose-suggestion.
        async with review_env_0034["session_factory"]() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO review_items "
                    "(id, vault_id, item_type, status, proposed_title, rationale, created_at) "
                    "VALUES (:id, 'test-vault', 'purpose-suggestion', 'pending', 'Old', 'r', "
                    "datetime('now'))"
                ),
                {"id": str(uuid.uuid4())},
            )
            await sess.commit()

        provider = _make_chat_provider(_DRIFT_JSON)
        with _patch_resolve(provider):
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["home automation"], "s"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 0, "throttle must short-circuit before any provider call"
        assert await _count_purpose_suggestions(review_env_0034, status="pending") == 1

    async def test_below_min_sources_skips(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-004: fewer than N (3) sources since last check → skip (zero cost)."""
        from app.ops import review as review_mod

        # Only 2 source pages → below default N=3.
        await _insert_source_page(review_env_0034, title="Src A")
        await _insert_source_page(review_env_0034, title="Src B")

        provider = _make_chat_provider(_DRIFT_JSON)
        with _patch_resolve(provider):
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["home automation"], "s"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 0
        assert await _count_purpose_suggestions(review_env_0034) == 0


# ── T-R93-005: approve applies to purpose.md + bumps data_version ─────────────────


class TestApproveAppliesToPurpose:
    async def test_approve_appends_and_bumps(self, review_env_0034: dict[str, Any]) -> None:
        """
        T-R93-005: approving a purpose-suggestion appends its section to vault/purpose.md,
        bumps data_version, and marks the item created (NO wiki page generated).
        """
        from app.config import settings
        from app.ops import review as review_mod

        # Seed a purpose.md so the append has an existing base.
        purpose_path = settings.vault_root / "purpose.md"
        purpose_path.parent.mkdir(parents=True, exist_ok=True)
        purpose_path.write_text("# Purpose\n\nML research vault.\n", encoding="utf-8")

        # Insert a pending purpose-suggestion carrying the marker-delimited addition.
        item_id = str(uuid.uuid4())
        rationale = (
            "New recurring theme."
            + review_mod._PURPOSE_ADDITION_MARKER
            + "## Home Automation\n\nAlso tracks smart-home notes."
        )
        async with review_env_0034["session_factory"]() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO review_items "
                    "(id, vault_id, item_type, status, proposed_title, rationale, created_at) "
                    "VALUES (:id, 'test-vault', 'purpose-suggestion', 'pending', 'Home Automation', "
                    ":rationale, datetime('now'))"
                ),
                {"id": item_id, "rationale": rationale},
            )
            await sess.commit()

        # data_version before.
        async with review_env_0034["session_factory"]() as sess:
            dv_before = int(
                (
                    await sess.execute(
                        sa_text("SELECT data_version FROM vault_state WHERE vault_id='test-vault'")
                    )
                ).scalar_one()
            )

        # Approve routes to apply_purpose_suggestion — NO provider, NO wiki page.
        with patch("app.main._graph_cache", None):
            result = await review_mod.create_page_from_review(uuid.UUID(item_id))

        assert result.status == "created"
        assert result.resolution == "created"

        # purpose.md now contains the appended section.
        new_text = purpose_path.read_text(encoding="utf-8")
        assert "ML research vault." in new_text, "existing content preserved"
        assert "## Home Automation" in new_text, "suggested section appended"

        # data_version bumped exactly once.
        async with review_env_0034["session_factory"]() as sess:
            dv_after = int(
                (
                    await sess.execute(
                        sa_text("SELECT data_version FROM vault_state WHERE vault_id='test-vault'")
                    )
                ).scalar_one()
            )
        assert dv_after == dv_before + 1


# ── T-R93-006: provider failure → no item, ingest completes ──────────────────────


class TestProviderFailure:
    async def test_failure_degrades_no_item(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-006: a failing provider yields no item and never raises (ingest completes)."""
        from app.ops import review as review_mod

        for i in range(3):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider("", fail=True)
        with _patch_resolve(provider):
            # Must NOT raise.
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["x"], "s"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert await _count_purpose_suggestions(review_env_0034) == 0

    async def test_no_provider_returns_none(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-006b: no provider resolves (I6) → None, no item, no raise."""
        from app.ops import review as review_mod

        for i in range(3):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        with patch(
            "app.ops.review.resolve_operation_provider",
            new=AsyncMock(return_value=None),
        ):
            item = await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["x"], "s"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )
        assert item is None


# ── T-R93-007 / 008: bounded call + accepted type ────────────────────────────────


class TestBoundsAndType:
    async def test_exactly_one_call_no_retry(self, review_env_0034: dict[str, Any]) -> None:
        """T-R93-007: exactly one chat() call, never retried."""
        from app.ops import review as review_mod

        for i in range(3):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_DRIFT_JSON)
        with _patch_resolve(provider):
            await review_mod.generate_purpose_suggestion(
                vault_id="test-vault",
                analysis=_analysis(["home automation"], "s"),
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )
        assert provider._chat_calls[0] == 1

    def test_purpose_suggestion_is_valid_item_type(self) -> None:
        """T-R93-008: purpose-suggestion is an accepted item_type (Text column, no migration)."""
        from app.ops import review as review_mod

        assert "purpose-suggestion" in review_mod._VALID_ITEM_TYPES
