"""
F5 — 4-phase retrieval pipeline (ADR-0022, M5 Phase 1).

`retrieve()` is a single bounded pass (I7) that makes ZERO inference calls and ZERO
``vault/`` walks (I1). It reads three stores only — Qdrant (bge-m3 dense vectors via the
existing wrapper, I9), Postgres (``pages`` / ``links`` / ``edges`` tables, I2), and the
targeted source-file body for each surfaced candidate — and assembles a context string +
``[n]`` citation map server-side (I3).

The four phases, in order (ADR-0022 §2.2):

  1. Vector search   — embed query via ``get_embedding_client()`` (bge-m3), dense top-k over
                       the existing ``synapse_pages`` collection. Point id == ``pages.id``
                       (ADR-0002). Score = cosine similarity. Dense-only (AQ-v0.5-1).
                       **When ``settings.embeddings_enabled`` is False** (ADR-0030, Feature B),
                       Phase 1 is replaced by ``_phase1_lexical_search`` — a k-bounded
                       Postgres keyword/title ILIKE search. Phases 2–4 are UNCHANGED.
  2. Graph-expansion — BFS over the ``edges`` table (the F4 4-signal output) from the seed
                       pages, ``expansion_depth`` ≤ 2 (HARD cap), ordered by edge ``weight``
                       DESC; also follows resolved ``links.target_page_id``. Reads ``edges``
                       directly — never calls GraphEngine / FA2 (I2). ``data_version`` is
                       unchanged across the call (AC-F5-5).
  3. Token-budget    — ``budget_tokens = int(context_window * 0.20)`` (the F14 "retrieved"
                       slice of 60/20/5/15); ``budget_chars = budget_tokens * 4`` (char/4,
                       AQ-v0.5-2 — same ``_CHARS_PER_TOKEN`` convention as ``chat/``).
  4. Assembly        — walk candidates in rank order (vector seeds by cosine, then expansions
                       by edge weight) while budget remains; load each source-file body
                       (per-passage capped), assign the next 1-based contiguous ``n``, append
                       ``[n] <title>\\n<passage>`` to ``text`` and record the matching
                       ``Citation``. Lowest-ranked candidates that do not fit are DROPPED
                       (never mid-sentence truncate-without-drop, AC-F5-4). The assembler is
                       the single authority: ``len(citations) == count of distinct [n]``.

Bounding (I7): ``k`` (default 8), ``expansion_depth`` (default 2, hard max 2), and the char
budget are all explicit caps. The BFS is depth-bounded, not open-ended. No phase loops on a
provider; there is no inference call in ``retrieve()`` at all.

ADR-0030 lexical degrade (Feature B):
When ``EMBEDDINGS_ENABLED=false``, ``_phase1_lexical_search`` replaces the Qdrant vector
call. It runs a Postgres title ``ILIKE``-based prefilter capped to ``k`` rows — never loads
every page body (I7). Phase ``"vector"`` labels are preserved on lexical candidates so the
caller receives a structurally identical ``RetrievalContext`` (shared contract). Neither
the embedding client nor Qdrant is contacted when embeddings are disabled (ADR-0030 §2.3).

R7-8 — wiki-only retrieval scope (AC-R7-8-1, ADR-0049):
The citation/candidate assembly phase (``_load_page_meta``) and the lexical Phase-1 fallback
(``_phase1_lexical_search``) both apply the filter ``file_path NOT LIKE 'raw/%'`` to exclude
raw source documents from the assembled chat context. Raw documents are source material for
the ingest pipeline; they are not citable wiki knowledge pages. Only ``wiki/`` pages are
surfaced in citations returned to the caller. The vector Phase-1 path (Qdrant) may return
raw/ point ids — these are silently dropped by the ``_load_page_meta`` filter in Phase 4.
This filter is portable SQL: ``LIKE 'raw/%'`` works identically on Postgres (production) and
SQLite (tests). See ``docs/adr/0049-retrieval-wiki-only-scope.md`` for the full rationale.

R8-5 — type filter + date sort (AC-R8-5-1, AC-R8-5-2, F5):
``retrieve()`` accepts two optional presentation-layer parameters:

  ``type_filter``  — a list of YAML frontmatter ``type`` values (e.g. ``["entity", "concept"]``).
                     When non-empty the filter is applied at BOTH Phase 1 SQL (defense in
                     depth) and Phase 4 ``_load_page_meta`` SQL (authoritative gate, mirrors
                     the ADR-0049 raw/ pattern).  The Qdrant Phase-1 path may still return
                     candidates of any type; they are silently dropped by the Phase-4 filter.
                     SQL form: ``AND CAST(type AS TEXT) IN (:t0, :t1, …)`` — CAST is portable
                     across Postgres (production) and SQLite (tests).

  ``sort``         — ``"relevance"`` (default, ranking unchanged) | ``"date_desc"`` |
                     ``"date_asc"``.  Applied AFTER the 4-phase pipeline completes: the
                     assembled ``Citation`` list is re-ordered by ``pages.updated_at`` fetched
                     in a single bounded read.  Phase internals (budgets, BFS depth) are
                     NEVER changed by the sort (I7 untouched).  The context text ``[n]``
                     markers are re-numbered to match the new order.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from qdrant_client.http import models as qmodels
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.config_overrides import effective_bool
from app.db import get_session, is_postgres_session
from app.embeddings import get_embedding_client
from app.qdrant_client import get_qdrant_client

# R8-5: valid page types (AC-R8-5-1, schema: YAML frontmatter 'type' field values).
# Matches frontend PageTypeFilter enum in searchClient.ts.
VALID_PAGE_TYPES: frozenset[str] = frozenset(
    {"entity", "concept", "source", "synthesis", "comparison", "query"}
)

# R8-5: sort options (AC-R8-5-1).
SearchSortOption = Literal["relevance", "date_desc", "date_asc"]

logger = logging.getLogger(__name__)

# ── Budget convention (AQ-v0.5-2 — mirror chat/context.py, no tokenizer) ────────
# char/4 SAFETY cap, not exact accounting. Under-fill is the safe direction (I7).
_CHARS_PER_TOKEN = 4

# F14 "retrieved" slice of the 60/20/5/15 budget (ADR-0022 §2.2 phase 3).
_RETRIEVAL_BUDGET_FRACTION = 0.20

# Graph-expansion is BFS-bounded to depth 2 (ADR-0022 §2.2 phase 2 — HARD cap, I7).
_MAX_EXPANSION_DEPTH = 2

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# CG-A6 (ADR-0067 P3-3): exclude type=query lint-stub pages (tags contain BOTH 'stub' and
# 'lint') from chat citations so a near-empty ghost stub can never resolve an [n] footnote.
# Portable + NULL-safe:
#   - CAST(col AS TEXT) works on Postgres JSONB and SQLite JSON-stored-as-text (mirrors the
#     project's "portable CAST(col AS TEXT)" rule + the tags-as-JSON-string note in graph/engine).
#   - COALESCE(...,'') so a genuine query page with NULL tags is NOT mistaken for a stub (kept):
#     without it `type='query' AND NULL AND NULL` → NULL → `NOT NULL` → the row is wrongly dropped.
# The 'stub'/'lint' literals are constants (no user input), so there is no injection surface.
_STUB_EXCLUSION_SQL = (
    "AND NOT ("
    "CAST(type AS TEXT) = 'query' "
    "AND COALESCE(CAST(tags AS TEXT), '') LIKE '%stub%' "
    "AND COALESCE(CAST(tags AS TEXT), '') LIKE '%lint%'"
    ") "
)


def _display_path(file_path: str) -> str:
    """
    CG-A1 (ADR-0067 P3-3): a compact, human/LLM-friendly page path for citation context blocks.

    ``wiki/concepts/bge-m3.md`` → ``concepts/bge-m3``. Strips the ``wiki/`` root and the ``.md``
    suffix so the model can name the relevant page path naturally in prose (LLM Wiki
    backend-agent style) while the bare ``[n]`` marker stays the machine-resolvable anchor.
    """
    p = file_path
    if p.startswith("wiki/"):
        p = p[len("wiki/") :]
    if p.endswith(".md"):
        p = p[:-3]
    return p


def _strip_path_suffix(header_line: str) -> str:
    """
    Recover the bare title from a ``<title> (<path>)`` citation header (CG-A1).

    Drops a single trailing `` (…)`` parenthetical — the display path appended by the assembler
    (:func:`_phase4_assemble`) — so the header matches ``Citation.ref.title`` for the date-sort
    rebuild lookup. Titles that themselves contain parentheses (e.g. ``Amazon Web Services
    (AWS)``) keep them: only the LAST parenthetical (the appended path) is removed.
    """
    s = header_line.rstrip()
    if s.endswith(")"):
        idx = s.rfind(" (")
        if idx > 0:
            return s[:idx]
    return s


# ── B2-C3: Retrieval-mode presets (frozen — NEVER allow arbitrary values) ───────
# Keyed by mode name; values are (k, expansion_depth).
# expansion_depth is ALWAYS clamped to ≤ _MAX_EXPANSION_DEPTH (2) on use — the preset
# table must already satisfy this invariant (I7: hard cap is in retrieve() itself too).

RetrievalMode = Literal["fast", "standard", "deep", "local_first"]

_RETRIEVAL_MODE_PRESETS: dict[str, tuple[int, int]] = {
    "fast": (4, 0),
    "standard": (8, 2),  # ← current retrieve() defaults (k=8, expansion_depth=2)
    "deep": (12, 2),  # ← hard cap on expansion_depth = 2 (I7/I2)
    "local_first": (8, 2),  # ← identical to standard; web-gating handled in chat path
}


def retrieval_mode_params(mode: str) -> tuple[int, int]:
    """
    Return (k, expansion_depth) for the given retrieval mode (B2-C3, frozen).

    Falls back to ``"standard"`` for unknown values (defensive). expansion_depth is
    additionally clamped to ≤ ``_MAX_EXPANSION_DEPTH`` (2) as the absolute hard cap (I7).
    """
    k, depth = _RETRIEVAL_MODE_PRESETS.get(mode, _RETRIEVAL_MODE_PRESETS["standard"])
    return k, min(depth, _MAX_EXPANSION_DEPTH)


# ── Data structures (the contract — ADR-0022 §2.3, frozen) ──────────────────────


class PageRef(BaseModel):
    """A citable source-page target. Resolves ``[n]`` → a navigable ``pages`` row."""

    id: str
    """str(uuid) of the pages row (== Qdrant point id, ADR-0002)."""

    title: str
    """Frontmatter title, else filename stem (never empty — falls back, §2.6)."""

    slug: str
    """slugify(title or file_stem) — derived in code, NOT a DB column (§2.6)."""


class Citation(BaseModel):
    """One ``[n]`` marker → its source page + the matched candidate's score."""

    n: int
    """1-based citation index, contiguous from 1."""

    ref: PageRef

    score: float
    """Vector cosine (phase 'vector') OR edge weight (phase 'expansion')."""

    phase: Literal["vector", "expansion"]
    """Which phase surfaced the candidate (audit/debug)."""


