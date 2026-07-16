"""Bounded block-based orchestrated ingest loop (ADR-0076, nashsu/llm_wiki v0.6.3 ``autoIngest``).

The block twin of :func:`app.ingest.loop.run_orchestrated_loop`, kept in a SEPARATE module so the
JSON loop is untouched. Flow (llm_wiki ingest.ts:626-1326):

  1. ANALYSIS  — one ``provider.complete`` call producing a free-markdown analysis (NOT JSON).
  2. GENERATION loop — bounded by ``max_iter`` AND ``token_budget`` (I7). Each round emits FILE /
     REVIEW blocks; the FILE blocks are sanitized and validated with a RELAXED, block-specific
     validator (≥1 FILE block; every block has a non-empty title, a non-empty body, and a
     schema-routing-valid path). No JSON schema, no ``lang`` gate, no ``## Research queries`` gate.
     Empty output (0 FILE blocks) is a failure → retry with the errors appended. The last batch is
     kept even on non-convergence.
  3. REVIEW STAGE (conditional) — when the generation is large enough (``review_stage_min_chars``)
     or produced enough FILE blocks (``review_stage_min_file_blocks``), one extra ``complete`` call
     asks for high-signal REVIEW blocks. Inline REVIEW blocks already present in the generation are
     also collected. Deduped by ``(type, title)`` and RETURNED (not enqueued — that is WS-C).

Provider- and persistence-agnostic: it takes a bound provider + a run-scoped ``UsageAccumulator``
and returns a :class:`BlockLoopResult`. Writing pages is the pipeline's job (via
``app.ingest.block_writer``).

TODO(ADR-0076 follow-up): long-source chunked analysis (llm_wiki §1.6 ``analyzeLongSourceInChunks``)
is OUT OF SCOPE for this PR — the analysis is a single whole-source pass.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import frontmatter

from app.ingest import blocks as _blocks
from app.ingest import prompts as _prompts
from app.ingest import sanitize as _sanitize
from app.ingest.blocks import FileBlock, ReviewBlock
from app.ingest.loop import IngestCancelled
from app.ingest.provider.base import (
    InferenceProvider,
    ProviderEmptyOutput,
    ProviderTransientError,
    UsageAccumulator,
)
from app.wiki.schema import parse_page_type_routing, validate_page_routing

logger = logging.getLogger(__name__)

_ANALYSIS_MAX_TOKENS = 4096

# Bounded retry for TRANSIENT provider failures (I7): a rate-limit / overloaded / execution error
# on a complete() call is retried with exponential backoff instead of aborting the whole document.
_COMPLETE_MAX_ATTEMPTS = 3
_COMPLETE_BACKOFF_BASE_S = 2.0


@dataclass
class BlockLoopResult:
    """Outcome of the block loop (consumed by the pipeline's block branch)."""

    file_blocks: list[FileBlock] = field(default_factory=list)  # sanitized last batch
    analysis_text: str = ""
    review_blocks: list[ReviewBlock] = field(default_factory=list)
    converged: bool = False
    iterations: int = 0  # generate() attempts actually made (0..max_iter)
    stop_reason: str = "max_iter"  # "converged" | "max_iter" | "token_budget"


def _generation_max_tokens(max_context_chars: int) -> int:
    """Generation ``max_tokens`` tier by context window (llm_wiki ingest.ts:2427 — in CHARS)."""
    if max_context_chars >= 512_000:
        return 32_768
    if max_context_chars >= 256_000:
        return 24_576
    if max_context_chars >= 128_000:
        return 16_384
    return 8_192


def _augment_generation_user(user: str, errors: list[str]) -> str:
    """Append the previous attempt's validation errors to the generation user message (retry)."""
    block = "\n".join(f"- {e}" for e in errors)
    return (
        f"{user}\n\n"
        "# Validation errors from the previous attempt — FIX ALL of these:\n"
        f"{block}\n"
    )


async def _complete_with_retry(
    provider: InferenceProvider,
    system: str,
    user: str,
    *,
    max_tokens: int,
    accumulator: UsageAccumulator,
    token_budget: int,
    label: str,
    empty_ok: bool,
) -> str:
    """Call ``provider.complete()`` with bounded retry-with-backoff on a TRANSIENT failure (I7).

    - ``ProviderTransientError`` (rate-limit / overloaded / execution error) → retry up to
      ``_COMPLETE_MAX_ATTEMPTS`` with exponential backoff, but never past the run ``token_budget``.
    - ``ProviderEmptyOutput`` → if *empty_ok* (generation / review), return ``""`` so the loop's own
      zero-block augment-and-retry handles it; otherwise (analysis) re-raise.

    Retry/backoff orchestration lives in the loop, NOT in the provider (I6): providers only TYPE
    the failure. Previously a single empty/transient ``complete()`` aborted the whole document.
    """
    last_exc: ProviderTransientError | None = None
    for attempt in range(1, _COMPLETE_MAX_ATTEMPTS + 1):
        try:
            return await provider.complete(system, user, max_tokens=max_tokens)
        except ProviderEmptyOutput:
            if empty_ok:
                return ""
            raise
        except ProviderTransientError as exc:
            last_exc = exc
            if attempt >= _COMPLETE_MAX_ATTEMPTS or accumulator.total_tokens >= token_budget:
                raise
            delay = _COMPLETE_BACKOFF_BASE_S * (2 ** (attempt - 1))
            logger.warning(
                "block loop: %s transient provider error (attempt %d/%d) — backoff %.1fs: %s",
                label,
                attempt,
                _COMPLETE_MAX_ATTEMPTS,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None  # loop returns or raises; guard for type-checkers
    raise last_exc


def _validate_block_batch(file_blocks: list[FileBlock], routing: dict[str, str]) -> list[str]:
    """RELAXED, block-specific validator (ADR-0076). Returns errors (empty == valid).

    A batch is valid iff at least one FILE block parses AND every block has a non-empty
    frontmatter ``title``, a non-empty body, and a schema-routing-valid path
    (:func:`validate_page_routing`). NO ``lang`` requirement and NO ``## Research queries`` gate
    (both belong to the JSON loop's ``validate_pages``, not here).
    """
    if not file_blocks:
        return ["generation produced no FILE blocks (0 parsed) — output the ---FILE: blocks"]

    errors: list[str] = []
    for fb in file_blocks:
        # App-managed aggregates (index/log/overview) are maintained by the pipeline, not the
        # model: the writer deliberately DROPS any such block. The prompt still asks for a log
        # entry, so the model emits one — that is EXPECTED, never a validation failure. Skipping
        # them here prevents a never-converging retry loop (the model re-emits log.md each turn).
        _base = fb.path.rsplit("/", 1)[-1].lower()
        if _base in {"index.md", "log.md", "overview.md"}:
            continue
        prefix = f'FILE "{fb.path}"'
        try:
            post = frontmatter.loads(fb.content)
            meta = post.metadata
            body = post.content
        except Exception:  # noqa: BLE001 — malformed FM is a generation defect; flag + retry.
            errors.append(f"{prefix}: frontmatter could not be parsed")
            continue
        page_type = str(meta.get("type") or "").strip()
        title = str(meta.get("title") or "").strip()
        if not title:
            errors.append(f"{prefix}: frontmatter title is empty")
        if not body.strip():
            errors.append(f"{prefix}: page body is empty")
        ok, reason = validate_page_routing(page_type, fb.path, routing)
        if not ok:
            errors.append(f"{prefix}: {reason}")
    return errors


def _dedupe_reviews(reviews: list[ReviewBlock]) -> list[ReviewBlock]:
    """De-duplicate REVIEW blocks by ``(type, title)`` (order-preserving)."""
    seen: set[tuple[str, str]] = set()
    out: list[ReviewBlock] = []
    for rb in reviews:
        key = (rb.type, rb.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(rb)
    return out


async def run_block_loop(
    *,
    provider: InferenceProvider,
    accumulator: UsageAccumulator,
    source_text: str,
    purpose: str,
    schema: str,
    index: str,
    source_filename: str,
    origin_source: str,
    language_name: str | None,
    max_iter: int,
    token_budget: int,
    cancel_event: asyncio.Event | None = None,
    on_phase: Callable[[str], None] | None = None,
    overview: str = "",
    source_summary_path: str | None = None,
    max_context_chars: int = 204_800,
    review_stage_min_chars: int = 10_000,
    review_stage_min_file_blocks: int = 4,
) -> BlockLoopResult:
    """Run analysis → bounded generation → conditional review stage (ADR-0076).

    ``cancel_event`` is checked at each generation-loop BOUNDARY (never inside a provider call —
    ADR-0046 §3); on cancellation :class:`~app.ingest.loop.IngestCancelled` is raised for the
    pipeline's handler. ``token_budget`` is checked BEFORE each generation call (I7). Returns the
    last sanitized FILE-block batch with ``converged`` / ``stop_reason`` set.
    """
    provider.bind_accumulator(accumulator)
    routing = parse_page_type_routing(schema)

    # ── Stage 1: markdown analysis (single whole-source pass; chunking is a TODO) ──────
    if on_phase is not None:
        on_phase("analyzing")
    analysis_system = _prompts.build_analysis_prompt(
        purpose=purpose,
        index=index,
        source_content=source_text,
        schema=schema,
        language_name=language_name,
    )
    analysis_user = _prompts.build_analysis_user(
        source_identity=origin_source, source_context=source_text
    )
    analysis_text = await _complete_with_retry(
        provider,
        analysis_system,
        analysis_user,
        max_tokens=_ANALYSIS_MAX_TOKENS,
        accumulator=accumulator,
        token_budget=token_budget,
        label="analysis",
        empty_ok=False,
    )

    # ── Stage 2: bounded generation loop ──────────────────────────────────────────────
    gen_max_tokens = _generation_max_tokens(max_context_chars)
    generation_system = _prompts.build_generation_prompt(
        schema=schema,
        purpose=purpose,
        index=index,
        source_filename=source_filename,
        overview=overview,
        source_summary_path=source_summary_path,
        source_content=source_text,
        language_name=language_name,
    )

    file_blocks: list[FileBlock] = []
    generation_text = ""
    errors: list[str] = []
    converged = False
    iterations = 0
    stop_reason = "max_iter"

    for i in range(1, max_iter + 1):
        # Cooperative cancel at the loop boundary (ADR-0046 §3) — never inside a provider call.
        if cancel_event is not None and cancel_event.is_set():
            raise IngestCancelled(origin_source)

        # I7 bound #2: pre-call token-budget check (analysis already spent some budget).
        if accumulator.total_tokens >= token_budget:
            logger.info(
                "block loop: token_budget %d reached (%d tokens) before iter %d — stop",
                token_budget,
                accumulator.total_tokens,
                i,
            )
            stop_reason = "token_budget"
            break

        iterations = i
        if on_phase is not None:
            on_phase(f"generating ({i}/{max_iter})")

        generation_user = _prompts.build_generation_user(
            analysis=analysis_text, source_context=source_text
        )
        if errors:
            generation_user = _augment_generation_user(generation_user, errors)

        generation_text = await _complete_with_retry(
            provider,
            generation_system,
            generation_user,
            max_tokens=gen_max_tokens,
            accumulator=accumulator,
            token_budget=token_budget,
            label=f"generation({i}/{max_iter})",
            empty_ok=True,
        )

        parsed = _blocks.parse_file_blocks(generation_text)
        file_blocks = [
            FileBlock(path=b.path, content=_sanitize.sanitize_ingested_file_content(b.content))
            for b in parsed.files
        ]
        errors = _validate_block_batch(file_blocks, routing)
        if not errors:
            converged = True
            stop_reason = "converged"
            logger.info(
                "block loop: converged on iter %d/%d (%d files)", i, max_iter, len(file_blocks)
            )
            break

        logger.info(
            "block loop: iter %d/%d invalid (%d errors) — augment & retry", i, max_iter, len(errors)
        )

    if not converged:
        logger.warning(
            "block loop: stopped without convergence (reason=%s, iters=%d, tokens=%d)",
            stop_reason,
            iterations,
            accumulator.total_tokens,
        )

    # ── Stage 3: conditional dedicated review stage (llm_wiki shouldRunDedicatedReviewStage) ──
    review_blocks: list[ReviewBlock] = list(_blocks.parse_review_blocks(generation_text))
    should_review = (
        len(generation_text) >= review_stage_min_chars
        or len(file_blocks) >= review_stage_min_file_blocks
    )
    cancelled = cancel_event is not None and cancel_event.is_set()
    if should_review and not cancelled and accumulator.total_tokens < token_budget:
        if on_phase is not None:
            on_phase("reviewing")
        review_max_tokens = min(8_192, max(4_096, gen_max_tokens // 2))
        review_system = _prompts.build_review_stage_prompt(
            purpose=purpose,
            index=index,
            source_identity=origin_source,
            analysis=analysis_text,
            source_context=source_text,
            generation=generation_text,
            max_context_chars=max_context_chars,
            language_name=language_name,
        )
        try:
            review_text = await provider.complete(
                review_system,
                "Identify high-signal follow-up review items now. "
                "Output only REVIEW blocks, or nothing.",
                max_tokens=review_max_tokens,
            )
            review_blocks.extend(_blocks.parse_review_blocks(review_text))
        except Exception as exc:  # noqa: BLE001 — review stage is best-effort; keep inline blocks.
            logger.warning("block loop: dedicated review stage failed (non-fatal): %s", exc)

    return BlockLoopResult(
        file_blocks=file_blocks,
        analysis_text=analysis_text,
        review_blocks=_dedupe_reviews(review_blocks),
        converged=converged,
        iterations=iterations,
        stop_reason=stop_reason,
    )
