"""
Gloss-catalogue summary extraction (K3, 1.9.4 W6, finding PF-INDEX-GLOSS-1).

``extract_first_paragraph_summary`` derives a short, non-LLM gloss for a wiki page's body,
used as the em-dash text next to the wikilink in ``index.md`` (see ``app.wiki.index``) and
persisted in ``pages.summary`` (migration 0036). Deliberately mechanical — no InferenceProvider
call here (I6 is about the ingest/analysis AI; a catalogue gloss is not analysis).

Extraction rule (simple, Markdown-aware):
  1. Strip a leading H1/H2 heading line (``# Title`` / ``## Title``) if present — the title is
     already the wikilink text, repeating it in the gloss adds no information.
  2. Take the first non-empty, non-heading, non-list-marker-only paragraph (a run of
     non-blank lines up to the first blank line).
  3. Collapse internal whitespace/newlines to single spaces and strip Markdown emphasis
     markers (``*_`) that would otherwise render awkwardly as plain text.
  4. Truncate to *max_chars* (default matches the index.md gloss cap) with an ellipsis.

Returns ``None`` for an empty/whitespace-only body (no gloss to show — the catalogue falls
back to the bare wikilink line, unchanged from before this feature).
"""

from __future__ import annotations

import re

# Matches a leading Markdown heading line, e.g. "# Title" or "## Some Title".
_HEADING_RE = re.compile(r"^#{1,6}\s+.*$")

# Matches a line that is ONLY a Markdown horizontal rule / list marker with no text — skipped
# when looking for the first real paragraph.
_SKIP_LINE_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")

# Default cap mirrors app.wiki.index._GLOSS_MAX_CHARS (kept independent — one is the storage
# cap in Postgres/rendered gloss source, the other is index.md's own render-time truncation;
# both currently 200/120 respectively, deliberately generous at the storage layer so a future
# UI surface can show more than the index.md line does).
DEFAULT_MAX_CHARS: int = 200


def extract_first_paragraph_summary(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    """
    Return a short plain-text gloss derived from *body*'s first real paragraph, or None.

    *body* is expected to be the page content WITHOUT YAML frontmatter (callers already have
    this — it is the same text passed to ``upsert_vector`` for embedding).
    """
    if not body or not body.strip():
        return None

    lines = body.splitlines()
    paragraph_lines: list[str] = []
    started = False

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            if started:
                break  # end of the first paragraph
            continue  # skip leading blank lines

        if _HEADING_RE.match(line) or _SKIP_LINE_RE.match(line):
            if started:
                break  # a heading/rule ends the paragraph we were collecting
            continue  # skip a leading heading/rule before any paragraph text

        paragraph_lines.append(line)
        started = True

    if not paragraph_lines:
        return None

    text = " ".join(paragraph_lines)
    text = re.sub(r"\s+", " ", text).strip()
    # Strip common Markdown emphasis/heading-leftover markers for a clean plain-text gloss.
    text = re.sub(r"[*_`]", "", text)
    text = text.lstrip("#").strip()

    if not text:
        return None

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"

    return text