class RetrievalContext(BaseModel):
    """The assembled, budget-capped retrieval result — the ONE object F5 returns."""

    query: str

    text: str
    """Assembled context string WITH inline ``[n]`` markers (≤ budget)."""

    citations: list[Citation]
    """``n`` → ``PageRef`` map; ``len == count of distinct [n] in text`` (single authority)."""

    token_budget: int
    """The 20% slice used (for audit)."""

    approx_tokens: int
    """char/4 estimate of ``text`` (≤ ``token_budget``)."""

    data_version: int
    """Snapshot read from ``vault_state`` BEFORE assembly (AC-F5-5 read-only proof)."""


# ── Internal candidate record ───────────────────────────────────────────────────


class _Candidate:
    """A page surfaced by phase 1 or 2, with its provenance score + phase."""

    __slots__ = ("page_id", "score", "phase")

    def __init__(self, page_id: str, score: float, phase: Literal["vector", "expansion"]) -> None:
        self.page_id = page_id
        self.score = score
        self.phase = phase


# ── Public interface (ADR-0022 §2.1 — FROZEN) ──────────────────────────────────


async def retrieve(
    query: str,
    *,
    vault_id: str,
    context_window: int,
    k: int = 8,
    expansion_depth: int = 2,
    session: AsyncSession | None = None,
    type_filter: list[str] | None = None,
    sort: SearchSortOption = "relevance",
    exclude_stub_pages: bool = False,
) -> RetrievalContext:
    """
    Run the 4-phase retrieval pipeline and return a :class:`RetrievalContext`.

    Single bounded pass (I7), zero inference calls, zero ``vault/`` walk (I1). See the
    module docstring for the phase contract.

    Args:
        query: The user query to ground.
        vault_id: Logical vault scope (matches ``pages`` / ``edges`` / ``vault_state``).
        context_window: Configured context window; the retrieval slice is 20% of it (F14).
        k: Dense top-k for the vector phase (default 8, AC-F5-1).
        expansion_depth: BFS depth for graph-expansion; clamped to ``_MAX_EXPANSION_DEPTH``
            (2) — a HARD cap (I2/I7).
        session: Optional AsyncSession; a new one is opened per-phase when omitted (the
            graph engine's read style).
        type_filter: R8-5 — optional list of YAML ``type`` values to restrict results to
            (AC-R8-5-1/2).  Applied at both Phase 1 SQL and Phase 4 assembly (defense in
            depth, mirrors ADR-0049 raw/ pattern). ``None`` / empty → no type filter.
            Values must already be validated by the caller (422 raised upstream).
        sort: R8-5 — presentation-level sort applied AFTER the 4-phase pipeline
            (AC-R8-5-2). ``"relevance"`` (default) leaves ranking unchanged.
            ``"date_desc"`` / ``"date_asc"`` re-orders the final citation list by
            ``pages.updated_at``; phase internals and budgets are NEVER changed (I7).
        exclude_stub_pages: CG-A6 (ADR-0067 P3-3) — when True, ``type=query`` lint-stub
            pages (``tags`` contain both ``stub`` and ``lint``) are dropped at BOTH Phase 1
            lexical SQL and Phase 4 assembly (authoritative gate), so a near-empty ghost stub
            can never resolve an ``[n]`` citation. Default False (search endpoints unchanged);
            the chat retrieval path passes True (setting-gated, default on). Genuine query
            pages (real open questions, no stub/lint tags) are unaffected.

    Returns:
        A :class:`RetrievalContext` whose ``citations`` count equals the distinct ``[n]``
        count in ``text``.
    """
    depth = max(0, min(expansion_depth, _MAX_EXPANSION_DEPTH))
    effective_type_filter: list[str] = type_filter if type_filter else []

    # Snapshot data_version BEFORE any read so AC-F5-5 can prove retrieval is read-only.
    data_version = await _read_data_version(vault_id, session)

    budget_tokens = int(max(context_window, 1) * _RETRIEVAL_BUDGET_FRACTION)
    budget_chars = budget_tokens * _CHARS_PER_TOKEN

    # ── Phase 1: vector search OR lexical degrade (ADR-0030, Feature B) ────────
    # When embeddings are enabled (default): dense top-k via Qdrant + bge-m3 (I9).
    # When disabled: bounded Postgres keyword/title search — no embedding client or
    # Qdrant call is made (ADR-0030 §2.3, §2.5). Phases 2–4 are UNCHANGED either way.
    # R8-5: type_filter passed to lexical phase for SQL-level filtering (AC-R8-5-2).
    # Vector phase (Qdrant) cannot filter by type at the collection level without a
    # payload filter — type filtering is applied at Phase 4 assembly (defense-in-depth).
    if effective_bool("embeddings_enabled", settings.embeddings_enabled):
        vector_candidates = await _phase1_vector_search(query, vault_id=vault_id, k=k)
    else:
        vector_candidates = await _phase1_lexical_search(
            query,
            vault_id=vault_id,
            k=k,
            session=session,
            type_filter=effective_type_filter,
            exclude_stub_pages=exclude_stub_pages,
        )

    # ── Phase 2: graph-expansion (I2 — read edges + resolved links, NOT FA2) ──
    seed_ids = [c.page_id for c in vector_candidates]
    expansion_candidates = await _phase2_graph_expansion(
        seed_ids, vault_id=vault_id, depth=depth, session=session
    )

    # ── Phase 3: token-budget allocation (F14 — rank: seeds by cosine, then
    #            expansions by edge weight) ──────────────────────────────────────
    ranked = _phase3_rank(vector_candidates, expansion_candidates)

    # ── Phase 4: context assembly (I3 — build string + citation map ONCE) ─────
    # R8-5: type_filter applied here as the authoritative gate (defense-in-depth,
    # mirrors the raw/ exclusion pattern from ADR-0049, AC-R8-5-2).
    text, citations = await _phase4_assemble(
        ranked,
        vault_id=vault_id,
        budget_chars=budget_chars,
        session=session,
        type_filter=effective_type_filter,
        exclude_stub_pages=exclude_stub_pages,
    )

    # ── R8-5: sort — presentation-level, applied after pipeline (AC-R8-5-2) ──
    # Phase internals (budgets, BFS) are NEVER changed (I7 untouched).
    if sort in ("date_desc", "date_asc") and citations:
        citations = await _sort_citations_by_date(citations, sort=sort, session=session)
        # Re-number [n] markers to match the new order and rebuild text.
        text = _rebuild_text_from_citations(citations, text)

    approx_tokens = len(text) // _CHARS_PER_TOKEN

    phase1_mode = (
        "vector" if effective_bool("embeddings_enabled", settings.embeddings_enabled) else "lexical"
    )
    logger.info(
        "retrieve: vault=%r query_len=%d phase1=%s phase1_hits=%d expansions=%d "
        "citations=%d approx_tokens=%d/%d data_version=%d type_filter=%r sort=%r",
        vault_id,
        len(query),
        phase1_mode,
        len(vector_candidates),
        len(expansion_candidates),
        len(citations),
        approx_tokens,
        budget_tokens,
        data_version,
        effective_type_filter or None,
        sort,
    )

    return RetrievalContext(
        query=query,
        text=text,
        citations=citations,
        token_budget=budget_tokens,
        approx_tokens=approx_tokens,
        data_version=data_version,
    )


