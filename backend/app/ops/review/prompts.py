"""
F9 HITL Review Queue — prompt builders + lenient parsers (BE-ARCH-2 package split).

Pure functions only (no DB session, no provider call): builds the four bounded LLM prompts
used by ``propose.py`` (propose / sweep) and ``suggestions.py`` (purpose / schema drift), and
parses their JSON responses tolerantly (never raises — malformed output degrades to "no
proposal" / "keep pending", per the I7/Do-NOT contracts documented in the callers).

Also holds the small text-digest helpers shared by more than one prompt builder
(``_digest_written_pages``, ``_digest_frontmatter``, ``_read_bounded_page_excerpt``,
``_trim_source_excerpt``) and the vault-language directive helpers.

NOTE: the _llm.py helpers (``bounded_chat_collect``, ``clean_str``, ``clean_str_list``,
``coerce_int``, ``loads_json_lenient``, ``resolve_operation_provider``) were extracted in 1.9.0
to ``app/ops/_llm.py`` and are NOT duplicated here — this module only holds prompt/parse logic
that is private to the review queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.config import settings
from app.ingest.schemas import PageType
from app.ops._llm import clean_str, clean_str_list, loads_json_lenient

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis
    from app.models import Page, ReviewItem

# ── Accepted value sets (app-side enum-by-convention, no DB CHECK — ADR-0034 §3.1) ──
# R9-3 (v0.9): `purpose-suggestion` added. R9-4 (v0.9): `schema-suggestion` added. item_type is
# a free Text column (no DB CHECK constraint — ADR-0034 §3.1), so extending this app-side set is
# sufficient for BOTH; NO migration.
_VALID_ITEM_TYPES = frozenset(
    {
        "missing-page",
        "suggestion",
        "contradiction",
        "duplicate",
        "confirm",
        "purpose-suggestion",
        "schema-suggestion",
    }
)


# ── Proposal DTO (LLM call contract — ADR-0034 §4.3) ────────────────────────


@dataclass
class ProposalDTO:
    """
    Structured proposal returned by _llm_propose_reviews().

    Fields mirror the review_items columns (ADR-0034 §3.1).
    target_page_title: for contradiction/duplicate, the existing page title in conflict.
    """

    item_type: Literal["missing-page", "suggestion", "contradiction", "duplicate", "confirm"]
    proposed_title: str | None
    proposed_page_type: str | None  # entity|concept|query|synthesis|comparison|None
    rationale: str | None
    target_page_title: str | None = None  # resolved to page_id at enqueue time
    # ADR-0044 §4.1: contextual depth — both ride the SAME single proposal call (no extra call).
    referenced_page_titles: list[str] = field(default_factory=list)
    """Existing-vault titles this proposal is about (resolved → referenced_page_ids)."""
    search_queries: list[str] = field(default_factory=list)
    """≤ REVIEW_SEARCH_QUERIES_MAX web-search queries; search_queries[0] seeds Deep Research."""


# ── Shared digest / excerpt helpers ───────────────────────────────────────────


def _read_bounded_page_excerpt(path: Path, cap: int) -> str:
    """Read at most *cap* bytes from a page, preserving head+tail for large files."""
    if cap <= 0:
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size <= cap:
                return handle.read(cap).decode("utf-8", errors="replace").strip()
            head_bytes = cap * 2 // 3
            tail_bytes = cap - head_bytes
            head = handle.read(head_bytes).decode("utf-8", errors="replace").rstrip()
            handle.seek(max(0, size - tail_bytes))
            tail = handle.read(tail_bytes).decode("utf-8", errors="replace").lstrip()
        return f"{head}\n…[page trimmed]…\n{tail}".strip()
    except Exception:  # noqa: BLE001 — page excerpts are advisory review context
        return ""


def _digest_written_pages(written_pages: list[Page], *, max_pages: int = 20) -> str:
    """Bounded digest of only the pages written in the current ingest run (I1/I7)."""
    selected = written_pages[:max_pages]
    total_cap = max(
        0,
        int(getattr(settings, "review_propose_written_pages_chars", 6_000)),
    )
    per_page_cap = total_cap // len(selected) if selected and total_cap else 0
    lines: list[str] = []
    for page in selected:
        title = (page.title or "").strip() or "(untitled)"
        ptype = (page.page_type or "").strip() or "?"
        lines.append(f"- {title} [{ptype}]")
        file_path = (getattr(page, "file_path", "") or "").strip()
        if per_page_cap and file_path:
            excerpt = _read_bounded_page_excerpt(settings.vault_root / file_path, per_page_cap)
            if excerpt:
                lines.append(f"  Excerpt:\n{excerpt}")
    return "\n".join(lines) if lines else "(none)"


def _digest_frontmatter(written_pages: list[Page], *, max_pages: int = 20) -> str:
    """
    Compact frontmatter digest of the written pages for the schema-pattern prompt (R9-4).

    Unlike _digest_written_pages (title + type only), this surfaces the fields schema.md actually
    governs: `type`, `tags[]`, and whether `sources[]` is present. Bounded (max_pages); no full
    page content (I1). Used only by the schema check.
    """
    lines: list[str] = []
    for page in written_pages[:max_pages]:
        title = (page.title or "").strip() or "(untitled)"
        ptype = (page.page_type or "").strip() or "?"
        tags = getattr(page, "tags", None) or []
        tags_str = ", ".join(str(t) for t in tags[:10]) if tags else "(none)"
        has_sources = "yes" if (getattr(page, "sources", None) or []) else "no"
        lines.append(f"- {title} | type={ptype} | tags=[{tags_str}] | sources={has_sources}")
    return "\n".join(lines) if lines else "(none)"


def _review_lang_directive(lang: str) -> str:
    """
    Return a mandatory output-language block for review prompts, or "" when no language is known.

    Mirrors the generation directive (provider/_common.py:build_generate_prompt) so review items
    (proposed_title + rationale) come out in the VAULT language instead of defaulting to English —
    the review propose/sweep prompts were never language-aware, so on an Italian vault the reviews
    came out in English (v1.5.2 fix). JSON keys stay English; only human-facing text is localised.
    """
    lang = (lang or "").strip()
    if not lang:
        return ""
    return (
        "# MANDATORY OUTPUT LANGUAGE\n"
        f"Write every proposal's `proposed_title` and `rationale` in {lang} (ISO-639-1) — the "
        f"vault's language. Do NOT translate to English unless {lang!r} is 'en'. The JSON keys "
        "themselves stay in English.\n\n"
    )


def _resolve_review_language(analysis: Analysis | None = None) -> str:
    """Resolve the review output language: analysis.language → settings.overview_language."""
    lang = (getattr(analysis, "language", "") or "").strip() if analysis is not None else ""
    return lang or (getattr(settings, "overview_language", "") or "").strip()


def _trim_source_excerpt(text: str, cap: int) -> str:
    """
    Head+tail excerpt of a raw source, bounded to ``cap`` characters (llm_wiki trimLongText
    parity). Keeps the opening (scope / intro / assumptions usually live there) AND the closing
    (out-of-scope / exclusions / next-steps sections often land at the end), with an elision
    marker in between. Returns the whole text when it already fits. ``cap<=0`` → empty (disabled).
    """
    text = (text or "").strip()
    if cap <= 0 or not text:
        return ""
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    tail = cap - head
    return f"{text[:head].rstrip()}\n\n…[source trimmed]…\n\n{text[-tail:].lstrip()}"


# ── Proposal prompt (§4.3, propose.py::_llm_propose_reviews) ─────────────────


def _build_propose_instruction(
    *,
    analysis: Analysis | None,
    written_pages: list[Page],
    existing_titles: list[str],
    max_items: int,
    token_budget: int,
    source_text: str = "",
) -> str:
    """
    Build the single structured-proposal prompt (ADR-0034 §4.3 + llm_wiki
    buildReviewSuggestionPrompt parity).

    Asks for a JSON object {"proposals": [...]} of ≤ max_items items, each one of the five
    review types. The model is told to return ONLY JSON. token_budget is surfaced so the model
    keeps the output compact (the call is also wrapped in wait_for + capped on parse).

    llm_wiki parity: the RAW source text is included (bounded head+tail excerpt) alongside the
    analysis and written pages. Feeding the source content — not just the analysis — is what lets
    the model quote the document ("the doc excludes X as out-of-scope") and identify concrete
    in-scope/out-of-scope handoff gaps, yielding source-grounded suggestions with precise,
    descriptive titles rather than generic "missing from vault" slugs.
    """
    analysis_block = ""
    if analysis is not None:
        try:
            analysis_json = analysis.model_dump_json(indent=2)
        except Exception:  # noqa: BLE001
            analysis_json = "{}"
        analysis_block = f"# Ingest analysis\n{analysis_json}\n\n"

    pages_digest = _digest_written_pages(written_pages)
    titles_block = "\n".join(f"- {t}" for t in existing_titles[:200]) or "(none)"

    ref_max = int(getattr(settings, "review_referenced_pages_max", 8))
    query_max = int(getattr(settings, "review_search_queries_max", 3))
    source_cap = int(getattr(settings, "review_propose_source_chars", 6_000))
    source_excerpt = _trim_source_excerpt(source_text, source_cap)
    # Only emit the section (and its instruction) when we actually have source content.
    source_block = (
        f"# Source content (raw excerpt of the document just ingested)\n{source_excerpt}\n\n"
        if source_excerpt
        else ""
    )

    return (
        _review_lang_directive(_resolve_review_language(analysis))
        + "You are identifying high-value follow-up work for a self-organizing personal wiki. "
        "The wiki pages for this source have ALREADY been generated — your job is NOT to write "
        "pages, but to surface unresolved knowledge gaps a human should review or send to Deep "
        "Research.\n\n"
        "Propose ONLY genuinely useful, high-signal items. Prefer quality over quantity: a few "
        "sharp proposals beat many shallow ones, and proposing nothing is correct when the "
        "source is fully covered. Each type means:\n"
        "  - missing-page: an important entity/concept the source references but that still lacks "
        "its own page.\n"
        "  - suggestion: a research question, a source type to look for, a comparison, or an "
        "in-scope/out-of-scope handoff that would MATERIALLY improve the wiki. Ground it in the "
        "source: name the specific passage, exclusion, assumption, or boundary that motivates it "
        "(e.g. the document marks something out-of-scope but doesn't say how it connects to the "
        "in-scope work).\n"
        "  - contradiction: a conflict or tension between this source and existing pages that "
        "needs human judgment.\n"
        "  - duplicate: a page/name that likely already exists under a different name.\n"
        "  - confirm: a claim worth a human's explicit confirmation.\n\n"
        f"{source_block}"
        f"{analysis_block}"
        f"# Pages written this run\n{pages_digest}\n\n"
        f"# Existing vault page titles\n{titles_block}\n\n"
        'Return ONLY a JSON object with a single key "proposals" whose value is a list of at '
        f"most {max_items} objects. Each object has keys:\n"
        "  type: one of missing-page | suggestion | contradiction | duplicate | confirm\n"
        "  proposed_title: a PRECISE, DESCRIPTIVE page title in Title Case — a real title a "
        "reader would recognize (e.g. 'ELP Analysis and Downstream Workflow'), NOT a slug or a "
        "single keyword. Required for missing-page and suggestion.\n"
        "  proposed_page_type: one of entity | concept | query | synthesis | comparison "
        "(optional; NEVER 'source'). Use 'query' for a page that answers a research question or "
        "documents how workstreams/scopes connect; 'comparison' for a head-to-head.\n"
        "  rationale: 1-3 sentences that describe the gap AND why it matters. When it comes from "
        "the source, reference the specific passage/exclusion/assumption that motivates it.\n"
        "  target_page_title: string (REQUIRED for contradiction/duplicate — the existing "
        "page in conflict; otherwise omit or null)\n"
        f"  referenced_page_titles: list of up to {ref_max} EXISTING vault page titles (taken "
        "VERBATIM from the 'Existing vault page titles' list above) that this proposal is "
        "contextually about. Use ONLY titles from that list — never invent a title. Omit or [] "
        "if none apply.\n"
        f"  search_queries: list of up to {query_max} keyword-rich web-search queries (specific, "
        "suitable for a search engine — NOT titles or sentences) that would advance this item; "
        "the first seeds Deep Research. Required for suggestion and missing-page; omit or [] "
        "otherwise.\n\n"
        "Do NOT propose a page whose title already exists. Keep the output well under "
        f"{token_budget} tokens. Return no prose, only the JSON object."
    )


def _parse_proposals(raw: str) -> list[ProposalDTO]:
    """
    Parse the proposal JSON into ProposalDTO list. Tolerant of code fences / prose wrapping;
    silently drops malformed entries (degrade, never raise). Unknown types are dropped.
    """
    if not raw:
        return []
    obj = loads_json_lenient(raw)
    if obj is None:
        return []

    if isinstance(obj, dict):
        items_raw = obj.get("proposals", obj.get("items", []))
    elif isinstance(obj, list):
        items_raw = obj
    else:
        return []
    if not isinstance(items_raw, list):
        return []

    out: list[ProposalDTO] = []
    for entry in items_raw:
        if not isinstance(entry, dict):
            continue
        item_type = entry.get("type") or entry.get("item_type")
        if item_type not in _VALID_ITEM_TYPES:
            continue
        proposed_type = clean_str(entry.get("proposed_page_type"))
        # Provider JSON is untrusted boundary input. Only valid non-source user-content types
        # survive; invalid/source hints degrade to the deterministic cue heuristic.
        if proposed_type is not None:
            try:
                parsed_type = PageType(proposed_type)
                proposed_type = None if parsed_type is PageType.SOURCE else parsed_type.value
            except (ValueError, KeyError):
                proposed_type = None
        # ADR-0044 §4.1: tolerant extraction of the two new per-proposal lists (drop non-strings;
        # cap lengths). These ride the SAME single call — no extra provider round-trip.
        ref_max = int(getattr(settings, "review_referenced_pages_max", 8))
        query_max = int(getattr(settings, "review_search_queries_max", 3))
        referenced = clean_str_list(
            entry.get("referenced_page_titles") or entry.get("referenced_pages"),
            cap=ref_max,
        )
        queries = clean_str_list(entry.get("search_queries"), cap=query_max)
        out.append(
            ProposalDTO(
                item_type=item_type,
                proposed_title=clean_str(entry.get("proposed_title")),
                proposed_page_type=proposed_type,
                rationale=clean_str(entry.get("rationale")),
                target_page_title=clean_str(entry.get("target_page_title")),
                referenced_page_titles=referenced,
                search_queries=queries,
            )
        )
    return out


# ── Sweep prompt (§6.3, propose.py::_llm_sweep_judge) ─────────────────────────


def _build_sweep_instruction(
    *,
    judgeable: list[ReviewItem],
    existing_titles: list[str | None],
    token_budget: int,
) -> str:
    """
    Build the single conservative default-to-keep sweep prompt (ADR-0034 §6.3).

    Lists each candidate item by id + type + title + rationale; asks the model to return the
    ids it is CONFIDENT can be resolved. The default is to keep; ambiguity → keep.
    """
    item_lines: list[str] = []
    for it in judgeable:
        item_lines.append(
            f"- id={it.id} type={it.item_type} "
            f"title={(it.proposed_title or '')!r} "
            f"rationale={(it.rationale or '')!r}"
        )
    items_block = "\n".join(item_lines) or "(none)"
    titles_block = "\n".join(f"- {t}" for t in existing_titles[:200] if t) or "(none)"

    return (
        "You are the conservative auto-resolution judge of a wiki review queue.\n"
        "For each review item below, decide whether the concern NO LONGER APPLIES given the "
        "current vault. BE CONSERVATIVE: only resolve an item if you are CONFIDENT the concern "
        "is already satisfied (e.g. the page now exists, the duplicate is gone, the gap is "
        "filled). When in doubt, KEEP it pending.\n\n"
        f"# Current vault page titles\n{titles_block}\n\n"
        f"# Review items to judge\n{items_block}\n\n"
        'Return ONLY a JSON object with a single key "resolve" whose value is the list of item '
        "id strings you are confident can be resolved. Resolve NOTHING you are unsure about. "
        f"Keep the output well under {token_budget} tokens. Return no prose, only the JSON object."
    )


def _parse_sweep_verdicts(raw: str, by_id: dict[str, ReviewItem]) -> set[str]:
    """
    Parse the sweep verdict JSON into a set of ids to resolve. Any ambiguity / parse failure /
    unrecognized shape → empty set (default-to-keep, Do-NOT #7). Only ids present in *by_id*
    (i.e. the items we actually asked about) are accepted.
    """
    if not raw:
        return set()
    obj = loads_json_lenient(raw)
    if obj is None:
        return set()

    if isinstance(obj, dict):
        ids_raw = obj.get("resolve", obj.get("resolve_ids", []))
    elif isinstance(obj, list):
        ids_raw = obj
    else:
        return set()
    if not isinstance(ids_raw, list):
        return set()

    return {str(x) for x in ids_raw if str(x) in by_id}


# ── purpose.md drift prompt (R9-3, suggestions.py::generate_purpose_suggestion) ──


def _build_purpose_drift_instruction(
    *,
    analysis: Analysis | None,
    written_pages: list[Page],
    purpose_text: str,
    max_tokens: int,
) -> str:
    """
    Build the single bounded scope-drift prompt (R9-3). Asks the model to judge whether the newly
    ingested content is within the vault's stated purpose/scope; if NOT, to name the recurring
    theme and propose a short markdown section to add to purpose.md. Model returns ONLY JSON.
    """
    topics: list[str] = []
    summary = ""
    if analysis is not None:
        topics = list(getattr(analysis, "topics", []) or [])
        summary = (getattr(analysis, "summary", None) or "").strip()
    topics_block = ", ".join(topics[:20]) or "(none)"
    pages_digest = _digest_written_pages(written_pages)
    purpose_block = purpose_text.strip() or "(purpose.md is empty or missing)"

    return (
        "You maintain the purpose.md of a self-organizing wiki. purpose.md declares the vault's "
        "goal, scope, key questions, and thesis. Given the vault's current purpose.md and the "
        "topics/summary of newly ingested content, judge whether the new content represents a "
        "RECURRING THEME that is NOT already covered by the stated purpose (scope drift).\n\n"
        "Be conservative: if the new content clearly fits the existing scope, report in-scope.\n\n"
        f"# Current purpose.md\n{purpose_block}\n\n"
        f"# Newly ingested topics\n{topics_block}\n\n"
        f"# Newly ingested summary\n{summary or '(none)'}\n\n"
        f"# Pages written this run\n{pages_digest}\n\n"
        'Return ONLY a JSON object. If the content is within scope, return {"in_scope": true}. '
        'If there IS scope drift, return {"in_scope": false, "theme": "<short theme name, ≤6 '
        'words>", "why": "<one sentence: why this is outside current scope>", "addition": '
        '"<a short markdown section (heading + 1-3 sentences) to append to purpose.md that '
        'widens the scope to cover this theme>"}.\n'
        f"Keep the output well under {max_tokens} tokens. Return no prose, only the JSON object."
    )


def _parse_purpose_drift(raw: str) -> tuple[str, str, str] | None:
    """
    Parse the drift JSON. Returns (theme, why, addition) on a valid drift verdict, else None
    (in-scope, empty, or unparseable — degrade-safe, never raises).
    """
    if not raw:
        return None
    obj = loads_json_lenient(raw)
    if not isinstance(obj, dict):
        return None
    # Explicit in-scope, or missing drift fields → no suggestion.
    if obj.get("in_scope") is True:
        return None
    theme = clean_str(obj.get("theme"))
    addition = clean_str(obj.get("addition"))
    if not theme or not addition:
        return None
    why = clean_str(obj.get("why")) or f"New recurring theme not covered by purpose: {theme}."
    return theme, why, addition


# ── schema.md co-evolution prompt (R9-4, suggestions.py::generate_schema_suggestion) ──


def _build_schema_pattern_instruction(
    *,
    written_pages: list[Page],
    schema_text: str,
    max_tokens: int,
) -> str:
    """
    Build the single bounded schema co-evolution prompt (R9-4). Asks the model to compare the
    ACTUAL frontmatter/type/tag usage of the newly ingested pages against the vault's schema.md
    rules; if a recurring convention is not yet codified, name it and propose the exact markdown
    rule block to add to schema.md. Model returns ONLY JSON.
    """
    frontmatter_digest = _digest_frontmatter(written_pages)
    schema_block = schema_text.strip() or "(schema.md is empty or missing)"

    return (
        "You maintain the schema.md of a self-organizing wiki. schema.md is the FORMAL contract "
        "for page frontmatter: required fields, allowed `type` values, tag conventions, and "
        "wikilink style. Given the current schema.md and the ACTUAL frontmatter (type, tags, and "
        "which fields are present) of a batch of newly ingested pages, judge whether the pages "
        "reveal a RECURRING convention that is NOT yet codified in schema.md — for example: a tag "
        "family used consistently, a frontmatter field present on most pages but not required by "
        "schema.md, or a `type` value that is over/under-used in a way the rules do not describe.\n"
        "\n"
        "Be conservative: propose a change ONLY for a genuine, recurring, useful convention. If "
        "the pages already conform to schema.md, or the pattern is incidental / one-off, report "
        "no change. Do NOT invent conventions the pages do not actually exhibit.\n\n"
        f"# Current schema.md\n{schema_block}\n\n"
        f"# Frontmatter of pages written this run\n{frontmatter_digest}\n\n"
        "Return ONLY a JSON object. If no schema change is warranted, return "
        '{"needs_change": false}. If a new convention SHOULD be codified, return '
        '{"needs_change": true, "convention": "<short name of the convention, ≤6 words>", '
        '"why": "<one sentence: what recurring pattern you observed and why schema.md should '
        'capture it>", "addition": "<a short markdown section (heading + the exact rule text) to '
        'APPEND to schema.md codifying this convention>"}.\n'
        f"Keep the output well under {max_tokens} tokens. Return no prose, only the JSON object."
    )


def _parse_schema_pattern(raw: str) -> tuple[str, str, str] | None:
    """
    Parse the schema-pattern JSON. Returns (convention, why, addition) on a valid change verdict,
    else None (no change, empty, or unparseable — degrade-safe, never raises).
    """
    if not raw:
        return None
    obj = loads_json_lenient(raw)
    if not isinstance(obj, dict):
        return None
    # Explicit no-change, or missing change fields → no suggestion.
    if obj.get("needs_change") is False:
        return None
    convention = clean_str(obj.get("convention"))
    addition = clean_str(obj.get("addition"))
    if not convention or not addition:
        return None
    why = (
        clean_str(obj.get("why"))
        or f"Recurring frontmatter convention not in schema: {convention}."
    )
    return convention, why, addition
