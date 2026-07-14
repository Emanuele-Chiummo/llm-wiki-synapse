"""Provider-neutral port of llm_wiki v0.6.3's schema-driven page-type routing.

Ported 1:1 (behavior-preserving) from nashsu/llm_wiki v0.6.3
``src/lib/wiki-schema.ts`` (``parseWikiSchemaRouting`` / ``validateWikiPageRouting``
/ ``pageTypesSectionLines`` + helpers) and the base type/dir tables of
``src/lib/wiki-page-types.ts`` (``GENERATION_WIKI_TYPES`` + ``WIKI_TYPE_DIRS``).
Behaviour spec: ``docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md`` §5 (schema routing)
and §1.3 (page types).

Pure functions of their string inputs: no filesystem, no DB, no app imports
(stdlib only). The caller reads ``schema.md`` and caches the parsed routing by
``data_version``; this module never touches disk. UNWIRED — it is not yet imported
by the ingest orchestrator/writer (that lands in a later PR, the same additive
pattern as ``app/ingest/blocks.py`` and ``app/ingest/sanitize.py``).

Representation note (deliberate Synapse adaptation). ``wiki-schema.ts`` stores each
directory in its wiki-relative form (``"wiki/entities"``); Synapse stores the BARE
subdir segment (``"entities"``) to stay consistent with ``app/ingest/schemas.py``'s
``_TYPE_DIR`` map (``entity → "entities"`` …) and the ``vault.py`` bootstrap dirs.
The overview/root row ``"wiki/"`` normalises to ``""``. Error messages are rendered
back to the ``wiki/…`` form so they read the same as llm_wiki's
(``wiki-schema.ts:80`` / ``:87``).

Base-types auto-include decision (``wiki-schema.ts:28``): ``parseWikiSchemaRouting``
initialises ``typeDirs`` as an EMPTY map and appends ONLY rows found in the
``## Page Types`` table. Base types are therefore NOT auto-included when a table
omits them — ``parse_page_type_routing`` returns strictly what the table declares.
The per-type default lives in ``BASE_TYPE_DIRS`` and is applied lazily by
``subdir_for_type`` (a writer helper), never by the parser or the validator.
"""

from __future__ import annotations

import re

__all__ = [
    "BASE_TYPE_DIRS",
    "BASE_WIKI_TYPES",
    "parse_page_type_routing",
    "subdir_for_type",
    "validate_page_routing",
]

# The nine generation page types (``wiki-page-types.ts:1-11`` GENERATION_WIKI_TYPES),
# in source order. These are the types the generation prompt may emit; a project
# schema may additionally define custom types (people, technologies, …).
BASE_WIKI_TYPES: tuple[str, ...] = (
    "source",
    "entity",
    "concept",
    "comparison",
    "query",
    "synthesis",
    "thesis",
    "methodology",
    "finding",
)

# Default bare subdir for each base type (``wiki-page-types.ts:13-23`` WIKI_TYPE_DIRS,
# reversed to type→dir). Overlaps with Synapse's ``app/ingest/schemas.py`` ``_TYPE_DIR``
# for the original six (entity/concept/source/query/comparison/synthesis); the three
# research types (thesis/methodology/finding) come from llm_wiki. Note ``finding``
# pluralises to ``findings`` while ``thesis`` / ``methodology`` do not.
BASE_TYPE_DIRS: dict[str, str] = {
    "entity": "entities",
    "concept": "concepts",
    "source": "sources",
    "query": "queries",
    "comparison": "comparisons",
    "synthesis": "synthesis",
    "finding": "findings",
    "thesis": "thesis",
    "methodology": "methodology",
}

_WIKI_PREFIX = "wiki/"

# ── Parsing (wiki-schema.ts:27-64) ───────────────────────────────────────────

