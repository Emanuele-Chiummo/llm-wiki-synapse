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
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.embeddings import get_embedding_client
from app.qdrant_client import get_qdrant_client

logger = logging.getLogger(__name__)

# ── Budget convention (AQ-v0.5-2 — mirror chat/context.py, no tokenizer) ────────
# char/4 SAFETY cap, not exact accounting. Under-fill is the safe direction (I7).
_CHARS_PER_TOKEN = 4

# F14 "retrieved" slice of the 60/20/5/15 budget (ADR-0022 §2.2 phase 3).
_RETRIEVAL_BUDGET_FRACTION = 0.20

# Graph-expansion is BFS-bounded to depth 2 (ADR-0022 §2.2 phase 2 — HARD cap, I7).
_MAX_EXPANSION_DEPTH = 2

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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

    Returns:
        A :class:`RetrievalContext` whose ``citations`` count equals the distinct ``[n]``
        count in ``text``.
    """
    depth = max(0, min(expansion_depth, _MAX_EXPANSION_DEPTH))

    # Snapshot data_version BEFORE any read so AC-F5-5 can prove retrieval is read-only.
    data_version = await _read_data_version(vault_id, session)

    budget_tokens = int(max(context_window, 1) * _RETRIEVAL_BUDGET_FRACTION)
    budget_chars = budget_tokens * _CHARS_PER_TOKEN

    # ── Phase 1: vector search OR lexical degrade (ADR-0030, Feature B) ────────
    # When embeddings are enabled (default): dense top-k via Qdrant + bge-m3 (I9).
    # When disabled: bounded Postgres keyword/title search — no embedding client or
    # Qdrant call is made (ADR-0030 §2.3, §2.5). Phases 2–4 are UNCHANGED either way.
    if settings.embeddings_enabled:
        vector_candidates = await _phase1_vector_search(query, k=k)
    else:
        vector_candidates = await _phase1_lexical_search(
            query, vault_id=vault_id, k=k, session=session
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
    text, citations = await _phase4_assemble(ranked, budget_chars=budget_chars, session=session)

    approx_tokens = len(text) // _CHARS_PER_TOKEN

    phase1_mode = "vector" if settings.embeddings_enabled else "lexical"
    logger.info(
        "retrieve: vault=%r query_len=%d phase1=%s phase1_hits=%d expansions=%d "
        "citations=%d approx_tokens=%d/%d data_version=%d",
        vault_id,
        len(query),
        phase1_mode,
        len(vector_candidates),
        len(expansion_candidates),
        len(citations),
        approx_tokens,
        budget_tokens,
        data_version,
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


async def _phase1_vector_search(query: str, *, k: int) -> list[_Candidate]:
    """
    Embed *query* via the existing bge-m3 wrapper and run a dense top-k cosine search over
    the existing ``synapse_pages`` collection (I9 — no new service, no new collection).

    Point ids are ``pages.id`` (ADR-0002). Score = cosine similarity. Dense-only (AQ-v0.5-1).
    """
    if k <= 0:
        return []

    vector = await get_embedding_client().embed(query)
    client = get_qdrant_client()

    # query_points is the current (non-deprecated) dense top-k API; returns QueryResponse
    # whose .points are ScoredPoint(id, score, payload, ...). Replaces the removed .search().
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

    # S608 suppressed: only app-generated bind placeholders are interpolated; user input
    # travels as bound params via :tok0/:tok1/… — no SQL-injection vector.
    select_clause = f"SELECT id, ({score_parts}) AS match_score FROM pages"  # noqa: S608
    where_clause = f"WHERE vault_id = :vid AND deleted_at IS NULL AND ({ilike_parts})"
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
    # Cast UUID columns to text so the string-typed frontier binds compare cleanly: asyncpg
    # sends the placeholders as VARCHAR and Postgres has no implicit `uuid = varchar` operator
    # (else: UndefinedFunctionError). CAST(... AS TEXT) is portable — equivalent to ::text on
    # Postgres and valid on SQLite (tests), unlike the Postgres-only ::text shorthand. The
    # frontier is small (k * fan-out), so the lost index use on edges/links is immaterial here.
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
    budget_chars: int,
    session: AsyncSession | None,
) -> tuple[str, list[Citation]]:
    """
    Walk *ranked* candidates while the char budget remains; load each source-file body
    (per-passage capped), assign the next 1-based contiguous ``n``, append
    ``[n] <title>\\n<passage>`` to the text and record the matching :class:`Citation`.

    Lowest-ranked candidates that do not fit are DROPPED (never mid-sentence
    truncate-without-drop, AC-F5-4). The assembler is the single authority for ``[n]`` ↔
    ``Citation`` parity. Returns ``(text, citations)``.
    """
    if not ranked or budget_chars <= 0:
        return "", []

    # Per-passage cap so a single large source cannot consume the whole budget (§3.3).
    per_passage_cap = max(1, budget_chars // max(1, len(ranked)))

    page_ids = [c.page_id for c in ranked]
    meta = await _load_page_meta(page_ids, session)

    parts: list[str] = []
    citations: list[Citation] = []
    used_chars = 0
    n = 0

    for cand in ranked:
        info = meta.get(cand.page_id)
        if info is None:
            continue  # page vanished (soft-deleted/race) — skip, do not cite

        title = info["title"]
        passage = _load_passage(info["file_path"], cap=per_passage_cap)
        if not passage:
            continue  # unreadable/empty source — skip rather than cite an empty passage

        candidate_n = n + 1
        block = f"[{candidate_n}] {title}\n{passage}\n"
        # whole-block granularity: a block either fits or is dropped (never split mid-sentence)
        if used_chars + len(block) > budget_chars and citations:
            # budget exhausted; lowest-ranked remaining candidates are dropped (AC-F5-4)
            break
        if used_chars + len(block) > budget_chars and not citations:
            # the very first block already overflows — include it capped to the budget so a
            # non-empty result is still returned, but on a WHOLE-passage boundary (we shrink
            # the passage, never split a sentence across the cut: trim then strip trailing
            # partial word). Drop nothing else after.
            allowance = max(0, budget_chars - len(f"[{candidate_n}] {title}\n") - 1)
            passage = _trim_to_boundary(passage, allowance)
            if not passage:
                continue
            block = f"[{candidate_n}] {title}\n{passage}\n"

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


def _load_passage(file_path: str, *, cap: int) -> str:
    """
    Read the body of a single source file (targeted I1-safe read — one file, no vault walk),
    capped to *cap* chars on a whitespace boundary. Returns '' on any read error.
    """
    if cap <= 0:
        return ""
    full = (settings.vault_root / file_path).resolve()
    try:
        if not full.is_file():
            return ""
        body = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - filesystem edge
        logger.warning("retrieval: could not read source %s: %s", file_path, exc)
        return ""
    body = body.strip()
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
    page_ids: list[str], session: AsyncSession | None
) -> dict[str, dict[str, Any]]:
    """
    Load ``{title, file_path}`` for each live page id (I1 — table read, no vault walk).

    ``title`` falls back to the filename stem when frontmatter title is NULL/blank, so it is
    NEVER empty (§2.6). Soft-deleted pages are excluded (they must not be cited).
    """
    if not page_ids:
        return {}

    placeholders = ",".join(f":p{i}" for i in range(len(page_ids)))
    binds = {f"p{i}": pid for i, pid in enumerate(page_ids)}

    async def _run(sess: AsyncSession) -> dict[str, dict[str, Any]]:
        # S608 suppressed: app-generated bind placeholders (":p0,:p1,…") only — ids are bound.
        # CAST(id AS TEXT) — page_ids arrive as strings; without the cast Postgres rejects
        # `uuid = varchar` (UndefinedFunctionError). CAST is portable (Postgres + SQLite),
        # unlike Postgres-only ::text. Same fix as _expand_frontier.
        page_sql = (
            f"SELECT id, title, file_path FROM pages "  # noqa: S608
            f"WHERE deleted_at IS NULL AND CAST(id AS TEXT) IN ({placeholders})"
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
