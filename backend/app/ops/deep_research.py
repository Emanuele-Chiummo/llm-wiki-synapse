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

from app.db import get_session
from app.ingest.provider.base import UsageAccumulator
from app.models import DeepResearchRun, DeepResearchSource
from app.ops._llm import resolve_operation_provider
from app.ops.searxng import SearchHit, _semaphore
from app.ops.web_search import web_search_many
from app.security_net import SSRFError, safe_fetch

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
class OptimizedTopic:
    """
    Return value of optimize_topic (B5/D3 — pre-run topic optimization + confirm surface).

    optimized_topic: a domain-specific rephrasing of the seed topic, steered by the vault's
    overview.md + purpose.md. queries: 3..5 web-search-optimized SearXNG query strings.
    On the no-provider / degraded path both fall back to the seed topic (never an exception).
    """

    optimized_topic: str
    queries: list[str]


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
    _resolved = await resolve_operation_provider(vault_id)
    provider = _resolved[0] if _resolved is not None else None

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
            # v1.3.3: one unpersistable source must never fail the whole run
            # (Do-NOT #9 extended to the persistence step).
            for src in new_sources:
                try:
                    await _insert_source_row(run_id, src)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "deep_research: failed to persist source %s: %s — continuing",
                        src.url,
                        exc,
                    )

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
        # GUARD: only synthesize + ingest when at least one source was collected.
        # With zero sources the synthesis prompt degrades into a conversational
        # non-answer that must NOT be ingested as a wiki page (would create noise).
        if status in ("converged", "max_iter_reached", "budget_exhausted"):
            if collected:
                synthesis_md = await _synthesize(provider, topic, collected)
                synthesis_page_id = await _ingest_synthesis(run_id, vault_id, synthesis_md, topic)
            else:
                logger.info(
                    "deep_research run_id=%s: 0 sources collected — skipping synthesis/ingest "
                    "(no wiki page created; topic=%r)",
                    run_id,
                    topic,
                )

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


# ── Pre-run topic optimization (B5/D3 — read overview+purpose → optimized topic + queries) ──

# Cap the vault-context excerpt fed into the optimize prompt so a huge overview.md cannot
# blow the small optimize token budget (I7). The prompt hint carries the hard budget, the
# excerpt cap is a mechanical guard applied before the single call.
_OPTIMIZE_CONTEXT_MAX_CHARS: int = 6_000
# Bounds on the returned query list (B5/D3 requires 3..5 web-search-optimized queries).
_OPTIMIZE_MIN_QUERIES: int = 3
_OPTIMIZE_MAX_QUERIES: int = 5


def _load_research_vault_context() -> str:
    """
    Assemble the deep-research pre-run context: vault overview.md + purpose.md content.

    Used by optimize_topic to steer the topic rephrasing toward the vault's domain (B5/D3).
    Missing files → skipped section (tolerant, TOCTOU-safe: read without a prior exists()
    check so a file removed between check and read is silently ignored — same discipline as
    orchestrator._load_vault_context). Total excerpt capped at _OPTIMIZE_CONTEXT_MAX_CHARS (I7).

    NOTE: this is deep-research's OWN context surface — it reads overview.md (the auto-maintained
    big-picture note) which the ingest orchestrator's _load_vault_context deliberately does NOT
    (that one feeds schema.md for page generation). Kept local to this module rather than
    importing the ingest helper to avoid coupling the two context contracts.
    """
    from app.config import settings

    parts: list[str] = []
    # overview.md lives under wiki/, purpose.md at the vault root.
    candidates = (
        ("overview.md", settings.wiki_dir / "overview.md"),
        ("purpose.md", settings.vault_root / "purpose.md"),
    )
    for name, path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            continue
        if text:
            parts.append(f"# {name}\n{text}")

    joined = "\n\n".join(parts)
    if len(joined) > _OPTIMIZE_CONTEXT_MAX_CHARS:
        joined = joined[:_OPTIMIZE_CONTEXT_MAX_CHARS]
    return joined


def _naive_optimized(topic: str) -> OptimizedTopic:
    """
    Degraded fallback for optimize_topic (no provider / timeout / error / empty response).

    Echoes the seed topic as the optimized topic and returns it as the single query, so the
    UI confirm dialog still prefills and the user can edit + run offline (never a 500).
    """
    clean = topic.strip()
    return OptimizedTopic(optimized_topic=clean, queries=[clean] if clean else [topic])


