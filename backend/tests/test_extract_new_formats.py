"""
F12 Multi-format ingest — new extractor unit tests (P3-c, v1.5 LLM Wiki parity) [F12].

Tests:
  T-EXT-NEW-001  CSV → GFM markdown table (header + rows)
  T-EXT-NEW-002  CSV empty file → placeholder
  T-EXT-NEW-003  HTML → markdown (heading, paragraph, table)
  T-EXT-NEW-004  HTML empty → placeholder
  T-EXT-NEW-005  MDX → stripped text (import/export lines and JSX tags removed)
  T-EXT-NEW-006  MDX empty → placeholder
  T-EXT-NEW-007  RTF → plain text
  T-EXT-NEW-008  RTF empty → placeholder
  T-EXT-NEW-009  ODT → paragraph text
  T-EXT-NEW-010  ODT empty → placeholder
  T-EXT-NEW-011  ODS → GFM table per sheet
  T-EXT-NEW-012  ODS empty → placeholder
  T-EXT-NEW-013  ODP → slide text with ## Slide N headers
  T-EXT-NEW-014  ODP empty → placeholder
  T-EXT-NEW-015  All new suffixes are in EXTRACTABLE_BINARY_EXTENSIONS
  T-EXT-NEW-016  _EXTRACTABLE_EXTENSIONS (upload.py) mirrors EXTRACTABLE_BINARY_EXTENSIONS
  T-EXT-NEW-017  .doc is NOT in EXTRACTABLE_BINARY_EXTENSIONS (deferred)
  T-EXT-NEW-018  Static guard — markdownify/striprtf/odf not imported outside extract.py
  T-EXT-NEW-019  extract_text() dispatches CSV via public API
  T-EXT-NEW-020  extract_text() dispatches HTML via public API
  T-EXT-NEW-021  extract_text() dispatches MDX via public API
  T-EXT-NEW-022  extract_text() dispatches RTF via public API
  T-EXT-NEW-023  extract_text() dispatches ODT via public API
  T-EXT-NEW-024  extract_text() dispatches ODS via public API
  T-EXT-NEW-025  extract_text() dispatches ODP via public API
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_odf_zip(mimetype: bytes, content_xml: str) -> bytes:
    """Build a minimal ODF document ZIP (used for ODT/ODS/ODP fixtures)."""
    manifest_xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype.decode()}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", content_xml)
        zf.writestr("META-INF/manifest.xml", manifest_xml)
    return buf.getvalue()


def _odt_bytes(paragraphs: list[str]) -> bytes:
    """Minimal ODT with the given paragraphs."""
    para_xml = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    content = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<office:document-content"
        ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' office:version="1.2">'
        "<office:body><office:text>"
        + para_xml
        + "</office:text></office:body></office:document-content>"
    )
    return _make_odf_zip(b"application/vnd.oasis.opendocument.text", content)


def _ods_bytes(sheets: dict[str, list[list[str]]]) -> bytes:
    """Minimal ODS with the given sheets (name → rows of cells)."""
    tables_xml = ""
    for sheet_name, rows in sheets.items():
        row_xml = ""
        for row in rows:
            cells = "".join(
                f"<table:table-cell><text:p>{cell}</text:p></table:table-cell>" for cell in row
            )
            row_xml += f"<table:table-row>{cells}</table:table-row>"
        tables_xml += f'<table:table table:name="{sheet_name}">{row_xml}</table:table>'
    content = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<office:document-content"
        ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
        ' office:version="1.2">'
        "<office:body><office:spreadsheet>"
        + tables_xml
        + "</office:spreadsheet></office:body></office:document-content>"
    )
    return _make_odf_zip(b"application/vnd.oasis.opendocument.spreadsheet", content)


def _odp_bytes(slides: list[list[str]]) -> bytes:
    """
    Minimal ODP with the given slides (list of text strings per slide).

    Each slide becomes a draw:page with draw:frame/draw:text-box elements.
    """
    pages_xml = ""
    for slide_idx, texts in enumerate(slides):
        frames_xml = ""
        for text in texts:
            frames_xml += (
                '<draw:frame draw:name="frame" svg:x="1cm" svg:y="1cm"'
                ' svg:width="10cm" svg:height="5cm">'
                f"<draw:text-box><text:p>{text}</text:p></draw:text-box>"
                "</draw:frame>"
            )
        pages_xml += (
            f'<draw:page draw:name="slide{slide_idx}" draw:master-page-name="Master">'
            + frames_xml
            + "</draw:page>"
        )
    content = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<office:document-content"
        ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
        ' xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
        ' office:version="1.2">'
        "<office:body><office:presentation>"
        + pages_xml
        + "</office:presentation></office:body></office:document-content>"
    )
    return _make_odf_zip(b"application/vnd.oasis.opendocument.presentation", content)


# ── T-EXT-NEW-001 / 002: CSV ─────────────────────────────────────────────────


class TestCsvExtraction:
    """T-EXT-NEW-001/002: CSV → GFM table; empty → placeholder."""

    def test_csv_gfm_table(self, tmp_path: Path) -> None:
        """CSV with header + data rows produces a GFM markdown table."""
        from app.ingest.extract import extract_text

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("Name,Score,Grade\nAlice,95,A\nBob,82,B\n", encoding="utf-8")

        result = extract_text(csv_file)
        assert isinstance(result, str)
        assert "|" in result
        assert "Name" in result
        assert "Alice" in result
        assert "Bob" in result
        assert "---" in result or "|-" in result

    def test_csv_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """Empty CSV returns a placeholder message."""
        from app.ingest.extract import extract_text

        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("", encoding="utf-8")

        result = extract_text(csv_file)
        assert isinstance(result, str)
        assert len(result) > 0
        # Must be a placeholder, not a crash
        assert "csv" in result.lower() or "no content" in result.lower()

    def test_csv_single_header_row(self, tmp_path: Path) -> None:
        """CSV with only a header row produces a table with just the header + separator."""
        from app.ingest.extract import extract_text

        csv_file = tmp_path / "header_only.csv"
        csv_file.write_text("Col1,Col2,Col3\n", encoding="utf-8")

        result = extract_text(csv_file)
        assert "Col1" in result
        assert "Col2" in result
        assert "Col3" in result


# ── T-EXT-NEW-003 / 004: HTML ────────────────────────────────────────────────


class TestHtmlExtraction:
    """T-EXT-NEW-003/004: HTML → markdown; empty → placeholder."""

    def test_html_heading_and_paragraph(self, tmp_path: Path) -> None:
        """HTML with a heading and paragraph is converted to markdown."""
        from app.ingest.extract import extract_text

        html_file = tmp_path / "page.html"
        html_file.write_text(
            "<html><body><h1>My Title</h1><p>A paragraph of text.</p></body></html>",
            encoding="utf-8",
        )

        result = extract_text(html_file)
        assert isinstance(result, str)
        assert "My Title" in result
        assert "A paragraph of text." in result

    def test_html_table_becomes_gfm(self, tmp_path: Path) -> None:
        """HTML table is converted to a GFM markdown table."""
        from app.ingest.extract import extract_text

        html_file = tmp_path / "table.html"
        html_file.write_text(
            "<table><tr><th>Fruit</th><th>Count</th></tr>"
            "<tr><td>Apple</td><td>3</td></tr></table>",
            encoding="utf-8",
        )

        result = extract_text(html_file)
        assert "Fruit" in result
        assert "Apple" in result
        assert "|" in result

    def test_html_scripts_stripped(self, tmp_path: Path) -> None:
        """Script content is stripped from the HTML output."""
        from app.ingest.extract import extract_text

        html_file = tmp_path / "scripted.html"
        html_file.write_text(
            "<html><head><script>alert('xss')</script></head>"
            "<body><p>Clean content here.</p></body></html>",
            encoding="utf-8",
        )

        result = extract_text(html_file)
        assert "xss" not in result
        assert "Clean content here." in result

    def test_html_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """Empty HTML file returns a placeholder message."""
        from app.ingest.extract import extract_text

        html_file = tmp_path / "empty.html"
        html_file.write_text("", encoding="utf-8")

        result = extract_text(html_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "html" in result.lower() or "no text" in result.lower()


# ── T-EXT-NEW-005 / 006: MDX ─────────────────────────────────────────────────


class TestMdxExtraction:
    """T-EXT-NEW-005/006: MDX → stripped markdown text; empty → placeholder."""

    def test_mdx_strips_imports_and_exports(self, tmp_path: Path) -> None:
        """MDX: import/export lines are stripped; markdown body is preserved."""
        from app.ingest.extract import extract_text

        mdx_file = tmp_path / "component.mdx"
        mdx_file.write_text(
            "import React from 'react'\n"
            "import { Foo } from './foo'\n"
            "export const meta = { title: 'Test' }\n"
            "\n"
            "# My MDX Page\n"
            "\n"
            "This is a regular paragraph.\n",
            encoding="utf-8",
        )

        result = extract_text(mdx_file)
        assert "import React" not in result
        assert "export const" not in result
        assert "My MDX Page" in result
        assert "regular paragraph" in result

    def test_mdx_strips_jsx_tags(self, tmp_path: Path) -> None:
        """MDX: JSX component tags are stripped; text content is preserved."""
        from app.ingest.extract import extract_text

        mdx_file = tmp_path / "jsx.mdx"
        mdx_file.write_text(
            "# Heading\n"
            "\n"
            "<MyComponent prop='value'>\n"
            "  Inner content preserved.\n"
            "</MyComponent>\n"
            "\n"
            "<SelfClosing />\n"
            "\n"
            "Trailing text.\n",
            encoding="utf-8",
        )

        result = extract_text(mdx_file)
        assert "MyComponent" not in result
        assert "SelfClosing" not in result
        assert "Heading" in result
        assert "Inner content preserved." in result
        assert "Trailing text." in result

    def test_mdx_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """Empty MDX file returns a placeholder message."""
        from app.ingest.extract import extract_text

        mdx_file = tmp_path / "empty.mdx"
        mdx_file.write_text("", encoding="utf-8")

        result = extract_text(mdx_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "mdx" in result.lower() or "no text" in result.lower()

    def test_mdx_only_imports_returns_placeholder(self, tmp_path: Path) -> None:
        """MDX with only import/export lines produces a placeholder (no body left)."""
        from app.ingest.extract import extract_text

        mdx_file = tmp_path / "imports_only.mdx"
        mdx_file.write_text(
            "import A from 'a'\nimport B from 'b'\nexport default function() {}\n",
            encoding="utf-8",
        )

        result = extract_text(mdx_file)
        assert isinstance(result, str)
        assert len(result) > 0


# ── T-EXT-NEW-007 / 008: RTF ─────────────────────────────────────────────────


class TestRtfExtraction:
    """T-EXT-NEW-007/008: RTF → plain text; empty → placeholder."""

    def test_rtf_plain_text(self, tmp_path: Path) -> None:
        """Basic RTF file yields extracted plain text."""
        from app.ingest.extract import extract_text

        rtf_file = tmp_path / "doc.rtf"
        rtf_content = r"{\rtf1\ansi\deff0 Hello RTF world\par This is a second line.}"
        rtf_file.write_text(rtf_content, encoding="utf-8")

        result = extract_text(rtf_file)
        assert isinstance(result, str)
        assert "Hello RTF world" in result
        assert "second line" in result

    def test_rtf_empty_content_returns_placeholder(self, tmp_path: Path) -> None:
        """RTF file with no text content returns a placeholder message."""
        from app.ingest.extract import extract_text

        rtf_file = tmp_path / "empty.rtf"
        # Minimal RTF header with no text
        rtf_file.write_text(r"{\rtf1\ansi}", encoding="utf-8")

        result = extract_text(rtf_file)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_rtf_multi_paragraph(self, tmp_path: Path) -> None:
        """RTF with multiple paragraphs returns all text content."""
        from app.ingest.extract import extract_text

        rtf_file = tmp_path / "multi.rtf"
        rtf_content = (
            r"{\rtf1\ansi " r"First paragraph.\par " r"Second paragraph.\par " r"Third paragraph.}"
        )
        rtf_file.write_text(rtf_content, encoding="utf-8")

        result = extract_text(rtf_file)
        assert "First paragraph." in result
        assert "Second paragraph." in result
        assert "Third paragraph." in result


# ── T-EXT-NEW-009 / 010: ODT ─────────────────────────────────────────────────


class TestOdtExtraction:
    """T-EXT-NEW-009/010: ODT paragraph text extraction; empty → placeholder."""

    def test_odt_paragraphs(self, tmp_path: Path) -> None:
        """ODT with multiple paragraphs returns all text content."""
        from app.ingest.extract import extract_text

        odt_file = tmp_path / "doc.odt"
        odt_file.write_bytes(
            _odt_bytes(["First ODT paragraph.", "Second ODT paragraph.", "Third line here."])
        )

        result = extract_text(odt_file)
        assert isinstance(result, str)
        assert "First ODT paragraph." in result
        assert "Second ODT paragraph." in result
        assert "Third line here." in result

    def test_odt_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """ODT with no text paragraphs returns a placeholder message."""
        from app.ingest.extract import extract_text

        odt_file = tmp_path / "empty.odt"
        odt_file.write_bytes(_odt_bytes([]))

        result = extract_text(odt_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "odt" in result.lower() or "no text" in result.lower()

    def test_odt_single_paragraph(self, tmp_path: Path) -> None:
        """ODT with a single paragraph returns that paragraph."""
        from app.ingest.extract import extract_text

        odt_file = tmp_path / "single.odt"
        odt_file.write_bytes(_odt_bytes(["Only one paragraph."]))

        result = extract_text(odt_file)
        assert "Only one paragraph." in result


# ── T-EXT-NEW-011 / 012: ODS ─────────────────────────────────────────────────


class TestOdsExtraction:
    """T-EXT-NEW-011/012: ODS cell extraction → GFM table; empty → placeholder."""

    def test_ods_gfm_table(self, tmp_path: Path) -> None:
        """ODS with header + data rows produces a GFM markdown table."""
        from app.ingest.extract import extract_text

        ods_file = tmp_path / "data.ods"
        ods_file.write_bytes(
            _ods_bytes({"Sheet1": [["Name", "Value"], ["Alpha", "42"], ["Beta", "99"]]})
        )

        result = extract_text(ods_file)
        assert isinstance(result, str)
        assert "|" in result
        assert "Name" in result
        assert "Alpha" in result
        assert "Beta" in result
        assert "---" in result or "|-" in result

    def test_ods_sheet_name_in_output(self, tmp_path: Path) -> None:
        """Sheet name appears as a heading in the GFM output."""
        from app.ingest.extract import extract_text

        ods_file = tmp_path / "named.ods"
        ods_file.write_bytes(_ods_bytes({"MyData": [["Col"], ["Val"]]}))

        result = extract_text(ods_file)
        assert "MyData" in result

    def test_ods_multiple_sheets(self, tmp_path: Path) -> None:
        """Multiple ODS sheets produce separate GFM table sections."""
        from app.ingest.extract import extract_text

        ods_file = tmp_path / "multi.ods"
        ods_file.write_bytes(
            _ods_bytes(
                {
                    "Sheet1": [["A", "B"], ["1", "2"]],
                    "Sheet2": [["X", "Y"], ["3", "4"]],
                }
            )
        )

        result = extract_text(ods_file)
        assert "Sheet1" in result
        assert "Sheet2" in result

    def test_ods_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """ODS with no cell content returns a placeholder message."""
        from app.ingest.extract import extract_text

        ods_file = tmp_path / "empty.ods"
        ods_file.write_bytes(_ods_bytes({}))

        result = extract_text(ods_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "ods" in result.lower() or "no" in result.lower()


# ── T-EXT-NEW-013 / 014: ODP ─────────────────────────────────────────────────


class TestOdpExtraction:
    """T-EXT-NEW-013/014: ODP slide text extraction; empty → placeholder."""

    def test_odp_slide_text(self, tmp_path: Path) -> None:
        """ODP with multiple slides returns text from each slide."""
        from app.ingest.extract import extract_text

        odp_file = tmp_path / "pres.odp"
        odp_file.write_bytes(_odp_bytes([["Slide one content"], ["Slide two content"]]))

        result = extract_text(odp_file)
        assert isinstance(result, str)
        assert "Slide one content" in result
        assert "Slide two content" in result

    def test_odp_slide_headers(self, tmp_path: Path) -> None:
        """ODP output includes ## Slide N headers for each slide."""
        from app.ingest.extract import extract_text

        odp_file = tmp_path / "headers.odp"
        odp_file.write_bytes(_odp_bytes([["First slide text"], ["Second slide text"]]))

        result = extract_text(odp_file)
        assert "## Slide 1" in result
        assert "## Slide 2" in result

    def test_odp_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """ODP with no text content returns a placeholder message."""
        from app.ingest.extract import extract_text

        odp_file = tmp_path / "empty.odp"
        odp_file.write_bytes(_odp_bytes([]))

        result = extract_text(odp_file)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "odp" in result.lower() or "no text" in result.lower()

    def test_odp_multiple_texts_per_slide(self, tmp_path: Path) -> None:
        """ODP slide with multiple text frames returns all text."""
        from app.ingest.extract import extract_text

        odp_file = tmp_path / "multi_text.odp"
        odp_file.write_bytes(_odp_bytes([["Title text", "Body text here"]]))

        result = extract_text(odp_file)
        assert "Title text" in result
        assert "Body text here" in result


