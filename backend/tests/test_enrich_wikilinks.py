"""
ADR-0036 wikilink-enrichment post-pass tests.

Pure-logic tests (no DB/provider):
  - frontmatter-safe split/rejoin preserves the YAML block byte-for-byte (I5)
  - first-mention apply: single-mention, no double-wrap, alias form when surface != title
  - substitution validation drops hallucinated targets / missing mentions / self-links

Integration tests (temp vault + fake provider; DB primitives isolated — Postgres-portable):
  - enrich_wikilinks applies validated subs, re-indexes the edited page (K5 links derived),
    bumps data_version exactly once, leaves frontmatter byte-identical, makes ONE provider call
  - graceful skip with no provider configured (I6 — never hardcode, never crash)
  - anti-spam gate skips below min_chars at zero cost; the master flag disables the pass
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ops import enrich_wikilinks as enrich_mod
from app.ops.enrich_wikilinks import (
    _apply_first_mention,
    _parse_substitutions,
    _rejoin,
    _split_frontmatter,
    enrich_wikilinks,
)

# ── Pure: frontmatter split/rejoin ───────────────────────────────────────────────

_FM_FILE = (
    "---\ntype: concept\ntitle: Transformer\nsources:\n- raw/x.md\nlang: en\n---\n\n"
    "Body mentions attention mechanism here.\n"
)


def test_split_preserves_frontmatter_block() -> None:
    fm, body = _split_frontmatter(_FM_FILE)
    assert fm.startswith("---\n") and fm.rstrip("\n").endswith("---")
    assert "Body mentions attention mechanism" in body
    # Rejoin with the UNCHANGED body reproduces the file byte-for-byte (I5).
    assert _rejoin(fm, body) == _FM_FILE


def test_split_no_frontmatter() -> None:
    text = "Just a body, no frontmatter.\n"
    fm, body = _split_frontmatter(text)
    assert fm == ""
    assert body == text


def test_rejoin_with_edited_body_keeps_frontmatter() -> None:
    fm, body = _split_frontmatter(_FM_FILE)
    new_body = body.replace("attention mechanism", "[[Attention mechanism|attention mechanism]]")
    out = _rejoin(fm, new_body)
    # Frontmatter slice identical; only the body changed.
    assert out.startswith(fm)
    assert "[[Attention mechanism|attention mechanism]]" in out
    assert out.split("---\n\n", 1)[0] == _FM_FILE.split("---\n\n", 1)[0]


# ── Pure: first-mention apply ────────────────────────────────────────────────────


def test_apply_first_mention_alias_form() -> None:
    body = "We discuss attention mechanism in detail."
    out = _apply_first_mention(body, "attention mechanism", "Attention mechanism")
    assert out == "We discuss [[Attention mechanism|attention mechanism]] in detail."


def test_apply_first_mention_exact_title_no_alias() -> None:
    body = "See Attention mechanism for more."
    out = _apply_first_mention(body, "Attention mechanism", "Attention mechanism")
    assert out == "See [[Attention mechanism]] for more."


def test_apply_first_mention_only_first_occurrence() -> None:
    body = "attention mechanism and attention mechanism again"
    out = _apply_first_mention(body, "attention mechanism", "Attention mechanism")
    assert out is not None
    # Only the FIRST occurrence is wrapped (single-mention).
    assert out.count("[[Attention mechanism|attention mechanism]]") == 1
    assert out.endswith("attention mechanism again")


def test_apply_first_mention_skips_inside_existing_link() -> None:
    body = "Already linked [[Attention mechanism|attention mechanism]] here."
    # The only occurrence is inside an existing [[...]] → no eligible spot → None (no double-wrap).
    out = _apply_first_mention(body, "attention mechanism", "Attention mechanism")
    assert out is None


def test_apply_first_mention_absent_returns_none() -> None:
    assert _apply_first_mention("nothing here", "absent phrase", "Target") is None


# ── Pure: substitution validation ────────────────────────────────────────────────


def _fake_page(title: str, body_id: uuid.UUID | None = None) -> Any:
    p = type("P", (), {})()
    p.id = body_id or uuid.uuid4()
    p.title = title
    p.file_path = f"wiki/concepts/{title.lower().replace(' ', '-')}.md"
    p.page_type = "concept"
    return p


def test_parse_substitutions_validates() -> None:
    page = _fake_page("Transformer")
    title_to_page = {"Transformer": page}
    candidates = {"Transformer", "Attention mechanism"}
    raw = (
        '{"substitutions": ['
        f'{{"page_id": "{page.id}", "mention": "attention mechanism", '
        '"target_title": "Attention mechanism"},'
        # hallucinated target (not a candidate) → dropped
        f'{{"page_id": "{page.id}", "mention": "x", "target_title": "Nonexistent"}},'
        # self-link → dropped
        f'{{"page_id": "{page.id}", "mention": "y", "target_title": "Transformer"}},'
        # unknown page_id → dropped
        '{"page_id": "00000000-0000-0000-0000-000000000000", '
        '"mention": "z", "target_title": "Attention mechanism"}'
        "]}"
    )
    subs = _parse_substitutions(raw, title_to_page, candidates)
    assert len(subs) == 1
    assert subs[0].mention == "attention mechanism"
    assert subs[0].target_title == "Attention mechanism"
    assert subs[0].page_id == page.id


def test_parse_substitutions_garbage_returns_empty() -> None:
    page = _fake_page("T")
    assert _parse_substitutions("not json at all", {"T": page}, {"T"}) == []


# ── Integration: SQLite + ORM + fake provider ───────────────────────────────────


def _make_chat_provider(response: str, *, hang: bool = False) -> Any:
    """Mock InferenceProvider whose chat() yields *response* once (review-test shape)."""
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            if hang:
                import asyncio

                await asyncio.sleep(60)
            yield response

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


@pytest.fixture()
async def enrich_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
    """
    Temp-vault env for the enrichment integration tests.

    The DB-touching primitives are isolated by design so the test stays Postgres-portable and
    free of the JSONB/UUID-on-SQLite landmine (project memory): ``_load_candidate_titles`` is
    stubbed to return titles directly, and ``reindex_wiki_page_body`` is replaced with a faithful
    stub that performs the real-world effects the production helper performs — write the new file
    bytes, derive the K5 wikilinks, and (when ``bump=True``) bump a version counter — without
    binding ORM rows to SQLite. This lets the tests assert the ENRICHMENT orchestration (apply,
    file write, link derivation, single bump, one provider call) end-to-end.
    """
    from app import config as cfg

    vault_root = tmp_path / "vault"
    (vault_root / "wiki" / "concepts").mkdir(parents=True)
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    # Enrich defaults OFF since v1.7.0 (ADR-0076); these tests exercise the pass itself, so the
    # fixture opts it back in. The dedicated disabled-flag test overrides this to False.
    monkeypatch.setattr(cfg.settings, "wikilink_enrich_enabled", True)
    # Low default so the apply/no-provider tests pass the gate with short bodies; the dedicated
    # anti-spam test overrides this to a large value.
    monkeypatch.setattr(cfg.settings, "wikilink_enrich_min_chars", 10)

    state: dict[str, Any] = {
        "vault_root": vault_root,
        "data_version": 0,
        "candidate_titles": set(),
        "reindex_calls": [],
        "links": {},  # page_id → list[ParsedLink]
    }

    async def fake_load_candidates(vault_id: str, max_candidates: int) -> set[str]:
        return set(list(state["candidate_titles"])[:max_candidates])

    async def fake_reindex(
        *, page: Any, new_file_text: str, body_for_embedding: str, bump: bool = True
    ) -> None:
        from app.wiki.links import parse_wikilinks

        # Real effect 1: atomic-equivalent file write (the production helper writes new bytes).
        abs_path = (vault_root / page.file_path).resolve()
        abs_path.write_text(new_file_text, encoding="utf-8")
        # Real effect 2: K5 link derivation from the new body (feeds F4 ×3 signal).
        state["links"][page.id] = parse_wikilinks(body_for_embedding)
        # Real effect 3: single version bump per page when not batched.
        if bump:
            state["data_version"] += 1
        state["reindex_calls"].append({"page_id": page.id, "bump": bump})

    async def fake_bump() -> None:
        state["data_version"] += 1

    monkeypatch.setattr(enrich_mod, "_load_candidate_titles", fake_load_candidates)
    monkeypatch.setattr("app.ingest.orchestrator.reindex_wiki_page_body", fake_reindex)
    monkeypatch.setattr("app.ingest.orchestrator.bump_version", fake_bump)

    return state


def _seed_page_file(env: dict[str, Any], *, title: str, body: str, subdir: str = "concepts") -> Any:
    """Write a wiki .md file on disk + return a duck-typed Page row (no DB)."""
    page = type("P", (), {})()
    page.id = uuid.uuid4()
    page.vault_id = "test-vault"
    slug = title.lower().replace(" ", "-")
    page.file_path = f"wiki/{subdir}/{slug}.md"
    page.title = title
    page.page_type = "concept"
    page.sources = ["raw/x.md"]
    page.source_mtime_ns = 0
    abs_path = env["vault_root"] / "wiki" / subdir / f"{slug}.md"
    abs_path.write_text(
        f"---\ntype: concept\ntitle: {title}\nsources:\n- raw/x.md\nlang: en\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return page


async def test_enrich_applies_links_and_reindexes(
    enrich_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.wiki.links import ParsedLink

    # Existing target page title is a candidate.
    enrich_env["candidate_titles"] = {"Attention mechanism"}
    transformer = _seed_page_file(
        enrich_env,
        title="Transformer",
        body="The transformer relies on the attention mechanism heavily.",
    )

    provider = _make_chat_provider(
        f'{{"substitutions": [{{"page_id": "{transformer.id}", '
        '"mention": "attention mechanism", "target_title": "Attention mechanism"}]}'
    )
    monkeypatch.setattr(enrich_mod, "resolve_operation_provider", _async_return((provider, _cfg_row())))

    result = await enrich_wikilinks([transformer], "test-vault")

    assert result.pages_enriched == 1
    assert result.links_added == 1
    assert provider._chat_calls[0] == 1, "exactly ONE provider call (I7)"

    # File on disk got the wikilink; frontmatter preserved byte-for-byte (I5).
    text = (enrich_env["vault_root"] / "wiki" / "concepts" / "transformer.md").read_text(
        encoding="utf-8"
    )
    assert "[[Attention mechanism|attention mechanism]]" in text
    assert text.startswith("---\ntype: concept\ntitle: Transformer\nsources:\n- raw/x.md\n")

    # K5 link derived → becomes the F4 direct-link ×3 edge.
    derived = enrich_env["links"][transformer.id]
    assert ParsedLink(target="Attention mechanism", alias="attention mechanism") in derived

    # Re-index called once with bump=False (batched); data_version bumped exactly once for the pass.
    assert [c["bump"] for c in enrich_env["reindex_calls"]] == [False]
    assert enrich_env["data_version"] == 1


async def test_enrich_drops_hallucinated_substitution(
    enrich_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    enrich_env["candidate_titles"] = {"Attention mechanism"}
    transformer = _seed_page_file(
        enrich_env, title="Transformer", body="The transformer is a neural network."
    )
    # Target not a real candidate, mention not in body → dropped → no edit.
    provider = _make_chat_provider(
        f'{{"substitutions": [{{"page_id": "{transformer.id}", '
        '"mention": "quantum entanglement", "target_title": "Nonexistent Page"}]}'
    )
    monkeypatch.setattr(enrich_mod, "resolve_operation_provider", _async_return((provider, _cfg_row())))

    result = await enrich_wikilinks([transformer], "test-vault")

    assert result.pages_enriched == 0
    assert result.links_added == 0
    text = (enrich_env["vault_root"] / "wiki" / "concepts" / "transformer.md").read_text(
        encoding="utf-8"
    )
    assert "[[" not in text
    assert enrich_env["reindex_calls"] == []  # nothing applied → no re-index
    assert enrich_env["data_version"] == 0  # no bump when nothing applied


async def test_enrich_skips_when_no_provider(
    enrich_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    enrich_env["candidate_titles"] = {"Attention mechanism"}
    transformer = _seed_page_file(
        enrich_env, title="Transformer", body="The transformer relies on attention mechanism."
    )
    monkeypatch.setattr(enrich_mod, "resolve_operation_provider", _async_return(None))

    result = await enrich_wikilinks([transformer], "test-vault")
    assert result.skipped_reason == "no_provider"
    assert result.pages_enriched == 0
    # Never crashes the ingest; degrades gracefully (I6 — no silent default).


async def test_enrich_antispam_gate_below_min_chars(
    enrich_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "wikilink_enrich_min_chars", 10_000)
    enrich_env["candidate_titles"] = {"Attention mechanism"}
    transformer = _seed_page_file(enrich_env, title="Transformer", body="tiny body")
    provider = _make_chat_provider("{}")
    monkeypatch.setattr(enrich_mod, "resolve_operation_provider", _async_return((provider, _cfg_row())))

    result = await enrich_wikilinks([transformer], "test-vault")
    assert result.skipped_reason == "below_min_chars"
    assert provider._chat_calls[0] == 0, "anti-spam gate skips the call at zero cost"


async def test_enrich_disabled_flag(
    enrich_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "wikilink_enrich_enabled", False)
    transformer = _seed_page_file(enrich_env, title="Transformer", body="x" * 500)
    result = await enrich_wikilinks([transformer], "test-vault")
    assert result.skipped_reason == "disabled"


# ── helpers ──────────────────────────────────────────────────────────────────────


def _cfg_row() -> Any:
    from unittest.mock import MagicMock

    row = MagicMock()
    row.token_budget = 4_000
    row.model_id = "test-model"
    row.provider_type = "local"
    return row


def _async_return(value: Any):  # type: ignore[no-untyped-def]
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value

    return _f
