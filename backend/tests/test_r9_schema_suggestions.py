"""
R9-4 (v0.9) — schema.md co-evolution suggestions (K6, beyond llm_wiki).

Covers app/ops/review.py::generate_schema_suggestion + apply_schema_suggestion and the approve
routing in create_page_from_review for the new `schema-suggestion` ReviewItem type. Mirrors the
9-test structure of test_r9_purpose_suggestions.py, plus a disabled-by-default test (R9-4's one
deliberate divergence from R9-3: schema_suggestion_enabled defaults to False).

Tests:
  T-R94-000  DEFAULT OFF: with schema_suggestion_enabled=False (the default) → no item, no call
  T-R94-001  new pattern detected → ONE schema-suggestion ReviewItem emitted (mock provider)
  T-R94-002  no-change verdict → NO ReviewItem emitted (no queue spam, single call)
  T-R94-003  throttle: an existing PENDING schema-suggestion blocks a new one (zero cost)
  T-R94-004  throttle: fewer than N sources since last check → skip (zero cost)
  T-R94-005  approve applies the suggestion to schema.md + bumps data_version + marks created
  T-R94-006  provider failure → NO item, and the call never raises (ingest still completes)
  T-R94-007  bounded: exactly ONE chat() call, no retry
  T-R94-008  `schema-suggestion` is an accepted item_type (no migration — Text column)

The provider is always mocked — no real Ollama/API call. Reuses the ADR-0034 SQLite fixtures
(review_env_0034) which build the proposal-model schema and set settings.vault_path=tmp_path.
Every enabled test sets settings.schema_suggestion_enabled=True (default is False, R9-4 delta).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text as sa_text

# Reuse the ADR-0034 fixtures + DB helpers (same SQLite proposal-model schema + tmp vault).
from tests.test_review_adr0034 import (  # noqa: F401  (fixtures imported for pytest discovery)
    review_client_0034,
    review_env_0034,
)

# A model response reporting a new codifiable convention (valid change verdict).
_CHANGE_JSON = (
    '{"needs_change": true, "convention": "Homelab tag family", '
    '"why": "Most new pages carry a homelab/* tag family that schema.md does not codify.", '
    '"addition": "## Tags\\n\\nUse the `homelab/<service>` tag family for homelab pages."}'
)
_NO_CHANGE_JSON = '{"needs_change": false}'


# ── Provider mock ────────────────────────────────────────────────────────────────


def _make_chat_provider(response: str, *, fail: bool = False) -> Any:
    """Mock InferenceProvider whose chat() yields *response* once (or raises if fail)."""
    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            if fail:
                raise RuntimeError("provider boom")
            yield response

        return _gen()

    provider.chat = mock_chat
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
        "app.ops.review._resolve_review_provider",
        new=AsyncMock(return_value=(provider, _fake_config_row())),
    )


def _enable_schema() -> Any:
    """Enable the R9-4 check (default is OFF — the one R9-3 divergence)."""
    return patch("app.ops.review.settings.schema_suggestion_enabled", True)


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


def _written_page(title: str = "New Note", *, tags: list[str] | None = None) -> Any:
    page = MagicMock()
    page.id = uuid.uuid4()
    page.title = title
    page.page_type = "concept"
    page.tags = tags if tags is not None else ["homelab/proxmox"]
    page.sources = ["raw/sources/note.md"]
    return page


async def _count_schema_suggestions(env: dict[str, Any], *, status: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM review_items WHERE item_type='schema-suggestion'"
    if status is not None:
        q += f" AND status='{status}'"
    async with env["session_factory"]() as sess:
        return int((await sess.execute(sa_text(q))).scalar_one())


# ── T-R94-000: disabled by default ────────────────────────────────────────────────


class TestDisabledByDefault:
    async def test_default_off_no_call_no_item(self, review_env_0034: dict[str, Any]) -> None:
        """
        T-R94-000: schema_suggestion_enabled defaults to False (R9-4's deliberate, conservative
        divergence from R9-3). With the default in place, NO provider call is made and NO item is
        emitted even when everything else (sources, drift verdict) would otherwise trigger one.
        """
        from app.config import settings
        from app.ops import review as review_mod

        # The default must be False (documented: schema changes affect future ingest behavior).
        assert settings.schema_suggestion_enabled is False

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_CHANGE_JSON)
        # NOTE: no _enable_schema() here — rely on the shipped default.
        with _patch_resolve(provider):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 0, "disabled → must short-circuit before any provider call"
        assert await _count_schema_suggestions(review_env_0034) == 0


# ── T-R94-001: new pattern detected → item emitted ────────────────────────────────


class TestPatternDetection:
    async def test_pattern_emits_one_item(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-001: a change verdict emits exactly ONE schema-suggestion (mock provider)."""
        from app.ops import review as review_mod

        # Seed enough source pages to pass throttle 2 (default N=5).
        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_CHANGE_JSON)
        with _enable_schema(), _patch_resolve(provider):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is not None
        assert item.item_type == "schema-suggestion"
        assert item.proposed_title == "Homelab tag family"
        assert provider._chat_calls[0] == 1
        assert await _count_schema_suggestions(review_env_0034) == 1
        # The exact addition markdown is retrievable for the apply step (schema marker).
        addition = review_mod._extract_schema_addition(item.rationale)
        assert addition is not None and "homelab/<service>" in addition