# ── T-EXT-NEW-015: Extension set completeness ────────────────────────────────


class TestNewExtensionSets:
    """T-EXT-NEW-015/016/017: Extension set constants include all P3-c formats."""

    _NEW_EXTS = {".csv", ".html", ".mdx", ".rtf", ".odt", ".ods", ".odp"}

    def test_all_new_exts_in_extractable_binary(self) -> None:
        """All P3-c new extensions are in EXTRACTABLE_BINARY_EXTENSIONS (extract.py)."""
        from app.ingest.extract import EXTRACTABLE_BINARY_EXTENSIONS

        for ext in self._NEW_EXTS:
            assert (
                ext in EXTRACTABLE_BINARY_EXTENSIONS
            ), f"{ext} missing from EXTRACTABLE_BINARY_EXTENSIONS"

    def test_extractable_extensions_upload_mirrors_extract(self) -> None:
        """_EXTRACTABLE_EXTENSIONS (upload.py) mirrors EXTRACTABLE_BINARY_EXTENSIONS (T-EXT-NEW-016)."""
        from app.ingest.extract import EXTRACTABLE_BINARY_EXTENSIONS
        from app.upload import _EXTRACTABLE_EXTENSIONS

        assert EXTRACTABLE_BINARY_EXTENSIONS == _EXTRACTABLE_EXTENSIONS, (
            "upload._EXTRACTABLE_EXTENSIONS and extract.EXTRACTABLE_BINARY_EXTENSIONS are out of "
            "sync. Keep them identical.\n"
            f"  In extract.py only: {EXTRACTABLE_BINARY_EXTENSIONS - _EXTRACTABLE_EXTENSIONS}\n"
            f"  In upload.py only:  {_EXTRACTABLE_EXTENSIONS - EXTRACTABLE_BINARY_EXTENSIONS}"
        )

    def test_doc_not_in_extractable_extensions(self) -> None:
        """.doc is NOT in EXTRACTABLE_BINARY_EXTENSIONS (deferred — T-EXT-NEW-017)."""
        from app.ingest.extract import EXTRACTABLE_BINARY_EXTENSIONS

        assert ".doc" not in EXTRACTABLE_BINARY_EXTENSIONS, (
            ".doc should not be in EXTRACTABLE_BINARY_EXTENSIONS — "
            "it is deferred (see module docstring ADR note, P3-c)"
        )

    def test_new_exts_not_in_allowed_extensions(self) -> None:
        """New binary extensions are NOT in _ALLOWED_EXTENSIONS (watcher safety)."""
        from app.upload import _ALLOWED_EXTENSIONS

        for ext in self._NEW_EXTS:
            assert (
                ext not in _ALLOWED_EXTENSIONS
            ), f"{ext} found in _ALLOWED_EXTENSIONS — would break the watcher (ADR-0025 §4.3)"


