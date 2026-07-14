"""Provider-neutral port of llm_wiki v0.6.3's FILE / REVIEW / LINT block parsers.

Ported 1:1 (behavior-preserving) from nashsu/llm_wiki v0.6.3:

* ``parseFileBlocks`` / ``isSafeIngestPath`` — ``src/lib/ingest.ts:388-547``
* ``parseReviewBlocks`` / ``REVIEW_BLOCK_REGEX`` — ``src/lib/ingest.ts:1967-2030``
* ``LINT_BLOCK_REGEX`` parsing — ``src/lib/lint.ts:302-432``

These are pure functions: no I/O, no DB, no app imports (stdlib only). The behavior
contract is documented in ``docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md`` (§1.5.1
parseFileBlocks, §1.5.2 sanitizer, §2 parseReviewBlocks, §3 LINT blocks).

The output shapes are trimmed to what Synapse's ingest orchestrator consumes:
``ReviewBlock.options`` is a ``list[str]`` (llm_wiki keeps ``{label, action}`` objects;
Synapse only needs the label), and ``pages`` / ``search_queries`` default to empty lists
(llm_wiki uses ``undefined``). Parsing semantics are otherwise identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "FileBlock",
    "LintBlock",
    "ParseResult",
    "ReviewBlock",
    "first_char_is_file_opener",
    "is_safe_ingest_path",
    "parse_blocks",
    "parse_file_blocks",
    "parse_lint_blocks",
    "parse_review_blocks",
]


@dataclass
class FileBlock:
    """One ``---FILE: <path>--- … ---END FILE---`` block (ingest.ts:341-344)."""

    path: str
    content: str


@dataclass
class ReviewBlock:
    """One ``---REVIEW: type | title--- … ---END REVIEW---`` block (ingest.ts:1969)."""

    type: str
    title: str
    description: str
    options: list[str]
    pages: list[str]
    search_queries: list[str]


@dataclass
class LintBlock:
    """One ``---LINT: type | severity | title--- … ---END LINT---`` block (lint.ts:302)."""

    type: str
    severity: str
    title: str
    detail: str


@dataclass
class ParseResult:
    """Aggregate parse output. ``parse_file_blocks`` leaves ``reviews`` empty."""

    files: list[FileBlock] = field(default_factory=list)
    reviews: list[ReviewBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── FILE-block markers (ingest.ts:359-360, 427) ─────────────────────────────
# Both markers are case-insensitive, tolerant of interior whitespace
# (``--- END FILE ---``), and anchored to the whole line so a stray marker in
# prose or a list item does not register.
_OPENER_LINE = re.compile(r"^---\s*FILE:\s*(.+?)\s*---\s*$", re.IGNORECASE)
_CLOSER_LINE = re.compile(r"^---\s*END\s+FILE\s*---\s*$", re.IGNORECASE)
# CommonMark fence: 3+ backticks or tildes, ≤3 leading spaces (ingest.ts:427).
_FENCE_LINE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# Prefix form of the opener, for first_char_is_file_opener (see its docstring).
_FILE_OPENER_PREFIX = re.compile(r"---\s*FILE:", re.IGNORECASE)

# ── isSafeIngestPath helpers (ingest.ts:388-423) ────────────────────────────
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")
_DRIVE_LETTER = re.compile(r"^[a-zA-Z]:")
_WIN_INVALID_CHARS = re.compile(r'[<>:"|?*]')
_TRAILING_DOT_OR_SPACE = re.compile(r"[ .]$")
_COM_DEVICE = re.compile(r"^COM[1-9]$")
_LPT_DEVICE = re.compile(r"^LPT[1-9]$")

# ── REVIEW / LINT block regexes (ingest.ts:1967, lint.ts:302) ───────────────
# NOTE on ``\w``: llm_wiki runs on JavaScript where ``\w`` is ASCII-only
# (``[A-Za-z0-9_]``). Python's ``\w`` is Unicode by default, which WOULD change
# behavior for non-ASCII type tokens (a malformed non-ASCII type is dropped by
# JS but would parse under Unicode ``\w``). We spell out the ASCII class to keep
# the port 1:1.
_REVIEW_BLOCK_REGEX = re.compile(
    r"---REVIEW:\s*([A-Za-z0-9_][A-Za-z0-9_-]*)\s*\|\s*(.+?)\s*---\n" r"([\s\S]*?)---END REVIEW---"
)
_LINT_BLOCK_REGEX = re.compile(
    r"---LINT:\s*([^\n|]+?)\s*\|\s*([^\n|]+?)\s*\|\s*([^\n-]+?)\s*---\n" r"([\s\S]*?)---END LINT---"
)
_OPTIONS_LINE = re.compile(r"^OPTIONS:\s*(.+)$", re.MULTILINE)
_PAGES_LINE = re.compile(r"^PAGES:\s*(.+)$", re.MULTILINE)
_SEARCH_LINE = re.compile(r"^SEARCH:\s*(.+)$", re.MULTILINE)
_OPTIONS_STRIP = re.compile(r"^OPTIONS:.*$", re.MULTILINE)
_PAGES_STRIP = re.compile(r"^PAGES:.*$", re.MULTILINE)
_SEARCH_STRIP = re.compile(r"^SEARCH:.*$", re.MULTILINE)

_REVIEW_KNOWN_TYPES = ("contradiction", "duplicate", "missing-page", "suggestion")
_LINT_KNOWN_TYPES = ("contradiction", "stale", "missing-page", "suggestion")
_LINT_KNOWN_SEVERITIES = ("warning", "info")


def _is_windows_safe_path_segment(segment: str) -> bool:
    """Port of ``isWindowsSafePathSegment`` (ingest.ts:406-423)."""
    if len(segment) == 0:
        return False
    if _WIN_INVALID_CHARS.search(segment):
        return False
    if _TRAILING_DOT_OR_SPACE.search(segment):
        return False
    stem = segment.split(".")[0].upper()
    if not stem:
        return False
    if stem in ("CON", "PRN", "AUX", "NUL") or _COM_DEVICE.match(stem) or _LPT_DEVICE.match(stem):
        return False
    return True


def is_safe_ingest_path(p: str) -> bool:
    """Reject FILE-block paths that try to escape ``wiki/`` (ingest.ts:388-404).

    Allowed: any path under ``wiki/`` (e.g. ``wiki/concepts/foo.md``).
    Rejected: paths not under ``wiki/``, absolute paths, drive letters / UNC,
    any ``..`` segment, Windows-invalid characters or reserved device names,
    segments ending in space / dot, and control / NUL bytes.

    (The JS ``typeof p !== "string"`` guard is dropped: ``p`` is statically typed
    ``str`` here, so it can never fire; behavior is identical for all string inputs.)
    """
    if len(p.strip()) == 0:
        return False
    # No control / NUL bytes anywhere.
    if _CONTROL_CHARS.search(p):
        return False
    # Reject absolute paths (POSIX) and Windows drive letters / UNC.
    if p.startswith("/") or p.startswith("\\"):
        return False
    if _DRIVE_LETTER.match(p):
        return False
    # Normalize backslashes so a Windows-style payload cannot sneak past.
    normalized = p.replace("\\", "/")
    segments = normalized.split("/")
    if any(seg == ".." for seg in segments):
        return False
    if any(not _is_windows_safe_path_segment(seg) for seg in segments):
        return False
    # Must live under wiki/ — the only tree the ingest pipeline writes to.
    if not normalized.startswith("wiki/"):
        return False
    return True


def parse_file_blocks(raw: str) -> ParseResult:
    """Parse an LLM stage-2 generation into FILE blocks (ingest.ts:454-547).

    Line-based, fence-aware state machine (NOT the naive lazy regex). Handles:
    CRLF normalization; tolerant / case-insensitive markers; an ``---END FILE---``
    inside a fenced code block (kept as body text, CommonMark fence pairing);
    unclosed blocks at EOF (dropped with a truncation warning); empty and unsafe
    paths (dropped with a warning). REVIEW blocks are NOT handled here — llm_wiki
    parses those separately over the same text (see :func:`parse_review_blocks`
    and :func:`parse_blocks`).
    """
    normalized = raw.replace("\r\n", "\n")
    lines = normalized.split("\n")

    blocks: list[FileBlock] = []
    warnings: list[str] = []

    i = 0
    total = len(lines)
    while i < total:
        opener = _OPENER_LINE.match(lines[i])
        if opener is None:
            i += 1
            continue
        path = opener.group(1).strip()
        i += 1  # consume opener

        content_lines: list[str] = []
        fence_marker: str | None = None  # '`' or '~' while inside a fence
        fence_len = 0
        closed = False

        while i < total:
            line = lines[i]

            # Update fence state before checking the closer. Only close the fence
            # when we see the same character repeated at least as many times
            # (CommonMark rule), so docs-about-our-format can quote the literal
            # marker inside code fences without truncating the outer block.
            fence_match = _FENCE_LINE.match(line)
            if fence_match is not None:
                run = fence_match.group(1)
                char = run[0]  # '`' or '~'
                length = len(run)
                if fence_marker is None:
                    fence_marker = char
                    fence_len = length
                elif char == fence_marker and length >= fence_len:
                    fence_marker = None
                    fence_len = 0
                content_lines.append(line)
                i += 1
                continue

            # A closer line only counts outside any code fence.
            if fence_marker is None and _CLOSER_LINE.match(line) is not None:
                closed = True
                i += 1
                break

            content_lines.append(line)
            i += 1

        if not closed:
            path_label = path or "(unnamed)"
            warnings.append(
                f'FILE block "{path_label}" was not closed before end of stream — '
                "likely truncation (model hit max_tokens, timeout, or connection "
                "dropped). Block dropped."
            )
            continue

        if not path:
            warnings.append(
                "FILE block with empty path skipped " "(LLM omitted the path after `---FILE:`)."
            )
            continue

        if not is_safe_ingest_path(path):
            warnings.append(
                f'FILE block with unsafe path "{path}" rejected (must be under '
                "wiki/, no .., no absolute paths, and Windows-safe file names)."
            )
            continue

        blocks.append(FileBlock(path=path, content="\n".join(content_lines)))

    return ParseResult(files=blocks, warnings=warnings)


def parse_review_blocks(raw: str) -> list[ReviewBlock]:
    """Parse ``---REVIEW: … ---END REVIEW---`` blocks (ingest.ts:1969-2030).

    ``type`` is coerced to one of contradiction / duplicate / missing-page /
    suggestion, else ``confirm``. ``OPTIONS:`` splits on ``|`` (default
    ``["Approve", "Skip"]`` when absent); ``PAGES:`` splits on ``,``; ``SEARCH:``
    splits on ``|`` (empties filtered). ``description`` is the body minus those
    three lines (each removed once — matching JS single-replace), trimmed.
    """
    items: list[ReviewBlock] = []
    for match in _REVIEW_BLOCK_REGEX.finditer(raw):
        raw_type = match.group(1).strip().lower()
        title = match.group(2).strip()
        body = match.group(3).strip()

        type_ = raw_type if raw_type in _REVIEW_KNOWN_TYPES else "confirm"

        options_match = _OPTIONS_LINE.search(body)
        if options_match is not None:
            options = [o.strip() for o in options_match.group(1).split("|")]
        else:
            options = ["Approve", "Skip"]

        pages_match = _PAGES_LINE.search(body)
        pages = (
            [p.strip() for p in pages_match.group(1).split(",")] if pages_match is not None else []
        )

        search_match = _SEARCH_LINE.search(body)
        search_queries = (
            [q.strip() for q in search_match.group(1).split("|") if q.strip()]
            if search_match is not None
            else []
        )

        description = body
        description = _OPTIONS_STRIP.sub("", description, count=1)
        description = _PAGES_STRIP.sub("", description, count=1)
        description = _SEARCH_STRIP.sub("", description, count=1)
        description = description.strip()

        items.append(
            ReviewBlock(
                type=type_,
                title=title,
                description=description,
                options=options,
                pages=pages,
                search_queries=search_queries,
            )
        )
    return items


def parse_lint_blocks(raw: str) -> list[LintBlock]:
    """Parse ``---LINT: type | severity | title--- … ---END LINT---`` (lint.ts:302).

    ``type`` is coerced to contradiction / stale / missing-page / suggestion, else
    ``suggestion``; ``severity`` to warning / info, else ``info``. ``detail`` is the
    trimmed block body.

    (llm_wiki's ``runSemanticLint`` stores every finding as ``type: "semantic"`` and
    folds the raw type + a stripped ``PAGES:`` line into the detail string, lint.ts:409-431.
    This LintBlock keeps ``type`` as its own coerced field — per the PR3 spec — and has
    no pages field, so ``detail`` is the whole trimmed body.)
    """
    blocks: list[LintBlock] = []
    for match in _LINT_BLOCK_REGEX.finditer(raw):
        raw_type = match.group(1).strip().lower()
        severity = match.group(2).strip().lower()
        title = match.group(3).strip()
        body = match.group(4).strip()

        type_ = raw_type if raw_type in _LINT_KNOWN_TYPES else "suggestion"
        sev = severity if severity in _LINT_KNOWN_SEVERITIES else "info"

        blocks.append(LintBlock(type=type_, severity=sev, title=title, detail=body))
    return blocks


def parse_blocks(raw: str) -> ParseResult:
    """Convenience: parse FILE blocks (+warnings) AND REVIEW blocks from one text.

    Mirrors llm_wiki's ``autoIngest``, which calls ``parseFileBlocks(generation)`` and
    ``parseReviewBlocks(generation)`` over the same raw model output (ingest.ts:1099,
    1262). LINT blocks are a separate surface (lint pipeline) and are not included.
    """
    file_result = parse_file_blocks(raw)
    return ParseResult(
        files=file_result.files,
        reviews=parse_review_blocks(raw),
        warnings=file_result.warnings,
    )


def first_char_is_file_opener(raw: str) -> bool:
    """Whether ``raw`` begins with the ``---FILE:`` opener token.

    Semantics note (read from the TS): llm_wiki v0.6.3 has NO code path that
    discards a whole generation for not starting with ``---FILE:``. The rule lives
    ONLY in the generation prompt — "The FIRST character of your response MUST be
    ``-``" and "If you start with anything other than ``---FILE:``, the entire
    response will be discarded" (ingest.ts:2257, 2265). The actual parser
    (:func:`parse_file_blocks`) is tolerant and simply skips any preamble prose.

    This helper reifies that prompt contract for Synapse's orchestrated ingest loop
    (which MAY choose to discard + retry a non-conforming generation). It is
    deliberately STRICT: it does NOT strip leading whitespace (the prompt requires
    the very first character to be ``-``). It is case-insensitive and tolerant of
    spaces after the dashes, matching the ``_OPENER_LINE`` prefix ``^---\\s*FILE:``.
    """
    return _FILE_OPENER_PREFIX.match(raw) is not None