# A heading line: 1-6 ``#`` + space + title (+ optional ATX close) (wiki-schema.ts:50).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
# The Page Types heading title, case-insensitive (wiki-schema.ts:51 /^page\s+types$/i).
_PAGE_TYPES_TITLE_RE = re.compile(r"page\s+types", re.IGNORECASE)
# Any heading (hashes + space), used to detect the end of the section (wiki-schema.ts:59).
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+")
# Valid type cell (wiki-schema.ts:38 /^[a-z][a-z0-9_-]*$/i): an ASCII letter then
# letters/digits/``_``/``-``. Spelled out as ASCII-both-cases to stay 1:1 with JS,
# which does not fold Unicode inside a ``[a-z]`` range under the ``i`` flag.
_TYPE_CELL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _page_types_section_lines(schema_md: str) -> list[str]:
    """Return the raw lines under the ``## Page Types`` heading (wiki-schema.ts:47-64).

    Finds the first heading whose title is ``page types`` (case-insensitive, any
    level 1-6) and collects every following line until a heading of the same or a
    higher level (fewer-or-equal ``#``); deeper subheadings stay inside the section.
    Returns ``[]`` when no such heading exists (wiki-schema.ts:54).
    """
    lines = schema_md.split("\n")
    start = -1
    heading_level = 6
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if m and _PAGE_TYPES_TITLE_RE.fullmatch(m.group(2).strip()):
            start = i
            heading_level = len(m.group(1))
            break
    if start < 0:
        return []

    out: list[str] = []
    first = start + 1
    for line in lines[first:]:
        heading = _ANY_HEADING_RE.match(line.strip())
        if heading and len(heading.group(1)) <= heading_level:
            break
        out.append(line)
    return out


def _to_subdir(dir_cell: str) -> str:
    """Normalise a validated dir cell to its bare subdir (wiki-schema.ts:41 stripTrailingSlash).

    ``dir_cell`` is guaranteed to be ``"wiki"`` or ``"wiki/…"``. Trailing slashes are
    stripped and the leading ``wiki/`` removed: ``"wiki/entities/" → "entities"``,
    ``"wiki/" → ""`` (the wiki root, used by the overview row).
    """
    stripped = dir_cell.rstrip("/")
    if stripped == "wiki":
        return ""
    return stripped.removeprefix(_WIKI_PREFIX)


def parse_page_type_routing(schema_md: str) -> dict[str, str]:
    """Parse the ``## Page Types`` markdown table into ``{type: bare_subdir}``.

    Faithful port of ``parseWikiSchemaRouting`` (wiki-schema.ts:27-45):

    * only rows inside the ``## Page Types`` section are considered
      (``_page_types_section_lines``); tables under other headings are ignored;
    * a row must be a pipe-table row; cells are ``line.split("|")[1:-1]`` trimmed
      (wiki-schema.ts:31-34) — trim only, the type is **not** lowercased;
    * the first cell (type) must match ``[A-Za-z][A-Za-z0-9_-]*`` — this drops the
      ``| --- |`` separator and any header row whose first cell is not an identifier
      (wiki-schema.ts:38);
    * the second cell (dir) must be exactly ``wiki`` or start with ``wiki/``; other
      rows (including the ``| Type | Directory |`` header, whose dir cell is
      ``Directory``) are dropped (wiki-schema.ts:39);
    * later rows override earlier rows for the same type — **last-wins**, because the
      loop assigns ``typeDirs[type] = …`` in table order (wiki-schema.ts:41).

    Base types are NOT auto-included: the map starts empty and only declared rows are
    added (wiki-schema.ts:28). Returns a possibly-empty dict.
    """
    type_dirs: dict[str, str] = {}
    for raw_line in _page_types_section_lines(schema_md):
        if not raw_line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in raw_line.split("|")[1:-1]]
        if len(cells) < 2:
            continue
        type_cell, dir_cell = cells[0], cells[1]
        if not _TYPE_CELL_RE.fullmatch(type_cell):
            continue
        if dir_cell != "wiki" and not dir_cell.startswith(_WIKI_PREFIX):
            continue
        type_dirs[type_cell] = _to_subdir(dir_cell)
    return type_dirs


# ── Validation (wiki-schema.ts:66-113) ───────────────────────────────────────


def _normalize_rel_path(rel_path: str) -> str:
    """Backslash→slash, then strip leading slashes (wiki-schema.ts:105-107)."""
    return rel_path.replace("\\", "/").lstrip("/")


