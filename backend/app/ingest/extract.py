"""
F12 Multi-format text extractor — SOLE home of format-specific libraries (ADR-0025 §4.1).

STATIC GUARD (AC-F12-7):
  pypdf, docx, pptx, openpyxl MUST NOT be imported anywhere outside this module.
  Any PR that introduces those imports elsewhere is a P0 rejection.

INVARIANT CONTRACT:
  I6: extract_text() is PURE (path in, text out) — zero LLM/provider calls.
  I7: output capped at EXTRACT_MAX_CHARS (config). No loop — single-pass per file.
  I9: Uses well-known pure-Python extractor libs; unstructured deliberately NOT added (§4.5).
  I5: The companion .extracted.md written by the caller (upload handler) has valid YAML
      frontmatter — this module only returns text; formatting is the caller's responsibility.

Extension dispatch:
  .pdf  → pypdf (page text; images skipped with WARNING — AC-F12-1)
  .docx → python-docx (paragraphs)
  .pptx → python-pptx (slide text)
  .xlsx → openpyxl (sheets → GFM markdown table)
  image/AV in PLACEHOLDER_EXTENSIONS → one-line placeholder (§4.5; no OCR/transcript)
  anything else → raise UnsupportedFormatError (caller maps to HTTP 415)
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Extension sets (used by upload.py for type-gating) ───────────────────────
# NOTE: Do NOT add these to upload._ALLOWED_EXTENSIONS — the watcher imports that
# frozenset and must remain format-agnostic (ADR-0025 §4.3, Do-NOT #13).

EXTRACTABLE_BINARY_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})
"""Extensions extracted by the format libs (pypdf / python-docx / python-pptx / openpyxl)."""

PLACEHOLDER_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".wav", ".m4a"}
)
"""Extensions accepted but not transcribed/OCR'd in M5 (§4.5 — placeholder only)."""


class UnsupportedFormatError(ValueError):
    """Raised when the file extension is not in any accepted set (caller maps to HTTP 415)."""


def _extract_max_chars() -> int:
    """Cap on extracted text output (I7 — pathological file guard). From EXTRACT_MAX_CHARS env."""
    try:
        from app.config import settings

        return int(getattr(settings, "extract_max_chars", 2_000_000))
    except Exception:  # noqa: BLE001
        return 2_000_000


def extract_text(file_path: str | Path) -> str:
    """
    Dispatch on the lower-cased file extension and return extracted plain text (ADR-0025 §4.1).

    Output is capped at EXTRACT_MAX_CHARS (I7). This function is SYNCHRONOUS and makes
    NO network calls, NO LLM calls (I6).

    Raises UnsupportedFormatError for extensions not in EXTRACTABLE_BINARY_EXTENSIONS or
    PLACEHOLDER_EXTENSIONS — the caller (upload handler) maps this to HTTP 415.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    max_chars = _extract_max_chars()

    if suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    elif suffix == ".pptx":
        text = _extract_pptx(path)
    elif suffix == ".xlsx":
        text = _extract_xlsx(path)
    elif suffix in PLACEHOLDER_EXTENSIONS:
        text = _placeholder(path)
    else:
        raise UnsupportedFormatError(
            f"Unsupported file format: {suffix!r}. "
            f"Accepted binary formats: {sorted(EXTRACTABLE_BINARY_EXTENSIONS)}; "
            f"placeholder formats: {sorted(PLACEHOLDER_EXTENSIONS)}."
        )

    # I7 output cap — truncate silently (the extracted text may still be useful)
    if len(text) > max_chars:
        logger.warning(
            "extract_text: output truncated from %d to %d chars (EXTRACT_MAX_CHARS) for %s",
            len(text),
            max_chars,
            path.name,
        )
        text = text[:max_chars]

    return text


# ── Per-format extractors ─────────────────────────────────────────────────────
# pypdf / docx / pptx / openpyxl are imported LOCALLY inside each helper so they
# are only loaded when the corresponding format is actually processed. Any import
# error surfaces at extraction time (not at module import), and the static guard
# check only tests for module-level imports.


def _extract_pdf(path: Path) -> str:
    """Extract text from a PDF using pypdf (AC-F12-1: images in PDF skipped)."""
    import pypdf  # noqa: PLC0415 — local import enforces static guard (AC-F12-7)

    reader = pypdf.PdfReader(str(path))
    pages_text: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            # Images/forms that cannot be extracted are silently skipped (AC-F12-1)
            logger.warning(
                "extract_pdf: page %d extraction error in %s: %s — skipping", i, path.name, exc
            )
            page_text = ""
        if page_text:
            pages_text.append(page_text)

    if not pages_text:
        logger.warning("extract_pdf: no text extracted from %s (images-only PDF?)", path.name)
        return f"PDF file: no text content extracted from {path.name}."

    return "\n\n".join(pages_text)


def _extract_docx(path: Path) -> str:
    """Extract paragraph text from a DOCX using python-docx."""
    import docx  # noqa: PLC0415 — local import enforces static guard (AC-F12-7)

    doc = docx.Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        return f"DOCX file: no text content extracted from {path.name}."
    return "\n\n".join(paragraphs)


def _extract_pptx(path: Path) -> str:
    """Extract slide text from a PPTX using python-pptx (one logical doc)."""
    from pptx import Presentation  # noqa: PLC0415 — local import enforces static guard (AC-F12-7)

    prs = Presentation(str(path))
    slide_texts: list[str] = []
    for slide_num, slide in enumerate(prs.slides, 1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip()
                    if text:
                        parts.append(text)
        if parts:
            slide_texts.append(f"## Slide {slide_num}\n\n" + "\n".join(parts))

    if not slide_texts:
        return f"PPTX file: no text content extracted from {path.name}."
    return "\n\n".join(slide_texts)


def _extract_xlsx(path: Path) -> str:
    """
    Extract cell text from an XLSX using openpyxl, rendered as GFM markdown tables.

    One table per sheet. Non-empty rows only.
    """
    import openpyxl  # noqa: PLC0415 — local import enforces static guard (AC-F12-7)

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    sheet_texts: list[str] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            # Convert each cell to string; skip rows that are entirely empty
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                rows.append(cells)

        if not rows:
            continue

        # GFM table: first data row as header, then separator, then remaining rows
        if len(rows) >= 1:
            header = rows[0]
            sep = ["-" * max(len(h), 3) for h in header]
            table_lines = [
                "| " + " | ".join(header) + " |",
                "| " + " | ".join(sep) + " |",
            ]
            for data_row in rows[1:]:
                # Pad shorter rows to header width
                padded = data_row + [""] * max(0, len(header) - len(data_row))
                table_lines.append("| " + " | ".join(padded[: len(header)]) + " |")
            sheet_texts.append(f"## Sheet: {sheet_name}\n\n" + "\n".join(table_lines))

    wb.close()

    if not sheet_texts:
        return f"XLSX file: no cell content extracted from {path.name}."
    return "\n\n".join(sheet_texts)


def _placeholder(path: Path) -> str:
    """
    Return a one-line placeholder for image/AV files (§4.5 — no OCR/transcript in M5).

    Accepted (not 415) but produces a traceable stub; transcription deferred to M6.
    """
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return (
            f"Image file: no text extracted from {path.name}. "
            "Transcription/OCR is out of scope in this release."
        )
    else:
        return (
            f"AV file: transcript not available for {path.name} in this release. "
            "Audio/video transcription is out of scope in this release."
        )