# ── T-EXT-NEW-018: Static guard for new libs ─────────────────────────────────


class TestNewStaticGuard:
    """T-EXT-NEW-018: markdownify/striprtf/odf are only imported inside extract.py."""

    def _grep_imports(self, root: Path, forbidden: list[str]) -> list[str]:
        violations: list[str] = []
        for py_file in root.rglob("*.py"):
            if "extract.py" in py_file.name:
                continue  # allowed
            if "__pycache__" in str(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for pkg in forbidden:
                if f"import {pkg}" in text or f"from {pkg}" in text:
                    violations.append(f"{py_file.relative_to(root)}: imports {pkg}")
        return violations

    def test_no_new_format_lib_imports_outside_extract(self) -> None:
        """markdownify, striprtf, odf are not imported outside ingest/extract.py."""
        backend_app = Path(__file__).resolve().parent.parent / "app"
        violations = self._grep_imports(
            backend_app,
            ["markdownify", "striprtf", "odf"],
        )
        assert (
            not violations
        ), "New format lib imports found outside ingest/extract.py:\n" + "\n".join(violations)


# ── T-EXT-NEW-019..025: Public extract_text() dispatch ───────────────────────


class TestNewExtractDispatch:
    """T-EXT-NEW-019..025: extract_text() dispatches all new formats via public API."""

    def test_dispatch_csv(self, tmp_path: Path) -> None:
        """extract_text() dispatches .csv files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.csv"
        f.write_text("A,B\n1,2\n", encoding="utf-8")
        result = extract_text(f)
        assert "A" in result and "B" in result

    def test_dispatch_html(self, tmp_path: Path) -> None:
        """extract_text() dispatches .html files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.html"
        f.write_text("<p>HTML dispatch test</p>", encoding="utf-8")
        result = extract_text(f)
        assert "HTML dispatch test" in result

    def test_dispatch_mdx(self, tmp_path: Path) -> None:
        """extract_text() dispatches .mdx files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.mdx"
        f.write_text("# MDX Title\n\nBody content here.\n", encoding="utf-8")
        result = extract_text(f)
        assert "MDX Title" in result or "Body content" in result

    def test_dispatch_rtf(self, tmp_path: Path) -> None:
        """extract_text() dispatches .rtf files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.rtf"
        f.write_text(r"{\rtf1\ansi RTF dispatch test}", encoding="utf-8")
        result = extract_text(f)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_dispatch_odt(self, tmp_path: Path) -> None:
        """extract_text() dispatches .odt files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.odt"
        f.write_bytes(_odt_bytes(["ODT dispatch test paragraph."]))
        result = extract_text(f)
        assert "ODT dispatch test" in result

    def test_dispatch_ods(self, tmp_path: Path) -> None:
        """extract_text() dispatches .ods files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.ods"
        f.write_bytes(_ods_bytes({"Data": [["Col"], ["ODS dispatch"]]}))
        result = extract_text(f)
        assert "Col" in result or "ODS dispatch" in result

    def test_dispatch_odp(self, tmp_path: Path) -> None:
        """extract_text() dispatches .odp files correctly."""
        from app.ingest.extract import extract_text

        f = tmp_path / "test.odp"
        f.write_bytes(_odp_bytes([["ODP dispatch slide text"]]))
        result = extract_text(f)
        assert "ODP dispatch slide text" in result