def _dirname(rel_path: str) -> str:
    """Everything before the last ``/``; ``"."`` when there is none (wiki-schema.ts:109-113)."""
    normalized = _normalize_rel_path(rel_path)
    idx = normalized.rfind("/")
    return normalized[:idx] if idx >= 0 else "."


def _dir_to_subdir(dirpath: str) -> str:
    """Reduce a page's directory to the bare subdir used as a routing value.

    Tolerant of the optional leading ``wiki/`` and of trailing slashes (the task's
    ``wiki/thesis/foo.md`` path yields ``"thesis"``, matching the bare values
    ``parse_page_type_routing`` stores). ``"wiki"`` → ``""`` (the wiki root); a
    directory-less ``"."`` is preserved so it matches no routing value.
    """
    d = dirpath.strip("/")
    if d == "wiki":
        return ""
    if d.startswith(_WIKI_PREFIX):
        return d.removeprefix(_WIKI_PREFIX)
    return d


def _infer_type_from_subdir(subdir: str, routing: dict[str, str]) -> str | None:
    """First type whose bare subdir equals ``subdir``, in order (wiki-schema.ts:94-103)."""
    for page_type, dir_ in routing.items():
        if dir_ == subdir:
            return page_type
    return None


def _display_dir(subdir: str) -> str:
    """Render a bare subdir back to llm_wiki's ``wiki/…`` message form (wiki-schema.ts:80/87)."""
    if subdir == "":
        return "wiki"
    if subdir == ".":
        return "."
    return _WIKI_PREFIX + subdir


def validate_page_routing(
    page_type: str, rel_path: str, routing: dict[str, str]
) -> tuple[bool, str | None]:
    """Validate a page's ``type`` against its wiki-relative path (wiki-schema.ts:66-92).

    Returns ``(True, None)`` when the page is consistent with ``routing`` and
    ``(False, reason)`` otherwise. Mirrors the two llm_wiki error conditions, checked
    in this order:

    1. the declared ``type`` is routed to a directory the page does not use — the
       ``type`` has an expected dir and the path's dir differs (wiki-schema.ts:77-82);
    2. the page's directory is routed (by the table) to a different ``type`` than the
       one declared — inferred via ``_infer_type_from_subdir`` (wiki-schema.ts:84-89).

    An empty/whitespace ``page_type`` is not enforced (``(True, None)``): llm_wiki
    skips pages whose frontmatter has no parseable ``type`` (wiki-schema.ts:73). The
    path is normalised and its directory reduced to a bare subdir, tolerating the
    ``wiki/`` prefix and trailing slashes.
    """
    if not page_type or not page_type.strip():
        return (True, None)

    actual = _dir_to_subdir(_dirname(rel_path))

    expected = routing.get(page_type)
    if expected is not None and actual != expected:
        return (
            False,
            f'Page type "{page_type}" must be under "{_display_dir(expected)}/". '
            f'Current directory: "{_display_dir(actual)}".',
        )

    inferred = _infer_type_from_subdir(actual, routing)
    if inferred is not None and inferred != page_type:
        return (
            False,
            f'Pages under "{_display_dir(actual)}/" must use type "{inferred}", '
            f'but found "{page_type}".',
        )

    return (True, None)


def subdir_for_type(page_type: str, routing: dict[str, str]) -> str:
    """Return the bare wiki subdir a writer should use for ``page_type``.

    Resolution order: the project ``routing`` (schema.md table) first, then the
    built-in ``BASE_TYPE_DIRS`` default, then — for a custom type the schema did not
    route — the type name itself. The final step mirrors llm_wiki, where a page under
    ``wiki/<custom>/…`` infers type ``<custom>`` and vice-versa (wiki-page-types.ts:34
    custom-dir fallback; §1.3 "custom dirs fall back to the dir name"). Never raises;
    not a filesystem operation.
    """
    if page_type in routing:
        return routing[page_type]
    if page_type in BASE_TYPE_DIRS:
        return BASE_TYPE_DIRS[page_type]
    return page_type