# ── Phase 1 — vector search ─────────────────────────────────────────────────────

# BE-PERF-3: log the unscoped-fallback warning at most once per process (avoid log spam on
# every chat/search call while the backfill script has not yet been run).
_legacy_vault_id_fallback_warned: bool = False


async def _phase1_vector_search(query: str, *, vault_id: str, k: int) -> list[_Candidate]:
    """
    Embed *query* via the existing bge-m3 wrapper and run a dense top-k cosine search over
    the existing ``synapse_pages`` collection (I9 — no new service, no new collection).

    Point ids are ``pages.id`` (ADR-0002). Score = cosine similarity. Dense-only (AQ-v0.5-1).

    BE-PERF-3: the query is scoped to *vault_id* via a Qdrant payload ``Filter`` so a
    multi-vault deployment's dense top-k is never diluted — or leaked into citations — by
    points belonging to another vault (mirrors the vault-scoping pattern already applied to
    Postgres reads elsewhere in this module, e.g. ``_phase1_lexical_search``, ``_load_page_meta``).

    Backward-compat fallback: points written before ``vault_id`` was added to the Qdrant
    payload (``app.qdrant_client.upsert_point``) have no ``vault_id`` field and will never
    match the filter. Until ``backend/scripts/backfill_qdrant_vault_id.py`` has been run, an
    UNFILTERED retry is attempted — but ONLY when the filtered search comes back empty AND
    *vault_id* is the instance's configured default vault (``settings.vault_id``), i.e. the
    single legacy vault that existed before multi-vault support. This never leaks OTHER
    vaults' points into a non-default vault's search, and a warning is logged once per
    process so the operator knows to run the backfill.
    """
    if k <= 0:
        return []

    vector = await get_embedding_client().embed(query)
    client = get_qdrant_client()
    vault_filter = qmodels.Filter(
        must=[qmodels.FieldCondition(key="vault_id", match=qmodels.MatchValue(value=vault_id))]
    )

    # query_points is the current (non-deprecated) dense top-k API; returns QueryResponse
    # whose .points are ScoredPoint(id, score, payload, ...). Replaces the removed .search().
    response = await client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        query_filter=vault_filter,
        limit=k,
        with_payload=True,
    )

    if not response.points and vault_id == settings.vault_id:
        global _legacy_vault_id_fallback_warned  # noqa: PLW0603
        if not _legacy_vault_id_fallback_warned:
            logger.warning(
                "retrieval: vault-scoped vector search returned 0 hits for the default "
                "vault (vault_id=%r) — falling back to an UNFILTERED query_points call to "
                "cover legacy Qdrant points written before vault_id was added to the payload "
                "(BE-PERF-3). Run backend/scripts/backfill_qdrant_vault_id.py to backfill "
                "vault_id on existing points and remove this fallback path's relevance.",
                vault_id,
            )
            _legacy_vault_id_fallback_warned = True
        response = await client.query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            limit=k,
            with_payload=True,
        )

    candidates: list[_Candidate] = []
    seen: set[str] = set()
    for point in response.points:
        pid = str(point.id)
        if pid in seen:
            continue
        seen.add(pid)
        candidates.append(_Candidate(page_id=pid, score=float(point.score), phase="vector"))
    return candidates


