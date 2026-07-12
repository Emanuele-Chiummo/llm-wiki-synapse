"""
Broken-wikilink fuzzy repair suggestion — llm_wiki suggestBrokenTarget parity.

Before this, resolve_suggested_target reused the SAME exact→case→slug matcher that had
already marked the link dangling, so a typo'd [[Transformerz]] produced no suggestion and
the apply path fell back to spawning a stub page. llm_wiki instead offers a typo-tolerant
re-point suggestion (stringSimilarity, Levenshtein over the basename, threshold 0.74).
These tests lock the ported scoring + the maps-driven candidate selection.

Pure functions — no DB, no provider.
"""

from __future__ import annotations

import uuid

from app.ingest.orchestrator import _slugify
from app.wiki.links import (
    _BROKEN_LINK_SUGGESTION_MIN_SCORE,
    _CONTAINS_TARGET_SCORE,
    _SAME_BASENAME_SCORE,
    _fuzzy_suggest_target,
    _levenshtein,
    _normalize_link_target,
    _ResolverMaps,
    _string_similarity,
)

# ── normalization ─────────────────────────────────────────────────────────────


def test_normalize_strips_wiki_prefix_md_suffix_and_lowercases() -> None:
    assert _normalize_link_target("wiki/Entities/Transformer.md") == "entities/transformer"
    assert _normalize_link_target("  Transformer  ") == "transformer"


def test_levenshtein_basic() -> None:
    assert _levenshtein("kitten", "kitten") == 0
    assert _levenshtein("kitten", "sitting") == 3
    assert _levenshtein("", "abc") == 3


# ── stringSimilarity parity ───────────────────────────────────────────────────


def test_similarity_exact_after_normalize_is_one() -> None:
    assert _string_similarity("Transformer", "transformer") == 1.0


def test_similarity_substring_containment_score() -> None:
    # "transformer" ⊂ "transformers" → CONTAINS shortcut (plural typo re-point).
    assert _string_similarity("transformers", "transformer") == _CONTAINS_TARGET_SCORE


def test_similarity_same_basename_across_folders() -> None:
    assert (
        _string_similarity("entities/transformer", "concepts/transformer") == _SAME_BASENAME_SCORE
    )


def test_similarity_short_tokens_are_rejected() -> None:
    # Both basenames < 5 chars → too noisy for Levenshtein → 0 (no false suggestion).
    assert _string_similarity("rag", "tag") == 0.0


def test_similarity_typo_clears_threshold() -> None:
    # single-char typo in a long-enough word → high similarity, above 0.74.
    assert _string_similarity("Transfarmer", "Transformer") >= _BROKEN_LINK_SUGGESTION_MIN_SCORE


def test_similarity_unrelated_below_threshold() -> None:
    assert _string_similarity("Attention Is All You Need", "Backpropagation") < (
        _BROKEN_LINK_SUGGESTION_MIN_SCORE
    )


# ── _fuzzy_suggest_target (maps-driven selection) ─────────────────────────────


def _maps(*titles: str) -> _ResolverMaps:
    by_title: dict[str, uuid.UUID] = {}
    by_lower: dict[str, uuid.UUID] = {}
    by_slug: dict[str, uuid.UUID] = {}
    for t in titles:
        pid = uuid.uuid4()
        by_title.setdefault(t, pid)
        by_lower.setdefault(t.lower(), pid)
        by_slug.setdefault(_slugify(t), pid)
    return _ResolverMaps(by_title=by_title, by_lower=by_lower, by_slug=by_slug)


def test_fuzzy_suggests_closest_title() -> None:
    maps = _maps("Transformer", "Backpropagation", "Attention Mechanism")
    out = _fuzzy_suggest_target("Transformerz", maps)
    assert out is not None
    _, title = out
    assert title == "Transformer"


def test_fuzzy_returns_none_when_nothing_close() -> None:
    maps = _maps("Backpropagation", "Gradient Descent")
    assert _fuzzy_suggest_target("Quantum Chromodynamics", maps) is None


def test_fuzzy_matches_via_slug_form() -> None:
    # Model emitted the slug form of a real page; scored against _slugify(title).
    maps = _maps("Attention Mechanism")
    out = _fuzzy_suggest_target("attention-mechanism", maps)
    assert out is not None
    assert out[1] == "Attention Mechanism"


def test_fuzzy_empty_maps_is_none() -> None:
    assert _fuzzy_suggest_target("anything", _maps()) is None
