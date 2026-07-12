"""
Regression: a corrective re-ingest of a page owned ONLY by the re-ingested source must REPLACE
the body (nashsu/llm_wiki isOwnedOnlyBySource → replaceExistingBody), not LLM-merge stale facts
back in. Multi-source pages must still MERGE so no source's contribution is lost.

Covers the pure decision helper ``_is_owned_only_by_source`` used at the re-ingest merge site.
"""

from __future__ import annotations

from app.ingest.orchestrator import _is_owned_only_by_source


def test_owned_only_when_sole_prior_source_matches() -> None:
    assert _is_owned_only_by_source(["raw/sources/a.md"], "raw/sources/a.md") is True


def test_not_owned_when_another_source_contributed() -> None:
    # Shared page → must MERGE (return False), never replace.
    assert (
        _is_owned_only_by_source(["raw/sources/a.md", "raw/sources/b.md"], "raw/sources/a.md")
        is False
    )


def test_not_owned_when_prior_sources_unknown() -> None:
    # No recorded provenance → keep the safe merge behaviour.
    assert _is_owned_only_by_source(None, "raw/sources/a.md") is False
    assert _is_owned_only_by_source([], "raw/sources/a.md") is False


def test_false_on_empty_origin() -> None:
    assert _is_owned_only_by_source(["raw/sources/a.md"], "") is False


def test_ignores_blank_prior_entries() -> None:
    assert _is_owned_only_by_source(["", "raw/sources/a.md"], "raw/sources/a.md") is True