# ── Phase 1 (lexical degrade) — Postgres keyword/title search ──────────────────


async def _phase1_lexical_search(
    query: str,
    *,
    vault_id: str,
    k: int,
    session: AsyncSession | None,
    type_filter: list[str] | None = None,
    exclude_stub_pages: bool = False,
) -> list[_Candidate]:
    """
    Lexical degrade for Phase 1 when ``EMBEDDINGS_ENABLED=false`` (ADR-0030 §2.3, Feature B).

    Tokenizes *query* (lowercase, split on non-alphanumeric) then runs a case-insensitive
    ``lower(title) LIKE '%token%'`` prefilter capped to *k* rows (portable: works in both
    Postgres production and SQLite tests). Each token generates one predicate combined with
    OR — so any title containing any query token is a candidate. Rows are ordered by the
    number of matching tokens (computed deterministically server-side via a CASE SUM) and
    then by title.

    Bounding guarantee (I7): the query carries an explicit ``LIMIT :k`` — it NEVER loads
    every page body. Only the ``id`` column is fetched here; the assembler (Phase 4) already
    reads source-file bodies for the final candidates — no second walk (I1).

    The resulting candidates carry ``phase="vector"`` to preserve the structurally identical
    ``RetrievalContext`` contract expected by callers (shared contract — signature unchanged).
    Score = term-overlap count (0.0–1.0 normalised by token count); deterministic.

    R8-5: optional ``type_filter`` applies ``AND CAST(type AS TEXT) IN (:t0, :t1, …)``
    at the SQL level (defense-in-depth together with the Phase 4 gate). ``CAST(type AS TEXT)``
    is portable: equivalent on Postgres (production) and SQLite (tests), unlike Postgres-only
    ``::text``. No user input is interpolated — type values travel as bound params.

    Returns at most *k* ``_Candidate`` objects, never raises on empty query or no matches.
    """
    if k <= 0:
        return []

    # Tokenise: lowercase, split on non-alphanumeric (mirrors _SLUG_RE spirit, no dep).
    raw_tokens = re.split(r"[^a-z0-9]+", query.lower())
    tokens = [t for t in raw_tokens if t]  # drop empties from leading/trailing splits
    if not tokens:
        return []

    # Build per-token case-insensitive LIKE predicates with bound params.
    # Tokens are already lowercased; we compare against lower(title) so this is portable
    # across both Postgres (production) and SQLite (unit tests).  Never interpolate user
    # input — it travels as bound params :tok0/:tok1/… .
    # E.g. "foo bar" → "lower(title) LIKE :tok0 OR lower(title) LIKE :tok1"
    ilike_parts = " OR ".join(f"lower(title) LIKE :tok{i}" for i in range(len(tokens)))
    tok_binds = {f"tok{i}": f"%{tok}%" for i, tok in enumerate(tokens)}

    # Score = sum of per-token hits (each CASE returns 1 when lower(title) contains the token).
    score_parts = " + ".join(
        f"CASE WHEN lower(title) LIKE :tok{i} THEN 1 ELSE 0 END" for i in range(len(tokens))
    )

    binds: dict[str, object] = {"vid": vault_id, "lim": k}
    binds.update(tok_binds)

    # R8-5: type filter clause (AC-R8-5-2).
    # CAST(type AS TEXT) is portable (Postgres + SQLite); avoids Postgres-only ::text.
    # S608 suppressed: only app-generated bind-name placeholders (:t0,:t1,…) are interpolated;
    # actual type values travel as bound params — no SQL-injection vector.
    type_clause = ""
    if type_filter:
        type_placeholders = ",".join(f":t{i}" for i in range(len(type_filter)))
        type_clause = f"AND CAST(type AS TEXT) IN ({type_placeholders}) "  # noqa: S608
        binds.update({f"t{i}": tv for i, tv in enumerate(type_filter)})

    # CG-A6: drop type=query lint-stub pages (defense-in-depth alongside the Phase-4 gate).
    stub_clause = _STUB_EXCLUSION_SQL if exclude_stub_pages else ""

    # S608 suppressed: only app-generated bind placeholders are interpolated; user input
    # travels as bound params via :tok0/:tok1/… — no SQL-injection vector.
    # R7-8: wiki-only scope — same filter as _load_page_meta (AC-R7-8-1).
    select_clause = f"SELECT id, ({score_parts}) AS match_score FROM pages"  # noqa: S608
    where_clause = (
        f"WHERE vault_id = :vid AND deleted_at IS NULL "
        f"AND file_path NOT LIKE 'raw/%' "
        f"{type_clause}"
        f"{stub_clause}"
        f"AND ({ilike_parts})"
    )
    sql = f"{select_clause} {where_clause} ORDER BY match_score DESC, title ASC LIMIT :lim"

    async def _run(sess: AsyncSession) -> list[_Candidate]:
        result = await sess.execute(sa_text(sql).bindparams(**binds))
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        n_tokens = max(len(tokens), 1)
        for row in result:
            m = row._mapping
            pid = str(m["id"])
            if pid in seen:
                continue
            seen.add(pid)
            # Normalise raw count to [0, 1] so scores are comparable to cosine range.
            normalised_score = float(m["match_score"]) / n_tokens
            # Phase label is "vector" to preserve the shared-contract RetrievalContext shape.
            candidates.append(_Candidate(page_id=pid, score=normalised_score, phase="vector"))
        return candidates

    if session is not None:
        return await _run(session)
    async with get_session() as sess:
        return await _run(sess)


# ── Phase 2 — graph-expansion ───────────────────────────────────────────────────


