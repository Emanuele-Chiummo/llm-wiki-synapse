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

import logging
from dataclasses import dataclass

from pydantic import ValidationError

from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import Analysis, PageType, WikiPage

logger = logging.getLogger(__name__)


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
) -> LoopResult:
    """
    Run analyze-once → generate → validate → augment&retry, bounded by max_iter AND
    token_budget (I7). Pushes Usage to *accumulator* via the provider's out-of-band recording.

    Returns the last produced batch with converged/stop_reason set; the caller decides whether
    to persist (the architecture writes the last batch even on non-convergence so a
    source-summary page can still be guaranteed downstream — F3).
    """
    provider.bind_accumulator(accumulator)

    # analyze() ONCE per run (AQ-v0.2-1).
    analysis = await provider.analyze(source_text, vault_context)

    ctx = retrieval_context
    pages: list[WikiPage] = []
    converged = False
    iterations = 0
    stop_reason = "max_iter"

    for i in range(1, max_iter + 1):
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
        try:
            pages = await provider.generate(analysis, ctx)
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
    )