# ── T-R94-002: no-change → no item ────────────────────────────────────────────────


class TestNoChange:
    async def test_no_change_emits_nothing(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-002: a no-change verdict creates no ReviewItem (no spam; call still happens)."""
        from app.ops import review as review_mod

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_NO_CHANGE_JSON)
        with _enable_schema(), _patch_resolve(provider):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 1, "call still happens; it just reports no change"
        assert await _count_schema_suggestions(review_env_0034) == 0


# ── T-R94-003 / 004: throttle ────────────────────────────────────────────────────


class TestThrottle:
    async def test_existing_pending_blocks_new(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-003: a pending schema-suggestion blocks a new one — zero provider cost."""
        from app.ops import review as review_mod

        for i in range(6):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        # Pre-existing pending schema-suggestion.
        async with review_env_0034["session_factory"]() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO review_items "
                    "(id, vault_id, item_type, status, proposed_title, rationale, created_at) "
                    "VALUES (:id, 'test-vault', 'schema-suggestion', 'pending', 'Old', 'r', "
                    "datetime('now'))"
                ),
                {"id": str(uuid.uuid4())},
            )
            await sess.commit()

        provider = _make_chat_provider(_CHANGE_JSON)
        with _enable_schema(), _patch_resolve(provider):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 0, "throttle must short-circuit before any provider call"
        assert await _count_schema_suggestions(review_env_0034, status="pending") == 1

    async def test_below_min_sources_skips(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-004: fewer than N (5) sources since last check → skip (zero cost)."""
        from app.ops import review as review_mod

        # Only 4 source pages → below default N=5.
        for i in range(4):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_CHANGE_JSON)
        with _enable_schema(), _patch_resolve(provider):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert provider._chat_calls[0] == 0
        assert await _count_schema_suggestions(review_env_0034) == 0


# ── T-R94-005: approve applies to schema.md + bumps data_version ──────────────────


class TestApproveAppliesToSchema:
    async def test_approve_appends_and_bumps(self, review_env_0034: dict[str, Any]) -> None:
        """
        T-R94-005: approving a schema-suggestion appends its rule block to vault/schema.md,
        bumps data_version, and marks the item created (NO wiki page generated).
        """
        from app.config import settings
        from app.ops import review as review_mod

        # Seed a schema.md so the append has an existing base.
        schema_path = settings.vault_root / "schema.md"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text("# Schema\n\nRequired frontmatter: type, title.\n", encoding="utf-8")

        # Insert a pending schema-suggestion carrying the marker-delimited addition.
        item_id = str(uuid.uuid4())
        rationale = (
            "Recurring homelab tag family."
            + review_mod._SCHEMA_ADDITION_MARKER
            + "## Tags\n\nUse the `homelab/<service>` tag family for homelab pages."
        )
        async with review_env_0034["session_factory"]() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO review_items "
                    "(id, vault_id, item_type, status, proposed_title, rationale, created_at) "
                    "VALUES (:id, 'test-vault', 'schema-suggestion', 'pending', "
                    "'Homelab tag family', :rationale, datetime('now'))"
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

        # Approve routes to apply_schema_suggestion — NO provider, NO wiki page.
        with patch("app.main._graph_cache", None):
            result = await review_mod.create_page_from_review(uuid.UUID(item_id))

        assert result.status == "created"
        assert result.resolution == "created"

        # schema.md now contains the appended rule block, preserving existing content.
        new_text = schema_path.read_text(encoding="utf-8")
        assert "Required frontmatter: type, title." in new_text, "existing content preserved"
        assert "## Tags" in new_text, "suggested rule appended"
        assert "homelab/<service>" in new_text

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


# ── T-R94-006: provider failure → no item, ingest completes ──────────────────────


class TestProviderFailure:
    async def test_failure_degrades_no_item(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-006: a failing provider yields no item and never raises (ingest completes)."""
        from app.ops import review as review_mod

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider("", fail=True)
        with _enable_schema(), _patch_resolve(provider):
            # Must NOT raise.
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )

        assert item is None
        assert await _count_schema_suggestions(review_env_0034) == 0

    async def test_no_provider_returns_none(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-006b: no provider resolves (I6) → None, no item, no raise."""
        from app.ops import review as review_mod

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        with _enable_schema(), patch(
            "app.ops.review._resolve_review_provider",
            new=AsyncMock(return_value=None),
        ):
            item = await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )
        assert item is None


# ── T-R94-007 / 008: bounded call + accepted type ────────────────────────────────


class TestBoundsAndType:
    async def test_exactly_one_call_no_retry(self, review_env_0034: dict[str, Any]) -> None:
        """T-R94-007: exactly one chat() call, never retried."""
        from app.ops import review as review_mod

        for i in range(5):
            await _insert_source_page(review_env_0034, title=f"Src {i}")

        provider = _make_chat_provider(_CHANGE_JSON)
        with _enable_schema(), _patch_resolve(provider):
            await review_mod.generate_schema_suggestion(
                vault_id="test-vault",
                written_pages=[_written_page()],
                origin_source="raw/sources/note.md",
            )
        assert provider._chat_calls[0] == 1

    def test_schema_suggestion_is_valid_item_type(self) -> None:
        """T-R94-008: schema-suggestion is an accepted item_type (Text column, no migration)."""
        from app.ops import review as review_mod

        assert "schema-suggestion" in review_mod._VALID_ITEM_TYPES