async def _phase2_graph_expansion(
    seed_ids: list[str],
    *,
    vault_id: str,
    depth: int,
    session: AsyncSession | None,
) -> list[_Candidate]:
    """
    BFS over the ``edges`` table from *seed_ids*, depth-bounded (HARD ≤ 2, I2/I7), ordered by
    edge ``weight`` DESC, plus resolved ``links.target_page_id`` for direct-link expansion.

    Reads the ``edges`` table directly — it does NOT call the GraphEngine or FA2 (I2). The
    expansion score for a discovered page is the HIGHEST incident edge weight that reached it.
    Pages already in the seed set are not re-emitted (the vector phase owns them).
    """
    if not seed_ids or depth <= 0:
        return []

    seed_set = set(seed_ids)
    # page_id → best (highest) edge weight that reached it during expansion
    best_weight: dict[str, float] = {}
    frontier: set[str] = set(seed_ids)
    visited: set[str] = set(seed_ids)

    async def _run(sess: AsyncSession) -> None:
        nonlocal frontier
        for _ in range(depth):
            if not frontier:
                break
            neighbours = await _expand_frontier(sess, vault_id, frontier)
            next_frontier: set[str] = set()
            for nid, weight in neighbours:
                if nid in seed_set:
                    continue  # the vector phase already owns seeds
                # keep the highest edge weight as the expansion score
                if weight > best_weight.get(nid, float("-inf")):
                    best_weight[nid] = weight
                if nid not in visited:
                    visited.add(nid)
                    next_frontier.add(nid)
            frontier = next_frontier

    if session is not None:
        await _run(session)
    else:
        async with get_session() as sess:
            await _run(sess)

    return [_Candidate(page_id=pid, score=w, phase="expansion") for pid, w in best_weight.items()]


async def _expand_frontier(
    sess: AsyncSession, vault_id: str, frontier: set[str]
) -> list[tuple[str, float]]:
    """
    One BFS hop: return ``(neighbour_page_id, edge_weight)`` for every edge incident to a
    frontier page (either endpoint), ordered by ``weight`` DESC, plus resolved wikilink
    targets/sources from ``links`` (carried at weight 0.0 when no edge backs them).
    """
    frontier_list = list(frontier)
    # bind a comma-separated IN-list of placeholders (frontier is bounded by k * fan-out)
    placeholders = ",".join(f":f{i}" for i in range(len(frontier_list)))
    binds = {f"f{i}": fid for i, fid in enumerate(frontier_list)}
    binds["vid"] = vault_id

    out: list[tuple[str, float]] = []

    # Edges (the F4 4-signal output): pick the opposite endpoint, ordered by weight DESC.
    # S608 is suppressed: only app-generated bind-name placeholders (":f0,:f1,…") are
    # interpolated — the frontier ids travel as bound params, never as SQL text, so this is
    # not a user-input injection vector. Same justification for link_sql / page_sql below.
    #
    # BE-PERF-4: this hop runs on EVERY chat retrieval call (Phase 2), against the full
    # ``edges``/``links`` tables (filtered only by ``vault_id``), so it is a hot path — not
    # the "immaterial" one-off the old comment claimed. The previous form cast the COLUMN to
    # TEXT (``CAST(source_page_id AS TEXT) IN (...)``), which forces Postgres to seq-scan and
    # cast every row instead of using ``ix_edges_source_page_id`` / ``ix_edges_target_page_id``
    # (measured ~330x slower on a 200k-row table: 22.6ms seq scan vs 0.07ms bitmap index scan).
    # On Postgres we instead cast the PARAMETER to ``uuid`` so the comparison hits the native
    # column type and its index. SQLite (tests) has no UUID type and stores ids as TEXT, and
    # ``CAST(text AS UUID)`` is UNSAFE there (SQLite falls back to NUMERIC affinity for an
    # unrecognized type name and can silently parse a UUID's leading digits as scientific
    # notation, e.g. "123e4567-..." → inf) — so SQLite keeps the original portable
    # ``CAST(col AS TEXT)`` form unchanged (zero behaviour change on the test path).
    if is_postgres_session(sess):
        cast_placeholders = ",".join(f"CAST(:f{i} AS UUID)" for i in range(len(frontier_list)))
        in_clause = (
            f"(source_page_id IN ({cast_placeholders}) "
            f"OR target_page_id IN ({cast_placeholders}))"
        )
    else:
        in_clause = (
            f"(CAST(source_page_id AS TEXT) IN ({placeholders}) "
            f"OR CAST(target_page_id AS TEXT) IN ({placeholders}))"
        )
    edge_sql = (
        f"SELECT source_page_id, target_page_id, weight FROM edges "  # noqa: S608
        f"WHERE vault_id = :vid AND {in_clause} ORDER BY weight DESC"
    )
    edge_result = await sess.execute(sa_text(edge_sql).bindparams(**binds))
    for row in edge_result:
        m = row._mapping
        src = str(m["source_page_id"])
        tgt = str(m["target_page_id"])
        weight = float(m["weight"])
        # the neighbour is whichever endpoint is NOT in the frontier
        if src in frontier and tgt not in frontier:
            out.append((tgt, weight))
        elif tgt in frontier and src not in frontier:
            out.append((src, weight))
        elif src in frontier and tgt in frontier:
            # both in frontier — surface both opposite directions at this weight
            out.append((src, weight))
            out.append((tgt, weight))

    # Resolved wikilinks (direct-link expansion). Edge weight isn't on the links row, so a
    # link-only neighbour carries weight 0.0 — it ranks below any edge-backed expansion but
    # is still a candidate (ADR-0022 §2.2 "also follow resolved links.target_page_id").
    link_sql = (
        f"SELECT source_page_id, target_page_id FROM links "  # noqa: S608
        f"WHERE dangling = false AND target_page_id IS NOT NULL AND {in_clause}"
    )
    link_binds = {f"f{i}": fid for i, fid in enumerate(frontier_list)}
    link_result = await sess.execute(sa_text(link_sql).bindparams(**link_binds))
    for row in link_result:
        m = row._mapping
        src = str(m["source_page_id"])
        tgt = str(m["target_page_id"])
        if src in frontier and tgt not in frontier:
            out.append((tgt, 0.0))
        elif tgt in frontier and src not in frontier:
            out.append((src, 0.0))

    return out


# ── Phase 3 — token-budget ranking ──────────────────────────────────────────────


def _phase3_rank(
    vector_candidates: list[_Candidate],
    expansion_candidates: list[_Candidate],
) -> list[_Candidate]:
    """
    Rank candidates: vector seeds first (by cosine DESC), then expansions (by edge weight
    DESC). A page surfaced by both phases keeps its vector ranking (seeds win). Returns the
    de-duplicated rank order consumed by the budget-bounded assembler (phase 4).
    """
    ranked: list[_Candidate] = []
    seen: set[str] = set()

    for c in sorted(vector_candidates, key=lambda x: x.score, reverse=True):
        if c.page_id not in seen:
            seen.add(c.page_id)
            ranked.append(c)

    for c in sorted(expansion_candidates, key=lambda x: x.score, reverse=True):
        if c.page_id not in seen:
            seen.add(c.page_id)
            ranked.append(c)

    return ranked


# ── Phase 4 — context assembly ──────────────────────────────────────────────────


