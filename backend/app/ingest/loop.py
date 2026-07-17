"""
Orchestrated bounded ingest loop (I7, ADR-0007 §4/§5, ADR-0009).

Used for the NON-agentic providers (Local / API). The flow:

    analyze() ONCE  →  generate()  →  validate()
                         ▲                │ invalid
                         └── augment(ctx, errors) ── retry (bounded)

Bounds (BOTH enforced, ADR-0009 §1):
  • max_iter           — stop after at most N generate() attempts (default 3).
  • token_budget       — checked BEFORE each generate() call; stop if the run accumulator has
                         already reached/exceeded the budget (default 60k orchestrated).
The loop exits on whichever bound hits first; on non-convergence it stops CLEANLY at max_iter
with converged=False — never an overrun.

This module is provider- and persistence-agnostic: it takes a bound provider + a run-scoped
UsageAccumulator and returns a LoopResult. Writing pages + the ingest_runs row + the
cost-anomaly check are the orchestrator's job (ADR-0009 §3). The shared validator
(`validate_pages`) is the ONE validator the MCP write_page tool also reuses (ADR-0010 §2).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import Analysis, PageType, WikiPage

logger = logging.getLogger(__name__)

_QUERY_RESEARCH_HEADING_RE = re.compile(
    r"(?im)^#{1,6}\s*(?:research|search|retrieval)\s+quer(?:y|ies)\s*$"
)
_QUERY_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_QUERY_GENERIC_TOKENS = frozenset(
    {
        "a",
        "altro",
        "ancora",
        "about",
        "anything",
        "can",
        "cercare",
        "conoscere",
        "cosa",
        "darmi",
        "del",
        "della",
        "details",
        "dettagli",
        "di",
        "dimmi",
        "domanda",
        "dovrei",
        "else",
        "explain",
        "find",
        "fornire",
        "give",
        "i",
        "il",
        "information",
        "informazioni",
        "know",
        "la",
        "learn",
        "le",
        "lo",
        "me",
        "mi",
        "more",
        "out",
        "per",
        "più",
        "please",
        "provide",
        "puoi",
        "qualcosa",
        "question",
        "questo",
        "research",
        "ricerca",
        "sapere",
        "search",
        "should",
        "scoprire",
        "spiega",
        "something",
        "su",
        "tell",
        "the",
        "this",
        "topic",
        "un",
        "una",
        "vorrei",
        "what",
        "you",
    }
)


def _specific_query_terms(value: str) -> set[str]:
    """Return concrete lexical terms usable as a source-grounding proxy (EN/IT UI locales)."""
    normalized = re.sub(r"\W+", " ", value).casefold().strip()
    return {
        token
        for token in normalized.split()
        if len(token) >= 3
        and token not in _QUERY_GENERIC_TOKENS
        and (not token.isdigit() or len(token) >= 4)
    }


# ── Cooperative cancellation exception (ADR-0046 §3) ─────────────────────────


class IngestCancelled(Exception):
    """
    Raised at the top of each orchestrated-loop iteration when the run's
    cancel_event is set (ADR-0046 §3 / I6: never raised inside a provider call).

    Carries the origin_source for logging.  Caught by run_ingest_pipeline in
    orchestrator.py, which performs cascade cleanup then finalises the run as
    status="cancelled".
    """

    def __init__(self, origin_source: str) -> None:
        self.origin_source = origin_source
        super().__init__(f"ingest cancelled: {origin_source}")


# ── Shared validator (ADR-0007 §5 / ADR-0010 §2 — ONE validator) ────────────────


def validate_pages(pages: list[WikiPage], origin_source: str) -> list[str]:
    """
    Return a list of human-readable validation errors for *pages* (empty list == valid).

    A batch is INVALID (triggers a retry) if ANY page fails (AQ-v0.2-7):
      • type in the PageType enum;
      • non-empty title;
      • non-empty sources[] that CONTAINS the origin source's relative path (F3 traceability);
      • non-empty lang;
      • non-empty content.
    Dangling wikilinks do NOT invalidate (K5 stores them with dangling=True).

    Because WikiPage is a Pydantic model, most rules are already enforced at parse time; this
    function re-checks them defensively AND adds the business rule "origin path ∈ sources[]"
    that Pydantic alone cannot express. It is the SAME validator the MCP write_page tool calls.
    """
    errors: list[str] = []
    if not pages:
        return ["batch is empty: at least one page is required"]

    for i, page in enumerate(pages):
        prefix = f"page[{i}] ({page.title!r})"
        if not isinstance(page.type, PageType):
            errors.append(f"{prefix}: type {page.type!r} is not a valid PageType")
        if not page.title.strip():
            errors.append(f"{prefix}: title is empty")
        if not page.content.strip():
            errors.append(f"{prefix}: content is empty")
        if page.type is PageType.QUERY:
            heading = _QUERY_RESEARCH_HEADING_RE.search(page.content)
            if heading is None:
                errors.append(f"{prefix}: query page must include a '## Research queries' section")
            else:
                section = page.content[heading.end() :]
                next_heading = re.search(r"(?m)^#{1,6}\s+", section)
                if next_heading is not None:
                    section = section[: next_heading.start()]
                title_norm = re.sub(r"\W+", " ", page.title).casefold().strip()
                # The prose before the query list is the page's source-backed question context.
                # A retrieval query must be lexically anchored to that context and then add a
                # constraint/evidence term. This rejects generic paraphrases without pretending
                # that a finite placeholder dictionary can prove semantic grounding.
                context_terms = _specific_query_terms(
                    f"{page.title}\n{page.content[: heading.start()]}"
                )
                contextual_queries = []
                for candidate in _QUERY_LIST_ITEM_RE.findall(section):
                    candidate_norm = re.sub(r"\W+", " ", candidate).casefold().strip()
                    tokens = candidate_norm.split()
                    candidate_terms = _specific_query_terms(candidate)
                    anchor_terms = candidate_terms & context_terms
                    novel_terms = candidate_terms - context_terms
                    if (
                        candidate_norm == title_norm
                        or len(tokens) < 3
                        or len(candidate_terms) < 3
                        or len(anchor_terms) < 2
                        or not novel_terms
                    ):
                        continue
                    contextual_queries.append(candidate)
                if len(contextual_queries) < 2:
                    errors.append(
                        f"{prefix}: query page requires at least two contextual retrieval "
                        "queries beyond the title"
                    )
        fm = page.frontmatter
        if not fm.sources:
            errors.append(f"{prefix}: frontmatter.sources[] is empty (F3 traceability)")
        elif origin_source and origin_source not in fm.sources:
            errors.append(
                f"{prefix}: frontmatter.sources[] must include the origin path "
                f"{origin_source!r} (F3 traceability)"
            )
        if not fm.lang.strip():
            errors.append(f"{prefix}: frontmatter.lang is empty")
    return errors


def augment_context(retrieval_context: str, errors: list[str]) -> str:
    """
    Append a validation-error block to the retrieval context for the next generate() retry
    (ADR-0007 §4 — augmentation targets generation, not re-analysis).
    """
    block = "\n".join(f"- {e}" for e in errors)
    return (
        f"{retrieval_context}\n\n"
        "# Validation errors from the previous attempt — FIX ALL of these:\n"
        f"{block}\n"
    )


# ── Loop result ──────────────────────────────────────────────────────────────────


@dataclass
class LoopResult:
    """Outcome of the orchestrated loop (consumed by the orchestrator)."""

    pages: list[WikiPage]
    analysis: Analysis
    converged: bool
    iterations: int  # generate() attempts actually made (1..max_iter)
    stop_reason: str  # "converged" | "max_iter" | "token_budget"
    # 1.9.1 W5 (NC-1): last batch's validation errors (empty when converged) + token accounting
    # at stop time, mirroring app.ingest.block_loop.BlockLoopResult so the pipeline can persist
    # ingest_runs.diagnostics identically for both loop shapes (no parallel channel).
    last_errors: list[str] = field(default_factory=list)
    tokens_used: int = 0
    token_budget: int = 0

    def diagnostics(self) -> dict[str, object]:
        """Build the ``ingest_runs.diagnostics`` JSON payload (1.9.1 W5, NC-1)."""
        return {
            "stop_reason": self.stop_reason,
            "iterations": self.iterations,
            "last_errors": self.last_errors,
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
        }


# ── The bounded loop ─────────────────────────────────────────────────────────────


async def run_orchestrated_loop(
    *,
    provider: InferenceProvider,
    accumulator: UsageAccumulator,
    source_text: str,
    vault_context: str,
    retrieval_context: str,
    origin_source: str,
    max_iter: int,
    token_budget: int,
    cancel_event: asyncio.Event | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> LoopResult:
    """
    Run analyze-once → generate → validate → augment&retry, bounded by max_iter AND
    token_budget (I7). Pushes Usage to *accumulator* via the provider's out-of-band recording.

    cancel_event: optional asyncio.Event set by the queue manager when the user requests
    cancellation (ADR-0046 §3). Checked at the TOP of each iteration — NEVER inside a
    provider call — so at most one in-flight generate() completes before abort. Raises
    IngestCancelled(origin_source) on cancellation; the orchestrator catches it and
    performs cascade cleanup.

    Returns the last produced batch with converged/stop_reason set; the caller decides whether
    to persist (the architecture writes the last batch even on non-convergence so a
    source-summary page can still be guaranteed downstream — F3).
    """
    provider.bind_accumulator(accumulator)

    # analyze() ONCE per run (AQ-v0.2-1) — except a LONG source is analyzed per bounded chunk and
    # the per-chunk Analysis objects are merged (Feature 1, ADR-0063 §3). analyze_source() routes
    # every call through provider.analyze() (I6), is bounded by ingest_long_source_max_chunks (I7),
    # and degrades to the single whole-source call under the threshold or on total chunk failure.
    from app.ingest.long_source import analyze_source

    if on_phase is not None:
        on_phase("analyzing")
    analysis = await analyze_source(provider, source_text, vault_context)

    ctx = retrieval_context
    pages: list[WikiPage] = []
    converged = False
    iterations = 0
    stop_reason = "max_iter"
    # 1.9.1 W5 (NC-1): initialized here (not just inside the loop body) so a token_budget /
    # max_iter=0 stop BEFORE any iteration runs still returns a well-defined last_errors (empty
    # rather than "no batch was ever validated" raising a NameError).
    errors: list[str] = []

    for i in range(1, max_iter + 1):
        # ── Cooperative cancel check (ADR-0046 §3 / I7) ──────────────────────────
        # Checked at the loop BOUNDARY, before any provider call, so we never tear a
        # half-written page (I1). At most one generate() completes after the event is set.
        if cancel_event is not None and cancel_event.is_set():
            stop_reason = "cancelled"
            raise IngestCancelled(origin_source)

        # I7 bound #2: pre-call token-budget check (ADR-0009 §1 — never make a call we can't
        # afford). Checked before generate() because analyze() already spent some budget.
        if accumulator.total_tokens >= token_budget:
            logger.info(
                "orchestrated loop: token_budget %d reached (%d tokens) before iter %d — stop",
                token_budget,
                accumulator.total_tokens,
                i,
            )
            stop_reason = "token_budget"
            break

        iterations = i
        if on_phase is not None:
            on_phase(f"generating ({i}/{max_iter})")
        try:
            # D1 (ADR-0063 §9): thread the run's source_text into generation so pages are written
            # from the source, not only the lossy Analysis summary. retrieval_context stays "" for
            # ingest (llm_wiki's "Source Context" IS the source text, not RAG); the builder
            # budget-trims the source (I7).
            pages = await provider.generate(analysis, ctx, source_text)
            if on_phase is not None:
                on_phase("validating")
            errors = validate_pages(pages, origin_source)
        except ValidationError as exc:
            # Malformed provider JSON → treat as a generation defect; retry with errors.
            errors = [f"schema validation failed: {exc.errors()!r}"]
            pages = []

        if not errors:
            converged = True
            stop_reason = "converged"
            logger.info("orchestrated loop: converged on iter %d/%d", i, max_iter)
            break

        logger.info(
            "orchestrated loop: iter %d/%d invalid (%d errors) — augment & retry",
            i,
            max_iter,
            len(errors),
        )
        ctx = augment_context(ctx, errors)

    if not converged:
        logger.warning(
            "orchestrated loop: stopped without convergence (reason=%s, iters=%d, tokens=%d)",
            stop_reason,
            iterations,
            accumulator.total_tokens,
        )

    return LoopResult(
        pages=pages,
        analysis=analysis,
        converged=converged,
        iterations=iterations,
        stop_reason=stop_reason,
        last_errors=errors if not converged else [],
        tokens_used=accumulator.total_tokens,
        token_budget=token_budget,
    )
