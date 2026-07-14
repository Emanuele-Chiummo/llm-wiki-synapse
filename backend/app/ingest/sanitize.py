"""Provider-neutral port of llm_wiki v0.6.3's ingest content sanitizer.

Ported 1:1 (behavior-preserving) from nashsu/llm_wiki v0.6.3
``src/lib/ingest-sanitize.ts``. Cleans an LLM-generated wiki page body before it
hits disk, rewriting four recurring malformed shapes into standard
``---\\n…\\n---\\n`` frontmatter form:

1. an outer ``` ```yaml `` / ``` ```md `` / ``` ```markdown `` / bare ``` ``` ``
   fence wrapping the whole document,
2. a stray leading ``frontmatter:`` key before the real ``---`` block,
3. a missing opening ``---`` when the model started "inside" the YAML block, and
4. inline wikilink lists (``key: [[a]], [[b]]``) inside the frontmatter block.

Each pattern is anchored at the very start of the document (or at top-level
frontmatter scope), so legitimate body content is left alone. Pure functions:
no I/O, no app imports (stdlib only). See ``docs/reference/
LLMWIKI-CORE-LOGIC-v0.6.3.md`` §1.5.2.
"""

from __future__ import annotations

import re

__all__ = [
    "add_missing_opening_frontmatter_fence",
    "repair_wikilink_lists_in_frontmatter",
    "sanitize_ingested_file_content",
    "strip_frontmatter_key_prefix",
    "strip_outer_code_fence",
]

_OUTER_FENCE_OPEN = re.compile(r"^[ \t]*```(?:yaml|md|markdown)?[ \t]*\r?\n")
_OUTER_FENCE_CLOSE = re.compile(r"\r?\n[ \t]*```[ \t]*\r?\n?\s*$")
_FRONTMATTER_KEY_PREFIX = re.compile(r"^[ \t]*frontmatter\s*:\s*\r?\n(?=[ \t]*---\s*\r?\n)")
_LEADING_FENCE = re.compile(r"^[ \t]*---\s*(\r?\n|$)")
_LINE_SPLIT = re.compile(r"\r?\n")
_FIRST_FM_KEY = re.compile(r"^(type|title|created|updated|tags|related|sources)\s*:", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s+")
_FRONTMATTER_BLOCK = re.compile(r"^---\s*\r?\n([\s\S]*?)\r?\n---\s*(\r?\n|$)")
# ``[\w-]`` is spelled out as ASCII (JS ``\w`` is ASCII-only) to stay 1:1.
_WIKILINK_LIST_LINE = re.compile(
    r"^(\s*[A-Za-z_][A-Za-z0-9_-]*\s*:\s*)" r"(\[\[[^\]]+\]\](?:\s*,\s*\[\[[^\]]+\]\])+)\s*$"
)


def sanitize_ingested_file_content(content: str) -> str:
    """Apply the four sanitizer rules in order (ingest-sanitize.ts:58-87)."""
    cleaned = content
    cleaned = strip_outer_code_fence(cleaned)
    cleaned = strip_frontmatter_key_prefix(cleaned)
    cleaned = add_missing_opening_frontmatter_fence(cleaned)
    cleaned = repair_wikilink_lists_in_frontmatter(cleaned)
    return cleaned


def strip_outer_code_fence(content: str) -> str:
    """Remove an outer code fence wrapping the whole document (rule 1).

    Only fires when the FIRST non-empty line is an opening fence AND the LAST
    non-empty line is a matching closing fence, so a page that legitimately ends
    with an unclosed fence (mid-stream truncation) is left untouched.
    """
    open_match = _OUTER_FENCE_OPEN.match(content)
    if open_match is None:
        return content
    after_open = content[open_match.end() :]
    close_match = _OUTER_FENCE_CLOSE.search(after_open)
    if close_match is None:
        return content
    return after_open[: close_match.start()]


def strip_frontmatter_key_prefix(content: str) -> str:
    """Strip a leading ``frontmatter:`` line followed by the real block (rule 2).

    Only acts when the next non-empty line is ``---``, so prose that mentions the
    word "frontmatter:" is unaffected.
    """
    m = _FRONTMATTER_KEY_PREFIX.match(content)
    if m is None:
        return content
    return content[m.end() :]


def add_missing_opening_frontmatter_fence(content: str) -> str:
    """Prepend ``---`` when the opening fence is missing but the closing one is present (rule 3).

    Fires only when the content starts with a frontmatter key and a ``---`` line
    appears within the next 30 lines before any ``#`` heading.
    """
    if _LEADING_FENCE.match(content) is not None:
        return content

    lines = _LINE_SPLIT.split(content)
    first_content_idx = next(
        (idx for idx, line in enumerate(lines) if len(line.strip()) > 0),
        -1,
    )
    if first_content_idx < 0:
        return content

    first = lines[first_content_idx].strip()
    if _FIRST_FM_KEY.match(first) is None:
        return content

    search_end = min(len(lines), first_content_idx + 30)
    for i in range(first_content_idx + 1, search_end):
        trimmed = lines[i].strip()
        if trimmed == "---":
            return "---\n" + "\n".join(lines[first_content_idx:])
        if _HEADING.match(trimmed) is not None:
            break

    return content


def repair_wikilink_lists_in_frontmatter(content: str) -> str:
    """Rewrite ``key: [[a]], [[b]]`` → ``key: ["[[a]]", "[[b]]"]`` inside the FM block (rule 4).

    Only the payload between the opening and closing ``---`` is touched; body
    wikilinks are left alone. Only the multi-item form is repaired (a lone
    ``key: [[a]]`` is legal YAML and untouched).
    """
    m = _FRONTMATTER_BLOCK.match(content)
    if m is None:
        return content

    repaired_payload = "\n".join(_repair_wikilink_line(line) for line in m.group(1).split("\n"))

    # Replace ONLY the payload between fences; preserve the original fence lines
    # and trailing-newline shape. ``m.start()`` is always 0 (the pattern is
    # anchored); ``+ 4`` skips the leading ``---\n`` — a latent llm_wiki quirk we
    # keep verbatim (it assumes an LF opener, not CRLF).
    start = m.start()
    return content[: start + 4] + repaired_payload + content[start + 4 + len(m.group(1)) :]


def _repair_wikilink_line(line: str) -> str:
    """Repair a single ``key: [[a]], [[b]]`` frontmatter line, else return it as-is."""
    lm = _WIKILINK_LIST_LINE.match(line)
    if lm is None:
        return line
    items = ", ".join(
        f'"{stripped}"' for stripped in (s.strip() for s in lm.group(2).split(",")) if stripped
    )
    return f"{lm.group(1)}[{items}]"
