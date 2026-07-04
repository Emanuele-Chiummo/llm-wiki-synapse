"""
Deep Research loop — bounded multi-query SearXNG loop + ingest-seam synthesis (F10, ADR-0024).

THE INVARIANT CONTRACT (I7 headline — any violation is P0 rejection):
  1. The refinement loop is `for iteration in range(1, max_iter + 1)` — structurally capped.
     NOT a while-True. Not configurable mid-loop.
  2. token_budget checked at the TOP of each round before spending — under-spend, never over.
  3. concurrency=3 is a HARDCODED module constant shared with ops/searxng.py. Changing it
     requires an architect-approved ADR amendment.
  4. Bounds are FROZEN on the deep_research_runs row at INSERT and read once into locals.
     The loop NEVER re-reads config or env mid-flight (AQ-v0.5-4).
  5. status defaults pessimistically to "max_iter_reached"; terminal write is in a finally
     block — never leaves status "running" (Do-NOT #7, AC-F10-2b).

I9: ALL web search goes through ops/searxng.py → SEARXNG_URL. Zero fallback engines.
I6: query-gen, assess, synthesize ALL use the resolved InferenceProvider.chat() — no hardcoded
    backend, no isinstance/type-branch, no new ABC method (Do-NOT #6/#10).
I1/I5: synthesis is raw source material → written to vault/raw/sources/ → ingest_file().
       NOT a direct write to vault/wiki/ (Do-NOT #5, AQ-v0.5-3).
I8: Do NOT bump data_version directly — ingest_file does the single bump (Do-NOT #11).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.db import get_session
from app.ingest.provider.base import UsageAccumulator
from app.models import DeepResearchRun, DeepResearchSource
from app.ops.searxng import SearchHit, _semaphore, searxng_search_many

logger = logging.getLogger(__name__)

# Cost-anomaly threshold (ADR-0009 §3, ADR-0024 §3.3)
COST_ANOMALY_THRESHOLD_USD: float = 1.00


# ── Module-level property accessors (frozen once per run, Do-NOT #2 / ADR-0024 §3.1) ──
# These are NOT constants — they read settings lazily so tests that set env vars work.
# The loop uses local variables frozen from run_obj at start, never these again.


def _default_max_iter() -> int:
    from app.config import settings
    from app.config_overrides import effective_int

    return effective_int("deep_research_max_iter", int(settings.deep_research_max_iter))


def _default_token_budget() -> int:
    from app.config import settings
    from app.config_overrides import effective_int

    return effective_int("deep_research_token_budget", int(settings.deep_research_token_budget))


def _max_queries() -> int:
    from app.config import settings
    from app.config_overrides import effective_int

    return effective_int("deep_research_max_queries", int(settings.deep_research_max_queries))


def _fetch_max_chars() -> int:
    from app.config import settings

    return int(settings.deep_research_fetch_max_chars)


# Convenience aliases used inside the module (evaluated lazily at call time)
MAX_QUERIES: int = 5  # conservative compile-time default; real value from _max_queries()
FETCH_MAX_CHARS: int = 20_000  # conservative default; real value from _fetch_max_chars()


# ── Public result type (ADR-0024 §2.3) ────────────────────────────────────────


@dataclass
class DeepResearchResult:
    """Return value of run_deep_research (ADR-0024 §2.3)."""

    run_id: uuid.UUID
    status: Literal["converged", "max_iter_reached", "budget_exhausted", "error"]
    iterations_used: int
    sources_fetched: int
    total_cost_usd: float
    synthesis_page_id: uuid.UUID | None  # pages row created by the re-entrant ingest_file
    error_message: str | None


# ── Internal phase types ───────────────────────────────────────────────────────


@dataclass
class FetchedSource:
    """One fetched + extracted candidate page (ADR-0024 §2.3)."""

    url: str
    title: str
    content_md: str | None  # None on fetch failure
    iteration: int


@dataclass
class Sufficiency:
    """Result of _assess_sufficiency (ADR-0024 §2.3)."""

    sufficient: bool
    gaps: list[str]  # gap descriptions for the next query-generation prompt


# ── Public entry point (ADR-0024 §2.2 — LOCKED SIGNATURE) ─────────────────────


async def run_deep_research(
    *,
    vault_id: str,
    topic: str,
    max_iter: int | None = None,
    token_budget: int | None = None,
    run_id: uuid.UUID | None = None,
    seed_queries: list[str] | None = None,
) -> DeepResearchResult:
    """
    Run ONE bounded deep-research operation end-to-end (S-F10-1, AC-F10-1..7).

    Pipeline (single bounded loop, all six steps in order):
      1. generate 2..MAX_QUERIES sub-queries           (provider, I6)
      2. SearXNG search, concurrency == CONCURRENCY=3  (I9)
      3. fetch + extract candidate pages to markdown
      4. ASSESS sufficiency BEFORE any further query round  (provider, I6)
      5. on sufficient OR iter == max_iter → SYNTHESIZE     (provider, I6)
      6. write synthesis to raw/sources/ → ingest_file(...)  (AQ-v0.5-3, I1/I5)

    Bounds (I7) are FROZEN on the deep_research_runs row at start and never re-read mid-loop.
    Terminal status: converged | max_iter_reached | budget_exhausted | error.
    total_cost_usd accumulated + logged + $1 anomaly WARNING (ADR-0024 §3.3).

    seed_queries (R7-5, AC-R7-5-2): a review item's stored search_queries. When provided
    (non-empty), the FIRST iteration uses these curated queries VERBATIM instead of the provider
    round-trip — no re-generation. Bounded to _max_queries() (I7). Subsequent refinement
    iterations still generate from the assessed gaps. None/[] → generate from scratch (default).
    """
    # ── Resolve and freeze bounds (AQ-v0.5-4, ADR-0024 §3.1) ─────────────────
    frozen_max_iter: int = max_iter if max_iter is not None else _default_max_iter()
    frozen_token_budget: int = token_budget if token_budget is not None else _default_token_budget()

    # ── Run row ────────────────────────────────────────────────────────────────
    # The caller may have already INSERTed the row (POST /research/start pre-inserts
    # it so the client can poll immediately after 202, then passes its run_id here).
    # In that case we reuse that single row — minting a second id would orphan the
    # caller's row and leave it stuck "running" forever (C1, ADR-0024 §8.1).
    # For direct/test calls (run_id is None) we mint + INSERT the row ourselves.
    if run_id is None:
        run_id = uuid.uuid4()
        await _create_run_row(
            run_id=run_id,
            vault_id=vault_id,
            topic=topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
        )

    # ── Resolve provider ONCE (I6 — operation="ingest") ───────────────────────
    provider = await _resolve_provider(vault_id)

    accumulator = UsageAccumulator()
    if provider is not None:
        provider.bind_accumulator(accumulator)

    synthesis_page_id: uuid.UUID | None = None
    error_message: str | None = None
    status: Literal["converged", "max_iter_reached", "budget_exhausted", "error"] = (
        "max_iter_reached"  # PESSIMISTIC DEFAULT — overwritten only on real exit (ADR-0024 §3.2)
    )
    collected: list[FetchedSource] = []
    iterations_used: int = 0
    all_queries: list[str] = []

    try:
        # ── BOUNDS: read ONCE into locals (ADR-0024 §3.2, AQ-v0.5-4) ────────
        # max_iter and token_budget are NOW LOCAL CONSTANTS for this run.
        # The loop NEVER re-reads settings, the DB row, or any config.
        # Bounds are frozen at start (identical to the values written on the row,
        # whether the row was inserted here or by the endpoint). Never re-read.
        max_iter_local: int = frozen_max_iter
        token_budget_local: int = frozen_token_budget

        # Initial query generation (outside the loop — same as ADR-0024 §3.2 spec).
        # R7-5 (AC-R7-5-2): if the caller supplied curated seed_queries (a review item's stored
        # search_queries), use them VERBATIM for the first round — no provider round-trip, no
        # re-generation. Bounded to _max_queries() (I7). Otherwise generate from scratch.
        _mq = _max_queries()
        seeds = [q.strip() for q in (seed_queries or []) if q and q.strip()][:_mq]
        if seeds:
            queries = seeds
            logger.info(
                "run_deep_research: using %d curated seed queries for run_id=%s (no re-generation)",
                len(seeds),
                run_id,
            )
        else:
            queries = await _generate_queries(
                provider, topic, "", max_queries=_mq  # positional: prior_context="" (no gaps)
            )
        all_queries.extend(queries)

        # ── THE BOUNDED LOOP (I7 — structural cap) ────────────────────────────
        # for range, not while True. A reviewer confirms boundedness by reading ONE line.
        for iteration in range(1, max_iter_local + 1):  # ← HARD CAP (ADR-0024 §3.2)
            iterations_used = iteration
            await _update_run_iterations(run_id, iteration)  # live audit

            # ── budget gate BEFORE spending the round (I7, ADR-0024 §3.2) ────
            if accumulator.total_tokens >= token_budget_local:
                status = "budget_exhausted"
                break

            # ── Step 2: SearXNG search (concurrency=3, I9) ───────────────────
            hits: list[SearchHit] = await _search_searxng(queries)

            # ── Step 3: fetch + extract ───────────────────────────────────────
            new_sources = await _fetch_and_extract(hits, iteration=iteration)
            collected.extend(new_sources)

            # ── Persist per-source rows ───────────────────────────────────────
            for src in new_sources:
                await _insert_source_row(run_id, src)

            await _update_run_sources(run_id, len(collected))

            # ── Step 4: ASSESS sufficiency BEFORE deciding to refine ──────────
            # (CLAUDE.md §7, Do-NOT #8 — always assess before refine)
            verdict = await _assess_sufficiency(provider, topic, collected)

            if verdict.sufficient:
                status = "converged"
                break

            # not sufficient AND not last iteration → refine queries
            # (if iteration == max_iter_local the for-range exits → status stays
            # "max_iter_reached" — correct by construction, ADR-0024 §3.2)
            if iteration < max_iter_local:
                queries = await _generate_queries(
                    provider, topic, verdict.gaps, max_queries=_mq  # positional: prior_context
                )
                all_queries.extend(queries)

        # ── Single terminal synthesize (ADR-0024 §3.2 — runs for all terminal statuses) ──
        # NOTE: status "error" falls through to the finally block below (no synthesize).
        if status in ("converged", "max_iter_reached", "budget_exhausted"):
            synthesis_md = await _synthesize(provider, topic, collected)
            synthesis_page_id = await _ingest_synthesis(run_id, vault_id, synthesis_md, topic)

    except Exception as exc:  # noqa: BLE001
        # Terminal error path — always write "error" status (Do-NOT #7, AC-F10-2b)
        status = "error"
        error_message = str(exc)
        logger.exception("run_deep_research: unhandled error for run_id=%s", run_id)

    finally:
        # ── Finalize the run row (ALWAYS — Do-NOT #7 ensures we never leave "running") ──
        total_cost_usd = round(accumulator.total_cost_usd, 4)

        await _finalize_run_row(
            run_id=run_id,
            status=status,
            iterations_used=iterations_used,
            sources_fetched=len(collected),
            queries_used=all_queries,
            total_cost_usd=total_cost_usd,
            synthesis_page_id=synthesis_page_id,
            error_message=error_message,
        )

        # ── Structured log (ADR-0024 §3.3) ───────────────────────────────────
        logger.info(
            "deep_research run_id=%s status=%s iterations=%d sources=%d cost_usd=%.4f topic=%r",
            run_id,
            status,
            iterations_used,
            len(collected),
            total_cost_usd,
            topic,
        )

        # ── $1 cost-anomaly WARNING (ADR-0009 §3 / ADR-0024 §3.3) ─────────────
        if total_cost_usd > COST_ANOMALY_THRESHOLD_USD:
            logger.warning(
                "COST ANOMALY: deep_research run_id=%s total_cost_usd=%.4f exceeds $%.2f "
                "(topic=%r) — investigate runaway/misconfiguration",
                run_id,
                total_cost_usd,
                COST_ANOMALY_THRESHOLD_USD,
                topic,
            )

    return DeepResearchResult(
        run_id=run_id,
        status=status,
        iterations_used=iterations_used,
        sources_fetched=len(collected),
        total_cost_usd=total_cost_usd,
        synthesis_page_id=synthesis_page_id,
        error_message=error_message,
    )


# ── Internal phase functions (names locked for D3 sequence diagram, ADR-0024 §2.3) ──


async def _generate_queries(
    provider: Any,
    topic: str,
    prior_context: str | list[str],
    *,
    max_queries: int,
) -> list[str]:
    """
    Ask the resolved InferenceProvider to generate 2..max_queries SearXNG sub-queries.

    Rides provider.chat() with a phase-specific instruction — no new ABC method (Do-NOT #10).
    Parses the returned text as a newline-delimited list of queries.
    Returns at most max_queries non-empty strings.
    """
    if provider is None:
        # No provider configured (mechanical path) — return the topic as a single query
        logger.debug("_generate_queries: no provider, using topic as single query")
        return [topic]

    if isinstance(prior_context, list):
        gaps_text = "\n".join(f"- {g}" for g in prior_context) if prior_context else ""
        context_block = f"Prior research gaps to address:\n{gaps_text}" if gaps_text else ""
    else:
        context_block = prior_context

    instruction = (
        f"You are a research assistant generating SearXNG search queries for a knowledge base.\n"
        f"Topic: {topic}\n"
        f"{context_block}\n\n"
        f"Generate {max_queries} focused web search queries (one per line, no numbering, no "
        f"markdown) that will find the most relevant and complementary information about this "
        f"topic. Each query should be a plain search string suitable for SearXNG."
    )

    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)

    raw = "".join(chunks).strip()
    queries = [line.strip() for line in raw.splitlines() if line.strip()]
    queries = queries[:max_queries]
    if not queries:
        queries = [topic]
    return queries


async def _search_searxng(queries: list[str]) -> list[SearchHit]:
    """
    Execute queries via SearXNG, concurrency bounded by the shared module semaphore (I9).

    Uses ops/searxng.searxng_search_many — the ONLY web-search call path (Do-NOT #3).
    """
    return await searxng_search_many(queries)


async def _fetch_and_extract(
    hits: list[SearchHit],
    *,
    iteration: int,
) -> list[FetchedSource]:
    """
    Fetch each hit URL and extract readable markdown text (ADR-0024 §4).

    Uses the shared CONCURRENCY semaphore (Do-NOT #4 — no second semaphore).
    Extraction is mechanical HTML→markdown (no LLM call, I6).
    Per-source content capped at FETCH_MAX_CHARS (ADR-0024 §4).
    Fetch failures are logged and produce FetchedSource with content_md=None (Do-NOT #9).
    """
    if not hits:
        return []

    async def _fetch_one(hit: SearchHit) -> FetchedSource:
        async with _semaphore:
            content_md: str | None = None
            try:
                async with httpx.AsyncClient(
                    timeout=10.0,
                    follow_redirects=True,
                    headers={"User-Agent": "Synapse/0.5 DeepResearch"},
                ) as client:
                    resp = await client.get(hit.url)
                if resp.status_code == 200:
                    raw_html = resp.text
                    content_md = _html_to_markdown(raw_html)[: _fetch_max_chars()]
                else:
                    logger.debug(
                        "_fetch_and_extract: HTTP %d for %s",
                        resp.status_code,
                        hit.url,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("_fetch_and_extract: fetch failed for %s: %s", hit.url, exc)

            return FetchedSource(
                url=hit.url,
                title=hit.title,
                content_md=content_md,
                iteration=iteration,
            )

    sources = await asyncio.gather(*[_fetch_one(h) for h in hits])
    return list(sources)


async def _assess_sufficiency(
    provider: Any,
    topic: str,
    collected: list[FetchedSource],
) -> Sufficiency:
    """
    Ask the provider whether the collected sources are sufficient to synthesize a good page.

    Rides provider.chat() — no new ABC method (Do-NOT #10).
    Parses a SUFFICIENT|INSUFFICIENT token optionally followed by gap descriptions.
    Returns Sufficiency(sufficient=True, gaps=[]) on convergence.
    Falls back to sufficient=False on parse failure (conservative, never silently terminates).
    """
    if provider is None:
        # No provider — treat any collected content as sufficient
        logger.debug("_assess_sufficiency: no provider, treating as sufficient")
        return Sufficiency(sufficient=bool(collected), gaps=[])

    sources_summary = _format_sources_for_prompt(collected, max_chars=8000)

    instruction = (
        f"You are evaluating whether collected web research is sufficient to write a good "
        f"knowledge base article about: {topic}\n\n"
        f"Collected sources ({len(collected)} total):\n{sources_summary}\n\n"
        f"Respond with EXACTLY one of:\n"
        f"SUFFICIENT\n"
        f"or:\n"
        f"INSUFFICIENT\n<gap 1>\n<gap 2>\n...\n\n"
        f"If insufficient, list the specific gaps as plain text lines after INSUFFICIENT."
    )

    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)

    raw = "".join(chunks).strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    if not lines:
        return Sufficiency(sufficient=False, gaps=["insufficient (empty response)"])

    verdict_line = lines[0].upper()
    if verdict_line.startswith("SUFFICIENT"):
        return Sufficiency(sufficient=True, gaps=[])

    # INSUFFICIENT — collect gap lines
    gaps = lines[1:] if len(lines) > 1 else ["more information needed"]
    return Sufficiency(sufficient=False, gaps=gaps)


async def _synthesize(
    provider: Any,
    topic: str,
    collected: list[FetchedSource],
) -> str:
    """
    Ask the provider to synthesize a well-structured markdown document from the collected sources.

    Rides provider.chat() — no new ABC method (Do-NOT #10).
    Instructs the model to include [[wikilinks]] and source URLs (ADR-0024 §6).
    Returns the markdown body.
    """
    if provider is None:
        # No provider — produce a minimal synthesis from snippets
        logger.debug("_synthesize: no provider, assembling raw synthesis")
        parts = [f"# {topic}\n\n*Synthesized from web research.*\n"]
        for src in collected:
            if src.content_md:
                parts.append(f"\n## {src.title}\n\nSource: {src.url}\n\n{src.content_md[:2000]}\n")
        return "\n".join(parts)

    sources_full = _format_sources_for_prompt(collected, max_chars=40_000)

    instruction = (
        f"You are writing a research SYNTHESIS article about: {topic}\n\n"
        f"Use the following web research sources:\n{sources_full}\n\n"
        f"Write a comprehensive markdown document that:\n"
        f"- Has a clear # heading for the topic\n"
        f"- Uses ## subheadings for major aspects\n"
        f"- Includes [[wikilinks]] for related concepts (e.g. [[Docker]], [[Kubernetes]])\n"
        f"- Cites sources as inline URLs or a References section\n"
        f"- Is factual, concise, and suitable for a personal knowledge base\n"
        # R7-10(c): steer downstream ingest classification → this becomes a `synthesis` page
        # (landing under wiki/synthesis/), not a generic concept/entity. The re-ingest analyze()
        # step reads this framing sentence.
        f"This is a SYNTHESIS of multiple web sources — a survey/overview page. When it is "
        f"ingested into the wiki it MUST be classified as page type 'synthesis'.\n"
        f"Output ONLY the markdown document, no preamble."
    )

    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)

    return "".join(chunks).strip()


async def _ingest_synthesis(
    run_id: uuid.UUID,
    vault_id: str,
    synthesis_md: str,
    topic: str,
) -> uuid.UUID | None:
    """
    Write synthesis to vault/raw/sources/ and re-enter the ingest_file seam (AQ-v0.5-3).

    ADR-0024 §6:
      1. Write to raw/sources/deep-research-<run_id>.md with valid frontmatter (I5).
      2. Call ingest_file(abs_path) — the SINGLE intake seam (ADR-0003).
         This runs the hash gate (I1), resolves the ingest provider, runs analyze→generate→
         validate (or CLI delegate), writes wiki pages, embeds, bumps data_version ONCE.
         F10 NEVER writes to pages/Qdrant directly and NEVER double-bumps (Do-NOT #5/#11).

    Returns the page_id from IngestResult, or None on error.
    """
    from app.config import settings as _settings
    from app.ingest.orchestrator import ingest_file

    rel = f"raw/sources/deep-research-{run_id}.md"
    abs_path = _settings.vault_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with valid frontmatter (I5 — must be Obsidian-compatible)
    full_content = _frontmatter_wrap(synthesis_md, topic, run_id)
    abs_path.write_text(full_content, encoding="utf-8")

    # ── Store synthesis_text on the run row before ingest ──────────────────────
    await _update_run_synthesis_text(run_id, synthesis_md)

    try:
        result = await ingest_file(abs_path)  # ← AQ-v0.5-3: NOT provider.generate()
        logger.info(
            "_ingest_synthesis: synthesis ingested page_id=%s run_id=%s",
            result.page_id,
            run_id,
        )
        return result.page_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_ingest_synthesis: ingest_file failed for run_id=%s: %s — synthesis written "
            "to raw/sources/ but not indexed",
            run_id,
            exc,
        )
        return None


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _create_run_row(
    *,
    run_id: uuid.UUID,
    vault_id: str,
    topic: str,
    max_iter: int,
    token_budget: int,
) -> DeepResearchRun:
    """INSERT a deep_research_runs row with status='running' and frozen bounds."""
    # Use str(run_id) so ORM INSERT works with both Postgres (UUID col) and
    # SQLite in-memory tests (String(36) variant).  UUID(as_uuid=True) on Postgres
    # accepts a string UUID value; with_variant(String(36), "sqlite") on SQLite too.
    async with get_session() as session:
        run = DeepResearchRun(
            id=str(run_id),
            vault_id=vault_id,
            topic=topic,
            status="running",
            max_iter=max_iter,
            token_budget=token_budget,
            iterations_used=0,
            queries_used=[],
            sources_fetched=0,
            converged=False,
            total_cost_usd=0,
            synthesis_text=None,
            synthesis_page_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
        )
        session.add(run)
        await session.flush()
        # Return a detached snapshot — the loop reads max_iter/token_budget from it.
        session.expunge(run)
        return run


async def _update_run_iterations(run_id: uuid.UUID, iteration: int) -> None:
    """Update iterations_used for live audit (AC-F10-2b)."""
    from sqlalchemy import update

    async with get_session() as session:
        await session.execute(
            update(DeepResearchRun)
            .where(DeepResearchRun.id == str(run_id))
            .values(iterations_used=iteration)
        )


async def _update_run_sources(run_id: uuid.UUID, sources_fetched: int) -> None:
    """Update sources_fetched count for live audit."""
    from sqlalchemy import update

    async with get_session() as session:
        await session.execute(
            update(DeepResearchRun)
            .where(DeepResearchRun.id == str(run_id))
            .values(sources_fetched=sources_fetched)
        )


async def _update_run_synthesis_text(run_id: uuid.UUID, synthesis_md: str) -> None:
    """Store synthesis_text on the run row (AC-F10-4c)."""
    from sqlalchemy import update

    async with get_session() as session:
        await session.execute(
            update(DeepResearchRun)
            .where(DeepResearchRun.id == str(run_id))
            .values(synthesis_text=synthesis_md)
        )


async def _finalize_run_row(
    *,
    run_id: uuid.UUID,
    status: str,
    iterations_used: int,
    sources_fetched: int,
    queries_used: list[str],
    total_cost_usd: float,
    synthesis_page_id: uuid.UUID | None,
    error_message: str | None,
) -> None:
    """Write the terminal run state (always called from finally — AC-F10-2b, Do-NOT #7)."""
    from sqlalchemy import update

    now = datetime.now(UTC)
    async with get_session() as session:
        await session.execute(
            update(DeepResearchRun)
            .where(DeepResearchRun.id == str(run_id))
            .values(
                status=status,
                iterations_used=iterations_used,
                sources_fetched=sources_fetched,
                queries_used=queries_used,
                converged=(status == "converged"),
                total_cost_usd=total_cost_usd,
                synthesis_page_id=synthesis_page_id,
                completed_at=now,
                error_message=error_message,
            )
        )


async def _insert_source_row(run_id: uuid.UUID, src: FetchedSource) -> None:
    """Insert one deep_research_sources row (AC-F10-6b, ADR-0024 §7.2)."""
    async with get_session() as session:
        source = DeepResearchSource(
            id=str(uuid.uuid4()),
            run_id=str(run_id),
            url=src.url,
            title=src.title,
            fetched_content_md=src.content_md,
            relevance_score=None,  # optional/best-effort in Phase 2
            iteration=src.iteration,
            created_at=datetime.now(UTC),
        )
        session.add(source)


# ── Provider resolution helper (I6) ───────────────────────────────────────────


async def _resolve_provider(vault_id: str) -> Any | None:
    """
    Resolve the InferenceProvider for operation='ingest' (ADR-0024 §1.1 / I6).

    Returns None when no provider_config row resolves (mechanical path, no AI calls).
    NEVER hardcodes a backend. NEVER uses isinstance/type-branch.
    """
    from app.ingest.provider import resolve_provider
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        logger.debug(
            "_resolve_provider: no provider_config for vault=%r — mechanical path", vault_id
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("_resolve_provider: DB error resolving provider: %s — mechanical path", exc)
        return None

    return resolve_provider(config_row)


# ── Text utilities ─────────────────────────────────────────────────────────────


def _frontmatter_wrap(synthesis_md: str, topic: str, run_id: uuid.UUID) -> str:
    """
    Wrap synthesis markdown with valid YAML frontmatter (I5 — Obsidian-compatible, K6).

    type: source is used because this is source material, not a finished wiki page.
    The downstream analyze→generate step of ingest_file will produce the final page(s).
    """
    fm = (
        "---\n"
        f"type: source\n"
        f'title: "Deep Research: {topic}"\n'
        f"sources:\n"
        f"  - deep-research-{run_id}\n"
        f'deep_research_run_id: "{run_id}"\n'
        "---\n\n"
    )
    return fm + synthesis_md


def _html_to_markdown(html: str) -> str:
    """
    Lightweight HTML→markdown reduction (ADR-0024 §4 — no LLM call, I6).

    Uses html2text if available (pip installable); otherwise does a basic tag-strip
    via re. The extractor is intentionally crude — the synthesis LLM step tolerates noise.
    """
    try:
        import html2text

        handler = html2text.HTML2Text()
        handler.ignore_links = False
        handler.ignore_images = True
        handler.body_width = 0  # no line wrapping
        return str(handler.handle(html))
    except ImportError:
        pass

    # Fallback: strip HTML tags with regex
    import re

    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _format_sources_for_prompt(collected: list[FetchedSource], *, max_chars: int) -> str:
    """
    Format collected sources as a compact text block for provider prompts.

    Caps total output at max_chars to avoid blowing the token budget.
    """
    parts: list[str] = []
    remaining = max_chars
    for src in collected:
        if remaining <= 0:
            break
        header = f"### {src.title}\nURL: {src.url}\n"
        body = src.content_md or "(fetch failed — no content)"
        entry = header + body[: max(0, remaining - len(header))] + "\n\n"
        parts.append(entry[:remaining])
        remaining -= len(entry)
    return "".join(parts)
