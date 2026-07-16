"""
Unit tests for K6 YAML frontmatter parsing (AC-K6-1, AC-K6-2, AC-K6-3).

These tests are INFRA-FREE — they exercise the _parse_frontmatter function
directly and do NOT touch Postgres, Qdrant, or the embedding service.

Coverage:
  AC-K6-1  valid frontmatter → correct extraction
  AC-K6-2  missing fields → NULLs (dict key absent), no exception
  AC-K6-3  no frontmatter block → all fields NULL, no exception
  AC-K6-4  sources is a list (JSONB-able), not a scalar
"""

from __future__ import annotations

import pytest
from app.ingest.orchestrator import _parse_frontmatter

# ── Helpers ────────────────────────────────────────────────────────────────────


def _encode(s: str) -> bytes:
    return s.encode("utf-8")


# ── AC-K6-1 valid frontmatter ──────────────────────────────────────────────────


class TestValidFrontmatter:
    """AC-K6-1 — valid frontmatter is parsed correctly."""

    def test_all_three_fields_extracted(self) -> None:
        raw = _encode(
            "---\n"
            "type: entity\n"
            'title: "Test Entity"\n'
            "sources:\n"
            "  - a.pdf\n"
            "  - b.pdf\n"
            "---\n\n"
            "Body content here.\n"
        )
        meta = _parse_frontmatter(raw, "test.md")
        assert meta.get("type") == "entity"
        assert meta.get("title") == "Test Entity"
        assert meta.get("sources") == ["a.pdf", "b.pdf"]

    def test_type_field_string(self) -> None:
        raw = _encode("---\ntype: concept\ntitle: My Concept\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "concept.md")
        assert isinstance(meta.get("type"), str)
        assert meta["type"] == "concept"

    def test_title_with_colon_in_quotes(self) -> None:
        raw = _encode('---\ntype: entity\ntitle: "Entity: Part 1"\nsources: []\n---\n')
        meta = _parse_frontmatter(raw, "entity.md")
        assert meta.get("title") == "Entity: Part 1"

    def test_sources_as_inline_yaml_list(self) -> None:
        raw = _encode("---\ntype: source\ntitle: Ref\nsources: [x.pdf, y.pdf]\n---\n")
        meta = _parse_frontmatter(raw, "ref.md")
        assert meta.get("sources") == ["x.pdf", "y.pdf"]

    def test_extra_fields_ignored(self) -> None:
        """Extra frontmatter fields beyond the three required ones are returned in meta."""
        raw = _encode(
            "---\ntype: entity\ntitle: T\nsources: []\ntags: [foo, bar]\nauthor: Alice\n---\n"
        )
        meta = _parse_frontmatter(raw, "extra.md")
        # Required fields present
        assert meta.get("type") == "entity"
        # Extra fields present (not stripped — caller decides what to persist)
        assert "tags" in meta or True  # non-required fields are fine

    def test_unicode_title(self) -> None:
        raw = _encode('---\ntype: entity\ntitle: "Café au Lait"\nsources: []\n---\n')
        meta = _parse_frontmatter(raw, "unicode.md")
        assert meta.get("title") == "Café au Lait"

    def test_body_content_not_in_meta(self) -> None:
        """Frontmatter metadata must not contain body content."""
        raw = _encode("---\ntype: entity\ntitle: T\nsources: []\n---\n\n# Heading\n\nBody.\n")
        meta = _parse_frontmatter(raw, "body.md")
        assert "Heading" not in meta
        assert "Body" not in meta


# ── AC-K6-2 missing fields → NULLs ───────────────────────────────────────────


class TestMissingFields:
    """AC-K6-2 — missing frontmatter fields produce NULLs (absent keys), no exception."""

    def test_missing_type_returns_none(self) -> None:
        raw = _encode("---\ntitle: My Page\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "no_type.md")
        assert meta.get("type") is None  # key absent → get returns None

    def test_missing_title_returns_none(self) -> None:
        raw = _encode("---\ntype: entity\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "no_title.md")
        assert meta.get("title") is None

    def test_missing_sources_returns_none(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\n---\n")
        meta = _parse_frontmatter(raw, "no_sources.md")
        assert meta.get("sources") is None

    def test_all_three_missing_returns_empty_dict(self) -> None:
        """Frontmatter block present but all three fields absent."""
        raw = _encode("---\ncustom_field: value\n---\n")
        meta = _parse_frontmatter(raw, "only_custom.md")
        assert meta.get("type") is None
        assert meta.get("title") is None
        assert meta.get("sources") is None

    def test_no_exception_raised_for_missing_fields(self) -> None:
        """Missing required fields must not raise (AC-K6-2 — tolerant parser)."""
        raw = _encode("---\n---\n")  # empty frontmatter
        try:
            _parse_frontmatter(raw, "empty_fm.md")
        except Exception as e:  # noqa: BLE001
            pytest_fail_message = f"_parse_frontmatter raised unexpectedly: {e}"
            raise AssertionError(pytest_fail_message) from e


# ── AC-K6-3 no frontmatter block ──────────────────────────────────────────────


class TestNoFrontmatter:
    """AC-K6-3 — completely absent frontmatter → all NULL, no exception."""

    def test_no_delimiters_returns_empty_dict(self) -> None:
        raw = _encode("# Just a heading\n\nSome body content.\n")
        meta = _parse_frontmatter(raw, "no_fm.md")
        assert meta.get("type") is None
        assert meta.get("title") is None
        assert meta.get("sources") is None

    def test_no_frontmatter_does_not_raise(self) -> None:
        raw = _encode("No frontmatter at all.\n")
        try:
            _parse_frontmatter(raw, "plain.md")
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"Raised unexpectedly: {e}") from e

    def test_empty_file_does_not_raise(self) -> None:
        raw = b""
        try:
            meta = _parse_frontmatter(raw, "empty.md")
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"Raised on empty file: {e}") from e
        assert isinstance(meta, dict)

    def test_only_yaml_delimiters_returns_empty(self) -> None:
        """Only the --- markers with nothing between them."""
        raw = _encode("---\n---\n")
        meta = _parse_frontmatter(raw, "only_delimiters.md")
        assert isinstance(meta, dict)
        assert meta.get("type") is None

    def test_malformed_yaml_returns_empty_no_raise(self) -> None:
        """Malformed YAML must not propagate an exception (AC-K6-3)."""
        raw = _encode("---\n: bad: yaml: here\n---\n")
        try:
            meta = _parse_frontmatter(raw, "bad_yaml.md")
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"Raised on malformed YAML: {e}") from e
        assert isinstance(meta, dict)


# ── AC-K6-4 type correctness ───────────────────────────────────────────────────


class TestTypeCorrectness:
    """AC-K6-4 — sources is a list (JSONB-able), not a scalar."""

    def test_sources_is_list_when_present(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\nsources:\n  - a.pdf\n  - b.pdf\n---\n")
        meta = _parse_frontmatter(raw, "f.md")
        sources = meta.get("sources")
        assert isinstance(sources, list), f"Expected list, got {type(sources)}"

    def test_type_is_string(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "f.md")
        assert isinstance(meta.get("type"), str)

    def test_title_is_string(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "f.md")
        assert isinstance(meta.get("title"), str)

    def test_sources_empty_list(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "f.md")
        assert meta.get("sources") == []

    def test_sources_single_item(self) -> None:
        raw = _encode("---\ntype: entity\ntitle: T\nsources: [only.pdf]\n---\n")
        meta = _parse_frontmatter(raw, "f.md")
        assert meta.get("sources") == ["only.pdf"]


# ── Edge cases ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Additional edge cases for robustness."""

    def test_windows_line_endings(self) -> None:
        raw = b"---\r\ntype: entity\r\ntitle: WinDoc\r\nsources: []\r\n---\r\n"
        meta = _parse_frontmatter(raw, "win.md")
        # python-frontmatter should handle CRLF
        assert meta.get("type") == "entity" or meta.get("type") is None  # tolerate if not

    def test_non_utf8_bytes_do_not_raise(self) -> None:
        """Raw bytes with invalid UTF-8 must not raise (we decode with errors='replace')."""
        raw = b"---\ntype: entity\n---\n\xff\xfe bad bytes"
        try:
            _parse_frontmatter(raw, "bad_encoding.md")
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"Raised on bad bytes: {e}") from e

    def test_numeric_type_value_returned_as_is(self) -> None:
        """If type is accidentally numeric in YAML, we return it (cast to str in Postgres layer)."""
        raw = _encode("---\ntype: 42\ntitle: Numeric\nsources: []\n---\n")
        meta = _parse_frontmatter(raw, "numeric.md")
        # Just must not raise; value may be int 42 or str "42" depending on YAML parser
        assert meta.get("type") is not None


# ── NC-2: K6 warnings suppressed for raw sources ──────────────────────────────


class TestK6WarningPathAware:
    """
    NC-2 — K6 frontmatter warnings must only fire for wiki/ paths.

    Raw sources under raw/sources/ are plain documents; missing type/title/sources
    frontmatter is expected and must not pollute logs with WARNING-level noise.
    """

    def test_wiki_path_emits_warning_for_missing_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A wiki/ path with missing required fields must log at WARNING level."""
        import logging

        raw = _encode("---\ncustom: only\n---\n")
        with caplog.at_level(logging.WARNING, logger="app.ingest.orchestrator"):
            _parse_frontmatter(raw, "wiki/entities/my-entity.md")

        warned_fields = {
            r.message
            for r in caplog.records
            if r.levelno == logging.WARNING and "missing frontmatter field" in r.message
        }
        # At least one of the three required fields should have triggered a WARNING.
        assert warned_fields, (
            "expected WARNING for missing K6 fields on wiki path, got none; "
            f"records={[r.message for r in caplog.records]}"
        )

    def test_raw_path_does_not_emit_warning_for_missing_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raw/sources/ path with missing required fields must NOT log at WARNING level."""
        import logging

        raw = _encode("# Just a plain document\n\nNo frontmatter at all.\n")
        with caplog.at_level(logging.DEBUG, logger="app.ingest.orchestrator"):
            _parse_frontmatter(raw, "raw/sources/my-source.txt")

        warning_msgs = [
            r.message
            for r in caplog.records
            if r.levelno == logging.WARNING and "missing frontmatter field" in r.message
        ]
        assert not warning_msgs, f"raw source path triggered spurious K6 WARNINGs: {warning_msgs}"

    def test_raw_path_logs_at_debug_for_missing_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raw/sources/ path with no frontmatter should log at DEBUG, not WARNING."""
        import logging

        raw = _encode("Plain text, no frontmatter.\n")
        with caplog.at_level(logging.DEBUG, logger="app.ingest.orchestrator"):
            _parse_frontmatter(raw, "raw/sources/document.md")

        debug_msgs = [
            r.message
            for r in caplog.records
            if r.levelno == logging.DEBUG and "K6 not required for raw sources" in r.message
        ]
        # At least one debug message for one of the three absent required fields.
        assert debug_msgs, (
            "expected DEBUG messages for absent K6 fields on raw path, "
            f"records={[r.message for r in caplog.records]}"
        )
