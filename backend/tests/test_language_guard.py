"""
Feature 3 (ADR-0063 §5) — wrong-language page drop.

Two layers:
  • unit tests for the deterministic script-family detector (``app.ingest.language``);
  • tests for ``_drop_wrong_language_pages`` — off-language concept dropped, on-language kept,
    source/entity pages exempt, disabled config keeps everything, and detection is degrade-safe.
"""

from __future__ import annotations

import pytest
from app.ingest.language import (
    body_matches_target_language,
    dominant_script_family,
    target_family,
)
from app.ingest.schemas import (
    Analysis,
    PageType,
    SuggestedPage,
    WikiFrontmatter,
    WikiPage,
)

ENGLISH = "This is an authoritative concept page written entirely in English prose. " * 3
ITALIAN = "Questa e una pagina di concetto scritta interamente in lingua italiana. " * 3
CHINESE = "这是一个完全用中文书写的权威概念页面，内容非常详细并且完整。" * 3
RUSSIAN = "Это авторитетная страница концепции, полностью написанная на русском языке." * 3


# ── Detector ─────────────────────────────────────────────────────────────────────


def test_dominant_script_family() -> None:
    assert dominant_script_family(ENGLISH) == "latin"
    assert dominant_script_family(ITALIAN) == "latin"
    assert dominant_script_family(CHINESE) == "cjk"
    assert dominant_script_family(RUSSIAN) == "cyrillic"


def test_target_family_mapping() -> None:
    assert target_family("en") == "latin"
    assert target_family("it") == "latin"
    assert target_family("zh") == "cjk"
    assert target_family("ru") == "cyrillic"
    assert target_family(None) == "latin"
    assert target_family("xx") == "latin"  # unknown → safe default


def test_body_matches_target_language() -> None:
    # Cross-script mismatch → does NOT match (would be dropped).
    assert body_matches_target_language(CHINESE, "en") is False
    assert body_matches_target_language(ENGLISH, "zh") is False
    # Same family / intra-Latin → matches (never dropped).
    assert body_matches_target_language(ENGLISH, "en") is True
    assert body_matches_target_language(ITALIAN, "en") is True  # intra-Latin, no false drop
    assert body_matches_target_language(ENGLISH, "it") is True
    assert body_matches_target_language(CHINESE, "zh") is True
    # Too short to judge → matches (keep).
    assert body_matches_target_language("ok", "zh") is True


# ── _drop_wrong_language_pages ─────────────────────────────────────────────────────


def _page(title: str, body: str, ptype: PageType) -> WikiPage:
    return WikiPage(
        title=title,
        type=ptype,
        content=body,
        frontmatter=WikiFrontmatter(
            type=ptype, title=title, sources=["raw/sources/x.md"], lang="en"
        ),
    )


def _analysis(lang: str) -> Analysis:
    return Analysis(
        topics=["t"],
        entities=[],
        language=lang,
        suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
    )


def _enable_guard(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ingest_language_guard_enabled", on)


def test_drops_off_language_concept_keeps_on_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.pipeline import _drop_wrong_language_pages

    _enable_guard(monkeypatch, True)
    pages = [
        _page("Good", ENGLISH, PageType.CONCEPT),
        _page("Bad", CHINESE, PageType.CONCEPT),
    ]
    kept = _drop_wrong_language_pages(pages, _analysis("en"))
    titles = {p.title for p in kept}
    assert titles == {"Good"}  # the Chinese concept page was dropped


def test_source_and_entity_pages_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.pipeline import _drop_wrong_language_pages

    _enable_guard(monkeypatch, True)
    pages = [
        _page("Src", CHINESE, PageType.SOURCE),  # exempt (F3 traceability)
        _page("Ent", CHINESE, PageType.ENTITY),  # exempt (cross-language proper nouns)
        _page("Con", CHINESE, PageType.CONCEPT),  # dropped
    ]
    kept = _drop_wrong_language_pages(pages, _analysis("en"))
    titles = {p.title for p in kept}
    assert titles == {"Src", "Ent"}


def test_guard_disabled_keeps_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.pipeline import _drop_wrong_language_pages

    _enable_guard(monkeypatch, False)
    pages = [_page("Bad", CHINESE, PageType.CONCEPT)]
    kept = _drop_wrong_language_pages(pages, _analysis("en"))
    assert len(kept) == 1


def test_no_analysis_or_empty_lang_keeps_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.pipeline import _drop_wrong_language_pages

    _enable_guard(monkeypatch, True)
    pages = [_page("Bad", CHINESE, PageType.CONCEPT)]
    assert len(_drop_wrong_language_pages(pages, None)) == 1
    # A whitespace-only language resolves to empty after strip → guard is a no-op.
    assert len(_drop_wrong_language_pages(pages, _analysis("  "))) == 1


def test_matching_language_all_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.pipeline import _drop_wrong_language_pages

    _enable_guard(monkeypatch, True)
    pages = [
        _page("A", CHINESE, PageType.CONCEPT),
        _page("B", CHINESE, PageType.SYNTHESIS),
    ]
    kept = _drop_wrong_language_pages(pages, _analysis("zh"))
    assert len(kept) == 2  # target zh matches Chinese bodies
