"""
Lint semantic parse — missing-page false-positive guard (llm_wiki broken-link parity).

llm_wiki never tells the user to "Create a wiki page titled X" for a page that already exists;
a wikilink that references an existing page with a different slug/casing is a broken LINK
(re-point the link), surfaced by the deterministic broken-wikilink pass with a suggested target.
Synapse's semantic LLM sometimes mislabels that as `missing-page` with a "Create …" action; the
guard in `_parse_findings` drops those when the target already exists. These tests lock it in.

Pure function — no DB, no provider.
"""

from __future__ import annotations

from app.ops.lint import _norm_title_for_match, _parse_findings


def _mp(target: str, desc: str = "referenced with a different slug") -> str:
    return (
        '{"findings":[{"category":"missing-page","severity":"error",'
        f'"description":"{desc}","target_title":"{target}"}}]}}'
    )


# ── normalization ─────────────────────────────────────────────────────────────


def test_norm_title_and_slug_collapse_equal() -> None:
    # Proper title and its wikilink slug normalise to the same existence key.
    assert _norm_title_for_match("AWS Cost Explorer") == _norm_title_for_match("aws-cost-explorer")
    assert _norm_title_for_match("Microsoft Entra ID") == _norm_title_for_match(
        "microsoft-entra-id"
    )
    assert _norm_title_for_match("AWS Cost Explorer") != _norm_title_for_match("AWS Cost Explorers")


# ── guard: drop "create" for an existing page ─────────────────────────────────


def test_missing_page_for_existing_title_is_dropped() -> None:
    out = _parse_findings(_mp("AWS Cost Explorer"), existing_titles=["AWS Cost Explorer"])
    assert out == []  # the page exists → not a missing page, no "Create" action


def test_missing_page_matches_existing_by_slug() -> None:
    # Model emitted the slug form as target_title; still recognised as existing.
    out = _parse_findings(_mp("aws-cost-explorer"), existing_titles=["AWS Cost Explorer"])
    assert out == []


def test_genuinely_missing_page_is_kept_with_create_action() -> None:
    out = _parse_findings(_mp("Brand New Concept"), existing_titles=["AWS Cost Explorer"])
    assert len(out) == 1
    f = out[0]
    assert f.category == "missing-page"
    assert f.proposed_action is not None and "Create" in f.proposed_action


def test_guard_noop_when_no_existing_titles_passed() -> None:
    # Backward-compatible: without the existing-titles set, behaviour is unchanged.
    out = _parse_findings(_mp("AWS Cost Explorer"))
    assert len(out) == 1
    assert out[0].proposed_action is not None


def test_other_categories_unaffected_by_guard() -> None:
    raw = (
        '{"findings":[{"category":"contradiction","severity":"warning",'
        '"description":"conflicting claims","target_title":"AWS Cost Explorer"}]}'
    )
    out = _parse_findings(raw, existing_titles=["AWS Cost Explorer"])
    assert len(out) == 1
    assert out[0].category == "contradiction"
    assert out[0].proposed_action is None