def _parse_optimized_response(raw: str, topic: str) -> OptimizedTopic:
    """
    Parse the optimize_topic provider response into an OptimizedTopic.

    Expected shape (line-oriented, tolerant):
        TOPIC: <rephrased domain-specific topic>
        QUERIES:
        <query 1>
        <query 2>
        ...
    Falls back gracefully: a missing TOPIC line keeps the seed topic; queries are clamped to
    [_OPTIMIZE_MIN_QUERIES.._OPTIMIZE_MAX_QUERIES] (padding with the topic if the model returned
    too few). Never raises — a garbled response degrades to _naive_optimized.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return _naive_optimized(topic)

    optimized = topic.strip()
    queries: list[str] = []
    in_queries = False
    for ln in lines:
        upper = ln.upper()
        if upper.startswith("TOPIC:"):
            candidate = ln.split(":", 1)[1].strip()
            if candidate:
                optimized = candidate
            in_queries = False
            continue
        if upper.startswith("QUERIES:"):
            in_queries = True
            # a query may share the QUERIES: line (e.g. "QUERIES: foo") — capture it
            tail = ln.split(":", 1)[1].strip()
            if tail:
                queries.append(tail)
            continue
        if in_queries:
            # strip common list markers the model may add despite instructions
            queries.append(ln.lstrip("-*0123456789. ").strip())

    # If the model ignored the QUERIES: marker entirely, treat every non-TOPIC line as a query.
    if not queries:
        queries = [
            ln.lstrip("-*0123456789. ").strip()
            for ln in lines
            if not ln.upper().startswith("TOPIC:")
        ]

    queries = [q for q in queries if q][:_OPTIMIZE_MAX_QUERIES]
    if not queries:
        return OptimizedTopic(optimized_topic=optimized or topic.strip(), queries=[topic.strip()])

    # Pad to the minimum with the (optimized) topic so the dialog always has a usable seed set.
    while len(queries) < _OPTIMIZE_MIN_QUERIES:
        queries.append(optimized or topic.strip())

    return OptimizedTopic(optimized_topic=optimized or topic.strip(), queries=queries)


async def optimize_topic(*, vault_id: str, topic: str) -> OptimizedTopic:
    """
    ONE bounded provider call that rephrases a seed *topic* into a domain-specific research
    topic + 3..5 web-search-optimized queries, steered by the vault overview.md + purpose.md
    (B5/D3 — the pre-run "optimize + confirm" surface for Graph-Insight-triggered research).

    Bounds (I7): a SINGLE provider.chat() turn wrapped in asyncio.wait_for; token budget surfaced
    as a prompt hint; total_cost_usd read from the run-scoped accumulator and logged. NO loop.

    Provider-neutral (I6): rides the resolved InferenceProvider.chat() seam — no hardcoded backend
    or model. When NO provider is configured (or the call times out / errors / returns garbage) it
    degrades to _naive_optimized(topic) — the caller returns 200 with the seed echoed so the UI
    confirm dialog still works offline. This function NEVER raises for provider issues.

    NOTE: no web call here (I9 not engaged) — optimization is LLM-only; the actual SearXNG run
    happens later via run_deep_research when the user confirms the (edited) topic.
    """
    from app.config import settings

    seed = topic.strip()
    if not seed:
        return _naive_optimized(topic)

    _resolved = await resolve_operation_provider(vault_id)
    provider = _resolved[0] if _resolved is not None else None
    if provider is None:
        logger.info(
            "optimize_topic: no provider configured for vault=%r — naive fallback (topic=%r)",
            vault_id,
            seed,
        )
        return _naive_optimized(topic)

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    token_budget = int(settings.deep_research_optimize_token_budget)
    timeout_s = float(settings.deep_research_optimize_timeout_seconds)
    vault_context = _load_research_vault_context()
    context_block = (
        f"Vault context (goal, scope, existing coverage):\n{vault_context}\n\n"
        if vault_context
        else ""
    )

    instruction = (
        "You optimize a seed research topic for a self-organizing knowledge base before a "
        "web-research run. Read the vault context (if any) to make the topic and queries "
        "SPECIFIC to this vault's domain, scope, and open questions.\n\n"
        f"{context_block}"
        f"Seed topic: {seed}\n\n"
        "Produce:\n"
        "1. A single rephrased, domain-specific research topic (concise, one line).\n"
        f"2. Between {_OPTIMIZE_MIN_QUERIES} and {_OPTIMIZE_MAX_QUERIES} focused web-search "
        "queries (plain search strings suitable for SearXNG — no markdown, no numbering) that "
        "together cover complementary facets of the topic.\n\n"
        f"Aim to stay within roughly {token_budget} tokens.\n"
        "Respond in EXACTLY this format:\n"
        "TOPIC: <rephrased topic>\n"
        "QUERIES:\n"
        "<query 1>\n"
        "<query 2>\n"
        "<query 3>"
    )

    async def _collect() -> str:
        from app.ingest.schemas import Message

        chunks: list[str] = []
        async for chunk in await provider.chat(
            messages=[Message(role="user", content=instruction)],
            retrieval_context="",
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    result: OptimizedTopic
    try:
        raw = await asyncio.wait_for(_collect(), timeout=timeout_s)
        result = _parse_optimized_response(raw, topic)
    except TimeoutError:
        logger.warning(
            "optimize_topic: provider call timed out after %.1fs — naive fallback (topic=%r)",
            timeout_s,
            seed,
        )
        result = _naive_optimized(topic)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "optimize_topic: provider call failed (%s) — naive fallback (topic=%r)", exc, seed
        )
        result = _naive_optimized(topic)

    # ── Cost logging (I7 — single bounded call, cost recorded out of band) ─────────
    total_cost_usd = round(accumulator.total_cost_usd, 4)
    logger.info(
        "optimize_topic: vault=%r topic=%r → optimized=%r queries=%d cost_usd=%.4f",
        vault_id,
        seed,
        result.optimized_topic,
        len(result.queries),
        total_cost_usd,
    )
    if total_cost_usd > COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: optimize_topic vault=%r total_cost_usd=%.4f exceeds $%.2f (topic=%r) "
            "— investigate misconfiguration",
            vault_id,
            total_cost_usd,
            COST_ANOMALY_THRESHOLD_USD,
            seed,
        )

    return result


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

    from app.config import settings
    from app.ingest.schemas import Message

    async def _collect() -> str:
        chunks: list[str] = []
        async for chunk in await provider.chat(
            messages=[Message(role="user", content=instruction)],
            retrieval_context="",
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    # I7: bound the provider turn so a hung backend can't wedge the run forever.
    try:
        raw = await asyncio.wait_for(
            _collect(), timeout=float(settings.deep_research_provider_timeout_seconds)
        )
    except TimeoutError:
        logger.warning(
            "_generate_queries: provider timed out after %.1fs — falling back to topic",
            settings.deep_research_provider_timeout_seconds,
        )
        return [topic]

    queries = [line.strip() for line in raw.splitlines() if line.strip()]
    queries = queries[:max_queries]
    if not queries:
        queries = [topic]
    return queries


async def _search_searxng(queries: list[str]) -> list[SearchHit]:
    """
    Execute queries via the selected web-search backend, concurrency bounded by the shared
    module semaphore (I7).

    Routes through ops/web_search.web_search_many — the single web-search dispatcher (ADR-0070).
    SearXNG is the default backend; the alternatives are opt-in, off by default (ADR-0066).
    Bounds (max_queries + semaphore) and URL-dedup are unchanged.
    """
    return await web_search_many(queries)


# ── Fetched-body handling (v1.3.3) ────────────────────────────────────────────
# SearXNG results are often PDFs or other binaries. Storing resp.text for those
# persisted raw bytes (incl. NUL 0x00) into a Postgres text column and failed the
# whole run with CharacterNotInRepertoireError.

# Max PDF body routed to the extractor (I7 — SearXNG can return arbitrary files).
_PDF_MAX_BYTES = 15 * 1024 * 1024

# Content types treated as text and eligible for HTML→markdown extraction.
_TEXTY_CONTENT_TYPES = frozenset(
    {
        "",  # missing header: fall through to the NUL-sanitized text path
        "application/xhtml+xml",
        "application/xml",
        "application/json",
        "application/rss+xml",
        "application/atom+xml",
    }
)


def _sanitize_db_text(text: str) -> str:
    """Strip NUL bytes — Postgres TEXT/VARCHAR reject 0x00 in UTF-8 (v1.3.3)."""
    return text.replace("\x00", "")


def _is_texty_content_type(content_type: str) -> bool:
    """True when the (bare, lower-cased) content type is safe to treat as text."""
    return content_type.startswith("text/") or content_type in _TEXTY_CONTENT_TYPES


async def _extract_pdf_body(body: bytes, url: str) -> str | None:
    """
    Extract text from a fetched PDF body via the ingest extractor seam
    (Marker when configured, pypdf fallback — ADR-0051). Runs in a thread:
    pypdf parsing is CPU-bound and must not block the event loop (I2 discipline).
    Returns None on any failure — the source is kept with content_md=None
    (Do-NOT #9: fetch failures never kill the run).
    """
    if len(body) > _PDF_MAX_BYTES:
        logger.info(
            "_extract_pdf_body: %s is %d bytes (> %d cap) — skipping",
            url,
            len(body),
            _PDF_MAX_BYTES,
        )
        return None

    def _run() -> str | None:
        import os
        import tempfile

        from app.ingest.extract import extract_text

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="synapse-dr-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            return extract_text(tmp_path)
        except Exception as exc:  # noqa: BLE001
            logger.info("_extract_pdf_body: extraction failed for %s: %s", url, exc)
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return await asyncio.to_thread(_run)


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
                # safe_fetch validates scheme/host (incl. redirect hops) before
                # connecting — guards against SSRF on SearXNG result URLs (R13-9/B2).
                resp = await safe_fetch(
                    hit.url,
                    headers={"User-Agent": "Synapse/0.5 DeepResearch"},
                )
                if resp.status_code == 200:
                    # v1.3.3: dispatch on content type — SearXNG results are often
                    # PDFs/binaries, and resp.text for those is NUL-ridden mojibake
                    # that Postgres rejects (0x00 in UTF-8) killing the whole run.
                    content_type = (
                        resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    body = resp.content
                    if content_type == "application/pdf" or body[:5] == b"%PDF-":
                        extracted = await _extract_pdf_body(body, hit.url)
                        if extracted:
                            content_md = extracted[: _fetch_max_chars()]
                    elif _is_texty_content_type(content_type):
                        content_md = _html_to_markdown(resp.text)[: _fetch_max_chars()]
                    else:
                        logger.info(
                            "_fetch_and_extract: skipping non-text content-type %r for %s",
                            content_type,
                            hit.url,
                        )
                    if content_md is not None:
                        content_md = _sanitize_db_text(content_md)
                else:
                    logger.debug(
                        "_fetch_and_extract: HTTP %d for %s",
                        resp.status_code,
                        hit.url,
                    )
            except SSRFError as exc:
                # Private/blocked URL — log at INFO so admins can see blocked fetches
                logger.info("_fetch_and_extract: SSRF guard blocked %s: %s", hit.url, exc)
            except Exception as exc:  # noqa: BLE001
                logger.debug("_fetch_and_extract: fetch failed for %s: %s", hit.url, exc)

            return FetchedSource(
                url=hit.url,
                # Titles come from SearXNG results — sanitize them too (v1.3.3)
                title=_sanitize_db_text(hit.title) if hit.title else hit.title,
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

    from app.config import settings
    from app.ingest.schemas import Message

    async def _collect() -> str:
        chunks: list[str] = []
        async for chunk in await provider.chat(
            messages=[Message(role="user", content=instruction)],
            retrieval_context="",
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    # I7: bound the provider turn. On timeout stay conservative (insufficient) — the
    # bounded max_iter loop still terminates, it never silently converges on a hang.
    try:
        raw = await asyncio.wait_for(
            _collect(), timeout=float(settings.deep_research_provider_timeout_seconds)
        )
    except TimeoutError:
        logger.warning(
            "_assess_sufficiency: provider timed out after %.1fs — treating as insufficient",
            settings.deep_research_provider_timeout_seconds,
        )
        return Sufficiency(sufficient=False, gaps=["insufficient (provider timeout)"])

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

    from app.config import settings
    from app.ingest.schemas import Message

    async def _collect() -> str:
        chunks: list[str] = []
        async for chunk in await provider.chat(
            messages=[Message(role="user", content=instruction)],
            retrieval_context="",
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    # I7: bound the synthesis turn. On timeout, degrade to the same snippet-assembled
    # fallback used when no provider is configured — a run always produces *something*.
    try:
        return await asyncio.wait_for(
            _collect(), timeout=float(settings.deep_research_provider_timeout_seconds)
        )
    except TimeoutError:
        logger.warning(
            "_synthesize: provider timed out after %.1fs — assembling snippet fallback",
            settings.deep_research_provider_timeout_seconds,
        )
        parts = [f"# {topic}\n\n*Synthesized from web research (provider timed out).*\n"]
        for src in collected:
            if src.content_md:
                parts.append(f"\n## {src.title}\n\nSource: {src.url}\n\n{src.content_md[:2000]}\n")
        return "\n".join(parts)


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
            # Defense in depth (v1.3.3): NUL bytes are stripped at fetch time,
            # but this is the last line before Postgres — never trust upstream.
            title=_sanitize_db_text(src.title) if src.title else src.title,
            fetched_content_md=(
                _sanitize_db_text(src.content_md) if src.content_md else src.content_md
            ),
            relevance_score=None,  # optional/best-effort in Phase 2
            iteration=src.iteration,
            created_at=datetime.now(UTC),
        )
        session.add(source)


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