async def _phase4_assemble(
    ranked: list[_Candidate],
    *,
    vault_id: str,
    budget_chars: int,
    session: AsyncSession | None,
    type_filter: list[str] | None = None,
    exclude_stub_pages: bool = False,
) -> tuple[str, list[Citation]]:
    """
    Walk *ranked* candidates while the char budget remains; load each source-file body
    (per-passage capped), assign the next 1-based contiguous ``n``, append
    ``[n] <title> (<path>)\\n<passage>`` to the text and record the matching :class:`Citation`.

    Lowest-ranked candidates that do not fit are DROPPED (never mid-sentence
    truncate-without-drop, AC-F5-4). The assembler is the single authority for ``[n]`` ↔
    ``Citation`` parity. Returns ``(text, citations)``.

    R8-5: ``type_filter`` is the authoritative gate applied inside ``_load_page_meta``
    (defense-in-depth; mirrors the raw/ exclusion pattern of ADR-0049, AC-R8-5-2).
    Candidates whose ``type`` is not in the filter are silently dropped here, just as
    raw/ candidates are dropped by the raw/ filter.

    CG-A1 (ADR-0067 P3-3): each block header now carries the page's compact path
    (``concepts/<slug>``) after the title so the model can name the relevant page path
    naturally in prose. The bare ``[n]`` marker remains the machine-resolvable anchor
    (parentheses are never counted as an ``[n]`` marker).

    CG-A6: when ``exclude_stub_pages`` is True, ``_load_page_meta`` drops type=query lint
    stubs so they never resolve a citation.

    BE-PERF-3: ``vault_id`` is the authoritative gate applied inside ``_load_page_meta`` —
    a Phase-1 vector candidate from another vault (possible until the Qdrant backfill runs,
    see ``app.rag.retrieval._phase1_vector_search``) is silently dropped here rather than
    surfaced/cited, closing the cross-vault citation leak.
    """
    if not ranked or budget_chars <= 0:
        return "", []

    # Per-passage cap so a single large source cannot consume the whole budget (§3.3).
    per_passage_cap = max(1, budget_chars // max(1, len(ranked)))

    page_ids = [c.page_id for c in ranked]
    meta = await _load_page_meta(
        page_ids,
        session,
        vault_id=vault_id,
        type_filter=type_filter or [],
        exclude_stub_pages=exclude_stub_pages,
    )

    # BE-PERF-12: candidates that survived the meta gate, still in rank order. The source-file
    # reads below have NO cross-candidate dependency (each is a single targeted file read, I1),
    # so they are fetched CONCURRENTLY via asyncio.gather — bounded by len(ranked), which is
    # already capped by k * expansion_depth (I7) — instead of paying serial disk latency once
    # per candidate on every chat turn. The budget/drop decision loop below stays sequential and
    # rank-ordered (unchanged semantics), it just consumes the pre-fetched passages.
    surviving = [(cand, meta[cand.page_id]) for cand in ranked if cand.page_id in meta]
    passages = await asyncio.gather(
        *(_load_passage(info["file_path"], cap=per_passage_cap) for _, info in surviving)
    )

    parts: list[str] = []
    citations: list[Citation] = []
    used_chars = 0
    n = 0

    for (cand, info), passage in zip(surviving, passages, strict=True):
        title = info["title"]
        # CG-A1: compact page path exposed in the header so the model can reference it in prose.
        disp = _display_path(info["file_path"])
        header = f"[{n + 1}] {title} ({disp})" if disp else f"[{n + 1}] {title}"
        if not passage:
            continue  # unreadable/empty source — skip rather than cite an empty passage

        candidate_n = n + 1
        block = f"{header}\n{passage}\n"
        # whole-block granularity: a block either fits or is dropped (never split mid-sentence)
        if used_chars + len(block) > budget_chars and citations:
            # budget exhausted; lowest-ranked remaining candidates are dropped (AC-F5-4)
            break
        if used_chars + len(block) > budget_chars and not citations:
            # the very first block already overflows — include it capped to the budget so a
            # non-empty result is still returned, but on a WHOLE-passage boundary (we shrink
            # the passage, never split a sentence across the cut: trim then strip trailing
            # partial word). Drop nothing else after.
            allowance = max(0, budget_chars - len(f"{header}\n") - 1)
            passage = _trim_to_boundary(passage, allowance)
            if not passage:
                continue
            block = f"{header}\n{passage}\n"

        n = candidate_n
        parts.append(block)
        used_chars += len(block)
        citations.append(
            Citation(
                n=n,
                ref=PageRef(
                    id=cand.page_id,
                    title=title,
                    slug=slugify(title),
                ),
                score=cand.score,
                phase=cand.phase,
            )
        )
        if used_chars >= budget_chars:
            break

    return "".join(parts), citations


def _trim_to_boundary(passage: str, allowance: int) -> str:
    """Trim *passage* to ``allowance`` chars on a whitespace boundary (no mid-word cut)."""
    if allowance <= 0:
        return ""
    if len(passage) <= allowance:
        return passage
    cut = passage[:allowance]
    sp = cut.rfind(" ")
    if sp > 0:
        cut = cut[:sp]
    return cut.rstrip()


async def _load_passage(file_path: str, *, cap: int) -> str:
    """
    Read the body of a single source file (targeted I1-safe read — one file, no vault walk),
    capped to *cap* chars on a whitespace boundary. Returns '' on any read error.

    The blocking filesystem read runs in a worker thread (``asyncio.to_thread``) so Phase-4
    assembly — which reads up to ``k``×depth files in sequence — never blocks the event loop
    and stall concurrent chat streams / watcher events (I3).
    """
    if cap <= 0:
        return ""
    full = (settings.vault_root / file_path).resolve()

    def _read() -> str | None:
        if not full.is_file():
            return None
        return full.read_text(encoding="utf-8", errors="replace")

    try:
        raw = await asyncio.to_thread(_read)
    except OSError as exc:  # pragma: no cover - filesystem edge
        logger.warning("retrieval: could not read source %s: %s", file_path, exc)
        return ""
    if raw is None:
        return ""
    body = raw.strip()
    return _trim_to_boundary(body, cap) if len(body) > cap else body


# ── Postgres metadata reads ─────────────────────────────────────────────────────


async def _read_data_version(vault_id: str, session: AsyncSession | None) -> int:
    """Read ``vault_state.data_version`` (read-only snapshot, AC-F5-5). 0 if no row."""

    async def _run(sess: AsyncSession) -> int:
        result = await sess.execute(
            sa_text("SELECT data_version FROM vault_state WHERE vault_id = :vid").bindparams(
                vid=vault_id
            )
        )
        row = result.first()
        return int(row[0]) if row is not None else 0

    if session is not None:
        return await _run(session)
    async with get_session() as sess:
        return await _run(sess)


async def _load_page_meta(
    page_ids: list[str],
    session: AsyncSession | None,
    vault_id: str,
    type_filter: list[str] | None = None,
    exclude_stub_pages: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Load ``{title, file_path}`` for each live page id (I1 — table read, no vault walk).

    ``title`` falls back to the filename stem when frontmatter title is NULL/blank, so it is
    NEVER empty (§2.6). Soft-deleted pages are excluded (they must not be cited).

    BE-PERF-3 (security/correctness fix): ``vault_id`` is now a REQUIRED, authoritative gate
    (``AND vault_id = :vid``), mirroring the vault-scoping already applied to graph reads
    (``app.graph.engine._load_data``) and cascade/orphan-detect ops. Before this fix, a page
    belonging to ANOTHER vault could be loaded here and actually CITED in chat/search
    responses — a cross-vault leak, not merely a performance issue — because Phase-1 vector
    candidates (Qdrant) were not vault-scoped either. Both sides are now scoped: Phase 1
    filters by ``vault_id`` in the Qdrant query, and this function re-asserts the same scope
    against Postgres as the authoritative gate (defense-in-depth, same pattern as the raw/
    exclusion below and R8-5's type_filter).

    R8-5: optional ``type_filter`` adds ``AND CAST(type AS TEXT) IN (:t0, :t1, …)`` — the
    authoritative gate (defense-in-depth, mirrors ADR-0049 raw/ pattern, AC-R8-5-2).
    ``CAST(type AS TEXT)`` is portable (Postgres + SQLite); values are bound params.

    CG-A6: optional ``exclude_stub_pages`` adds the authoritative stub-exclusion clause
    (``NOT (type=query AND tags LIKE '%stub%' AND tags LIKE '%lint%')``) so a lint-stub
    query page can never resolve an ``[n]`` citation on the chat path.
    """
    if not page_ids:
        return {}

    placeholders = ",".join(f":p{i}" for i in range(len(page_ids)))
    binds: dict[str, object] = {f"p{i}": pid for i, pid in enumerate(page_ids)}
    binds["vid"] = vault_id

    # R8-5: type filter clause (AC-R8-5-2).
    # CAST(type AS TEXT) is portable (Postgres + SQLite); avoids Postgres-only ::text.
    # S608 suppressed: only app-generated bind-name placeholders (:t0,:t1,…) are interpolated;
    # actual type values travel as bound params — no SQL-injection vector.
    type_clause = ""
    if type_filter:
        type_placeholders = ",".join(f":t{i}" for i in range(len(type_filter)))
        type_clause = f"AND CAST(type AS TEXT) IN ({type_placeholders}) "  # noqa: S608
        binds.update({f"t{i}": tv for i, tv in enumerate(type_filter)})

    # CG-A6: authoritative stub-exclusion gate (see _STUB_EXCLUSION_SQL).
    stub_clause = _STUB_EXCLUSION_SQL if exclude_stub_pages else ""

    async def _run(sess: AsyncSession) -> dict[str, dict[str, Any]]:
        # S608 suppressed: app-generated bind placeholders (":p0,:p1,…") only — ids are bound.
        #
        # BE-PERF-4: this runs on EVERY chat retrieval (Phase 4, one call per turn). The old
        # ``CAST(id AS TEXT) IN (...)`` form casts every row's PK to text before comparing,
        # which prevents Postgres from using the ``pages`` primary-key index — measured ~650x
        # slower on a 200k-row table (18.7ms seq scan vs 0.03ms index scan). On Postgres we
        # cast the PARAMETERS to ``uuid`` instead so the comparison hits the PK index directly.
        # SQLite (tests) keeps the original ``CAST(id AS TEXT) = :param`` form unchanged —
        # ``CAST(text AS UUID)`` is unsafe there (SQLite has no UUID type; an unrecognized cast
        # target falls back to NUMERIC affinity and can misparse a UUID's leading digits as a
        # float, e.g. "123e4567-..." → inf). Same fix as _expand_frontier.
        #
        # R7-8 (AC-R7-8-1): citation assembly is restricted to wiki/ pages only.
        # raw/ documents are source material, not citable wiki knowledge — they exist to
        # feed the ingest pipeline and should not be surfaced in chat context or search
        # results alongside synthesized wiki pages. The filter `file_path NOT LIKE 'raw/%'`
        # is portable SQL (works on both Postgres production and SQLite test engine).
        # See docs/adr/0049-retrieval-wiki-only-scope.md for the full rationale.
        if is_postgres_session(sess):
            id_clause = (
                "AND id IN ("
                + ",".join(f"CAST(:p{i} AS UUID)" for i in range(len(page_ids)))
                + ") "
            )
        else:
            id_clause = f"AND CAST(id AS TEXT) IN ({placeholders}) "
        page_sql = (
            f"SELECT id, title, file_path FROM pages "  # noqa: S608
            f"WHERE deleted_at IS NULL "
            f"AND vault_id = :vid "
            f"{id_clause}"
            f"AND file_path NOT LIKE 'raw/%' "
            f"{type_clause}"
            f"{stub_clause}"
        )
        result = await sess.execute(sa_text(page_sql).bindparams(**binds))
        out: dict[str, dict[str, Any]] = {}
        for row in result:
            m = row._mapping
            pid = str(m["id"])
            file_path = str(m["file_path"])
            raw_title = m["title"]
            title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None
            if title is None:
                title = Path(file_path).stem or "Untitled"
            out[pid] = {"title": title, "file_path": file_path}
        return out

    if session is not None:
        return await _run(session)
    async with get_session() as sess:
        return await _run(sess)


# ── Helpers ─────────────────────────────────────────────────────────────────────


def slugify(title: str) -> str:
    """
    Derive a url-friendly slug from *title* (lower, ascii-hyphenated). NOT a DB column
    (§2.6) — mirrors ``orchestrator._slugify``. Never empty (falls back to 'untitled').
    """
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


# ── R8-5: date-sort helpers ─────────────────────────────────────────────────────


async def _sort_citations_by_date(
    citations: list[Citation],
    *,
    sort: SearchSortOption,
    session: AsyncSession | None,
) -> list[Citation]:
    """
    Re-order *citations* by ``pages.updated_at`` (AC-R8-5-2).

    Fetches ``updated_at`` for each cited page id in a single bounded read (one query,
    not N queries — I7 spirit). Pages whose ids are not found retain their current position
    (defensive: should not occur since Phase 4 already loaded them).

    ``sort="date_desc"`` → newest first (updated_at DESC).
    ``sort="date_asc"``  → oldest first (updated_at ASC).

    Phase internals (budgets, BFS) are NEVER changed (I7 untouched). The context text
    is rebuilt by the caller via ``_rebuild_text_from_citations``.
    """
    if not citations:
        return citations

    page_ids = [c.ref.id for c in citations]
    placeholders = ",".join(f":p{i}" for i in range(len(page_ids)))
    binds: dict[str, object] = {f"p{i}": pid for i, pid in enumerate(page_ids)}

    # S608: app-generated bind placeholders only — page_ids are already strings, not user input.
    # CAST(updated_at AS TEXT) produces an ISO-8601 sortable string on both engines (SQLite
    # stores timestamps as TEXT already; Postgres casts TIMESTAMPTZ to text in ISO format) —
    # that projection cast is unrelated to index use and stays unchanged.
    #
    # BE-PERF-4: the WHERE-side ``CAST(id AS TEXT) IN (...)`` prevented the ``pages`` PK index
    # from being used on Postgres (same root cause as _load_page_meta). Cast the PARAMETERS to
    # ``uuid`` on Postgres instead; SQLite keeps the original portable column-cast form (see
    # _load_page_meta docstring for why casting the SQLite column to UUID would be unsafe).
    async def _run(sess: AsyncSession) -> dict[str, str]:
        if is_postgres_session(sess):
            id_clause = (
                "WHERE id IN ("
                + ",".join(f"CAST(:p{i} AS UUID)" for i in range(len(page_ids)))
                + ")"
            )
        else:
            id_clause = f"WHERE CAST(id AS TEXT) IN ({placeholders})"
        date_sql = (
            f"SELECT CAST(id AS TEXT) AS pid, CAST(updated_at AS TEXT) AS ua FROM pages "  # noqa: S608
            f"{id_clause}"
        )
        result = await sess.execute(sa_text(date_sql).bindparams(**binds))
        return {str(row._mapping["pid"]): str(row._mapping["ua"]) for row in result}

    if session is not None:
        updated_at_map = await _run(session)
    else:
        async with get_session() as sess:
            updated_at_map = await _run(sess)

    # Sort: pages without an updated_at entry stay at the end in their original order.
    reverse = sort == "date_desc"
    # Use a fallback string that sorts to the end (empty string sorts before any ISO date in
    # both ascending and descending modes via the NOT-FOUND path being last).
    _SORT_MISSING = "" if not reverse else "\xff\xff"
    sorted_citations = sorted(
        citations,
        key=lambda c: updated_at_map.get(c.ref.id, _SORT_MISSING),
        reverse=reverse,
    )

    # Re-number n (1-based contiguous) to reflect the new order.
    renumbered: list[Citation] = []
    for new_n, cit in enumerate(sorted_citations, start=1):
        renumbered.append(Citation(n=new_n, ref=cit.ref, score=cit.score, phase=cit.phase))
    return renumbered


def _rebuild_text_from_citations(citations: list[Citation], original_text: str) -> str:
    """
    Rebuild the context text after a date-sort re-ordering (AC-R8-5-2).

    The original *text* contains blocks in the old ``[n]`` order.  After re-ordering the
    citations list we need to produce a new text string with blocks in the new order and
    re-numbered ``[n]`` markers.

    Strategy: parse each ``[old_n] <title>\\n<passage>\\n`` block from *original_text* into
    a dict, then emit them in the new citation order.  Blocks that cannot be parsed fall back
    to a minimal placeholder (defensive; should not occur in practice).

    This is a presentation-level transformation only — budgets are NEVER revisited (I7).
    """
    if not citations or not original_text:
        return original_text

    # Parse the original text into blocks keyed by old n.
    # Pattern: "[n] <anything up to newline>\n<passage until next [n] or end>"
    _BLOCK_RE = re.compile(r"\[(\d+)\] [^\n]*\n(?:(?!\[\d+\]).*\n?)*", re.MULTILINE)
    old_blocks: dict[int, str] = {}
    for m in _BLOCK_RE.finditer(original_text):
        old_n_str = m.group(1)
        old_blocks[int(old_n_str)] = m.group(0)

    # Build a mapping from ref.id → old [n] so we can look up the block by id.
    # We need to recover the old n for each citation. Since citations were renumbered,
    # we cannot use c.n directly. Instead we store old_n in _sort_citations_by_date
    # before renumbering. But we don't have it here.
    #
    # Practical approach: the original text preserves the pre-sort n ordering.
    # After sort, citations[new_n-1].n == new_n.  The original n ordering was
    # 1..len(citations) in rank order. We can reconstruct by comparing ref.id order.
    #
    # However, we only know the NEW ordering.  The ORIGINAL ordering (before sort) was
    # the rank order from Phase 4 (vector seeds first, then expansions).  Since we have
    # both the original text blocks (keyed by old_n) and the NEW citation list (with
    # renumbered n), we need to map new citation position → old block.
    #
    # To do this cleanly, we pass the original n through the sort pipeline.  Since the
    # Citation objects were renumbered in _sort_citations_by_date (new_n = 1..N), but
    # the old blocks in original_text still have the pre-sort [n] labels, we cannot
    # trivially map them.  The safest approach: re-parse the text to extract (title, passage)
    # pairs in old-n order, then re-emit in new order.

    # Extract (old_n, title, passage_body) triples.
    _FULL_BLOCK_RE = re.compile(
        r"\[(\d+)\] ([^\n]*)\n((?:(?!\[\d+\]).*\n?)*)",
        re.MULTILINE,
    )
    old_triples: dict[int, tuple[str, str]] = {}
    for m in _FULL_BLOCK_RE.finditer(original_text):
        old_n = int(m.group(1))
        title = m.group(2)
        passage = m.group(3)
        old_triples[old_n] = (title, passage)

    # Build a mapping: ref.id → (title, passage) from old text.
    # The old ordering had citations in rank order with n=1..N; we need to know which
    # old_n corresponds to which ref.id.  Unfortunately _sort_citations_by_date already
    # renumbered the citations and we lost the old mapping.
    #
    # Work-around: build id→old_n from the ORIGINAL citation list.  But we don't have it
    # here; we only have the re-ordered (renumbered) list.
    #
    # Final clean solution: store the old n on each citation before renumbering.
    # Since that would require changing Citation, we use a simpler heuristic:
    #   The original text has blocks 1..N in RANK order.
    #   The titles in the text must match the titles in citations (same pages, same run).
    #   Map: title → (title_text, passage_body) from old_triples, then re-emit by new n.
    #   If two pages have the same title, the heuristic may mis-assign — but duplicate-title
    #   pages are already a schema violation (K6), so this is acceptable.

    # CG-A1: block headers may now carry a trailing " (<path>)" (see _phase4_assemble). Key the
    # lookup on the BARE title (path suffix stripped) so it matches Citation.ref.title, but keep
    # the FULL header (with path) to re-emit — otherwise the date-sorted text would (a) lose the
    # path suffix and (b) fail to find the passage (key mismatch → empty passage regression).
    header_by_title: dict[str, tuple[str, str]] = {}
    for _, (header_line, passage) in sorted(old_triples.items()):
        bare = _strip_path_suffix(header_line)
        if bare not in header_by_title:
            header_by_title[bare] = (header_line, passage)

    parts: list[str] = []
    for cit in citations:
        header_line, passage = header_by_title.get(cit.ref.title, (cit.ref.title, ""))
        parts.append(f"[{cit.n}] {header_line}\n{passage}")

    return "".join(parts)
