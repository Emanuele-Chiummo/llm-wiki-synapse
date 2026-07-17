"""
F9 HITL Review Queue — purpose.md / schema.md co-evolution suggestions (BE-ARCH-2 package split).

R9-3 (purpose.md scope-drift) and R9-4 (schema.md convention co-evolution, K6) share IDENTICAL
architecture: a single bounded provider call compares newly-ingested content against a vault
governance file; on drift/new-convention, ONE ReviewItem is enqueued whose `rationale` carries
both the human-readable "why" and the exact markdown block to append on approve. Approve routes
through the shared ``_apply_suggestion_to_file`` helper (parameterized by target file) — NOT a
wiki-page write.

MONKEYPATCH-COMPAT NOTE (BE-ARCH-2): generate_purpose_suggestion / generate_schema_suggestion
resolve their provider/queue seams via a DEFERRED ``from app.ops.review import X`` at call time
(instead of a static top-of-file import), so ``patch("app.ops.review.resolve_operation_provider",
...)`` — written against the pre-split monolithic module — keeps working unchanged. See
``propose.py``'s module docstring for the full rationale.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app import db as _db
from app import runtime_state
from app.config import settings
from app.ingest.schemas import PageType
from app.models import Page, ReviewItem, VaultState
from app.ops.review.prompts import (
    _build_purpose_drift_instruction,
    _build_schema_pattern_instruction,
    _parse_purpose_drift,
    _parse_schema_pattern,
)
from app.ops.review.queue import _content_key

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis

logger = logging.getLogger(__name__)

# ── R9-3 (v0.9): purpose.md drift suggestion ─────────────────────────────────────
# The `rationale` column carries BOTH the human-readable "why" AND the exact markdown block
# to append to purpose.md on approve. ADR-0034 §3.1: `resolution` is a small closed enum
# (created|skipped|…, NULL while pending) — the WRONG place for a diff. `rationale` is the
# card body shown to the human, so it is the clean fit. The apply step splits on this marker;
# everything after it is appended VERBATIM to purpose.md.
_PURPOSE_ADDITION_MARKER = "\n\n--- SUGGESTED purpose.md ADDITION ---\n\n"
_PURPOSE_SUGGESTION_TYPE = "purpose-suggestion"

# ── R9-4 (v0.9): schema.md co-evolution (K6, beyond llm_wiki) ────────────────────
# Same architecture as R9-3: the `rationale` column carries BOTH the human-readable "why" AND
# the exact markdown rule block to append to schema.md on approve. The apply step splits on this
# marker; everything after it is appended VERBATIM to schema.md. A DISTINCT marker string (vs.
# the purpose one) is used so the shared apply helper is unambiguous about which file it targets
# and so a future migration could tell the two apart.
_SCHEMA_ADDITION_MARKER = "\n\n--- SUGGESTED schema.md ADDITION ---\n\n"
_SCHEMA_SUGGESTION_TYPE = "schema-suggestion"


async def generate_purpose_suggestion(
    *,
    vault_id: str,
    analysis: Analysis | None,
    written_pages: list[Page],
    origin_source: str,
) -> ReviewItem | None:
    """
    Post-ingest scope-drift check (R9-3). Compare this run's analysis topics/summary against
    the vault purpose.md; if the model judges scope drift (a new recurring theme not covered by
    purpose), emit ONE `purpose-suggestion` ReviewItem. Called fire-and-forget from the
    orchestrator — the caller wraps this in try/except; a failure here NEVER breaks ingest (I7).

    BOUNDS (I7 / R9-3 AC "bounded provider call max_tokens 300, no retry"):
      - Exactly ONE provider.chat() call, no loop, no retry.
      - max_tokens = PURPOSE_SUGGESTION_MAX_TOKENS (300) enforced at the call site.
      - asyncio.wait_for(PURPOSE_SUGGESTION_TIMEOUT_SECONDS).
      - Cost logged to the run ledger via the bound UsageAccumulator (total_cost_usd).
      - On no-provider / timeout / any error / empty / in-scope verdict → return None.

    THROTTLE (R9-3):
      1. Skip if a `purpose-suggestion` is already pending for the vault (max 1 pending at a
         time — no queue spam).
      2. Fire only when ≥ PURPOSE_SUGGESTION_MIN_SOURCES (3) `source` pages have been ingested
         since the newest existing purpose-suggestion item (of any status). Cheap counter: a
         bounded indexed COUNT over pages.created_at vs. the last suggestion's created_at — no
         new column, no migration.

    Returns the created ReviewItem, or None when no suggestion is emitted (in-scope, throttled,
    disabled, or any failure).
    """
    if not bool(getattr(settings, "purpose_suggestion_enabled", True)):
        return None
    if not written_pages:
        return None

    # ── Throttle 1: at most one pending purpose-suggestion per vault ─────────────
    try:
        async with _db.get_session() as session:
            pending_existing = (
                await session.execute(
                    select(ReviewItem.id)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _PURPOSE_SUGGESTION_TYPE,
                        ReviewItem.status == "pending",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            # Newest purpose-suggestion of ANY status → drift counter watermark.
            last_created = (
                await session.execute(
                    select(func.max(ReviewItem.created_at)).where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _PURPOSE_SUGGESTION_TYPE,
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_purpose_suggestion: throttle read failed (non-fatal): %s", exc)
        return None

    if pending_existing is not None:
        logger.debug(
            "generate_purpose_suggestion: a purpose-suggestion is already pending (vault=%s) — "
            "skip (throttle 1, zero cost)",
            vault_id,
        )
        return None

    # ── Throttle 2: ≥ N source pages ingested since the last suggestion watermark ─
    min_sources = int(getattr(settings, "purpose_suggestion_min_sources", 3))
    try:
        async with _db.get_session() as session:
            count_stmt = (
                select(func.count())
                .select_from(Page)
                .where(
                    Page.vault_id == vault_id,
                    Page.page_type == PageType.SOURCE.value,
                    Page.deleted_at.is_(None),
                )
            )
            if last_created is not None:
                count_stmt = count_stmt.where(Page.created_at > last_created)
            sources_since = int((await session.execute(count_stmt)).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: source-counter read failed (non-fatal): %s", exc
        )
        return None

    if sources_since < min_sources:
        logger.debug(
            "generate_purpose_suggestion: only %d source(s) since last check < %d (vault=%s) — "
            "skip (throttle 2, zero cost)",
            sources_since,
            min_sources,
            vault_id,
        )
        return None

    # ── Read purpose.md (tolerant — missing file → empty purpose) ────────────────
    purpose_text = ""
    try:
        purpose_path = settings.vault_root / "purpose.md"
        if purpose_path.exists():
            purpose_text = purpose_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_purpose_suggestion: purpose.md read failed (non-fatal): %s", exc)
        purpose_text = ""

    # Deferred (package-level) import — keeps `patch("app.ops.review.resolve_operation_provider")`
    # / `patch("app.ops.review.bounded_chat_collect")` / `patch("app.ops.review.enqueue_review")`
    # effective post-split (see module docstring).
    from app.ops.review import (  # noqa: PLC0415
        bounded_chat_collect,
        enqueue_review,
        resolve_operation_provider,
    )

    # ── Resolve provider (I6 — no provider → None, zero cost) ────────────────────
    resolved = await resolve_operation_provider(vault_id)
    if resolved is None:
        logger.debug(
            "generate_purpose_suggestion: no ingest provider resolved (vault=%s) — skip (I6)",
            vault_id,
        )
        return None
    provider, _config_row = resolved

    max_tokens = int(getattr(settings, "purpose_suggestion_max_tokens", 300))
    timeout_s = float(getattr(settings, "purpose_suggestion_timeout_seconds", 20.0))

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_purpose_drift_instruction(
        analysis=analysis,
        written_pages=written_pages,
        purpose_text=purpose_text,
        max_tokens=max_tokens,
    )

    import asyncio

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            bounded_chat_collect(provider, instruction, use_complete=True, max_tokens=max_tokens),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "generate_purpose_suggestion: provider call timed out after %.1fs (vault=%s) — "
            "no suggestion (never fail ingest)",
            timeout_s,
            vault_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: provider call failed (vault=%s): %s — no suggestion",
            vault_id,
            exc,
        )
        return None
    finally:
        # I7: cost logged to the run ledger regardless of outcome.
        logger.info(
            "purpose_suggestion provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    parsed = _parse_purpose_drift(raw)
    if parsed is None:
        logger.debug(
            "generate_purpose_suggestion: model judged in-scope / no parseable drift (vault=%s)",
            vault_id,
        )
        return None

    theme, why, addition = parsed

    # ── Persist ONE purpose-suggestion ReviewItem ───────────────────────────────
    # rationale = human "why" + delimited exact markdown to append on approve.
    rationale = f"{why}{_PURPOSE_ADDITION_MARKER}{addition}"
    source_page_id = written_pages[0].id if written_pages else None
    content_key = _content_key(
        vault_id=vault_id,
        item_type=_PURPOSE_SUGGESTION_TYPE,
        proposed_title=theme,
    )
    try:
        item = await enqueue_review(
            vault_id=vault_id,
            item_type=_PURPOSE_SUGGESTION_TYPE,
            proposal_origin="system",
            proposed_title=theme,
            rationale=rationale,
            source_page_id=(uuid.UUID(str(source_page_id)) if source_page_id is not None else None),
            content_key=content_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: failed to enqueue suggestion (vault=%s): %s",
            vault_id,
            exc,
        )
        return None

    logger.info(
        "generate_purpose_suggestion: vault=%s emitted purpose-suggestion theme=%r item=%s",
        vault_id,
        theme,
        item.id,
    )
    return item


async def generate_schema_suggestion(
    *,
    vault_id: str,
    written_pages: list[Page],
    origin_source: str,
) -> ReviewItem | None:
    """
    Post-ingest schema co-evolution check (R9-4, K6 — beyond llm_wiki). Compare the ingested
    pages' ACTUAL frontmatter/type/tag usage patterns against the vault schema.md rules; if the
    model detects a RECURRING convention that is not yet codified (a tag family, a frontmatter
    field consistently used, a type misfit), emit ONE `schema-suggestion` ReviewItem with the
    exact markdown rule block to append to schema.md. Called fire-and-forget from the orchestrator
    (right after the R9-3 purpose check) — the caller wraps this in try/except; a failure here
    NEVER breaks ingest (I7).

    Architecture MIRRORS generate_purpose_suggestion exactly. Deliberate deltas (documented):
      1. DEFAULT OFF (schema_suggestion_enabled=False). schema.md is the formal frontmatter
         contract (K6); an approved change alters FUTURE ingest classification/validation, so the
         blast radius is larger than a purpose.md note — operator must opt in. (R9-3 defaults ON.)
      2. max_tokens=400 (vs. 300) — the model restates the convention AND emits the rule block.
      3. min_sources default 5 (vs. 3) — a convention should be seen across more material.
      4. Compares real frontmatter (type/tags), not just topics — schema.md governs frontmatter.

    BOUNDS (I7 / R9-4 AC "bounded call max_tokens 400, no retry"):
      - Exactly ONE provider.chat() call, no loop, no retry.
      - max_tokens = SCHEMA_SUGGESTION_MAX_TOKENS (400) enforced at the call site (_chat_collect).
      - asyncio.wait_for(SCHEMA_SUGGESTION_TIMEOUT_SECONDS).
      - Cost logged to the run ledger via the bound UsageAccumulator (total_cost_usd).
      - On disabled / no-provider / timeout / any error / empty / no-pattern → return None.

    THROTTLE (R9-4, identical shape to R9-3):
      1. Skip if a `schema-suggestion` is already pending for the vault (max 1 pending — no spam).
      2. Fire only when ≥ SCHEMA_SUGGESTION_MIN_SOURCES (5) `source` pages have been ingested
         since the newest existing schema-suggestion item (of any status). Cheap bounded COUNT
         over pages.created_at vs. the last suggestion's created_at — no new column, no migration.

    Returns the created ReviewItem, or None (disabled, in-schema, throttled, or any failure).
    """
    if not bool(getattr(settings, "schema_suggestion_enabled", False)):
        return None
    if not written_pages:
        return None

    # ── Throttle 1: at most one pending schema-suggestion per vault ──────────────
    try:
        async with _db.get_session() as session:
            pending_existing = (
                await session.execute(
                    select(ReviewItem.id)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _SCHEMA_SUGGESTION_TYPE,
                        ReviewItem.status == "pending",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            # Newest schema-suggestion of ANY status → drift counter watermark.
            last_created = (
                await session.execute(
                    select(func.max(ReviewItem.created_at)).where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _SCHEMA_SUGGESTION_TYPE,
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_schema_suggestion: throttle read failed (non-fatal): %s", exc)
        return None

    if pending_existing is not None:
        logger.debug(
            "generate_schema_suggestion: a schema-suggestion is already pending (vault=%s) — "
            "skip (throttle 1, zero cost)",
            vault_id,
        )
        return None

    # ── Throttle 2: ≥ N source pages ingested since the last suggestion watermark ─
    min_sources = int(getattr(settings, "schema_suggestion_min_sources", 5))
    try:
        async with _db.get_session() as session:
            count_stmt = (
                select(func.count())
                .select_from(Page)
                .where(
                    Page.vault_id == vault_id,
                    Page.page_type == PageType.SOURCE.value,
                    Page.deleted_at.is_(None),
                )
            )
            if last_created is not None:
                count_stmt = count_stmt.where(Page.created_at > last_created)
            sources_since = int((await session.execute(count_stmt)).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: source-counter read failed (non-fatal): %s", exc
        )
        return None

    if sources_since < min_sources:
        logger.debug(
            "generate_schema_suggestion: only %d source(s) since last check < %d (vault=%s) — "
            "skip (throttle 2, zero cost)",
            sources_since,
            min_sources,
            vault_id,
        )
        return None

    # ── Read schema.md (tolerant — missing file → empty schema) ──────────────────
    schema_text = ""
    try:
        schema_path = settings.vault_root / "schema.md"
        if schema_path.exists():
            schema_text = schema_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_schema_suggestion: schema.md read failed (non-fatal): %s", exc)
        schema_text = ""

    # Deferred (package-level) import — see module docstring (monkeypatch-compat).
    from app.ops.review import (  # noqa: PLC0415
        bounded_chat_collect,
        enqueue_review,
        resolve_operation_provider,
    )

    # ── Resolve provider (I6 — no provider → None, zero cost) ────────────────────
    resolved = await resolve_operation_provider(vault_id)
    if resolved is None:
        logger.debug(
            "generate_schema_suggestion: no ingest provider resolved (vault=%s) — skip (I6)",
            vault_id,
        )
        return None
    provider, _config_row = resolved

    max_tokens = int(getattr(settings, "schema_suggestion_max_tokens", 400))
    timeout_s = float(getattr(settings, "schema_suggestion_timeout_seconds", 20.0))

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_schema_pattern_instruction(
        written_pages=written_pages,
        schema_text=schema_text,
        max_tokens=max_tokens,
    )

    import asyncio

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            bounded_chat_collect(provider, instruction, use_complete=True, max_tokens=max_tokens),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "generate_schema_suggestion: provider call timed out after %.1fs (vault=%s) — "
            "no suggestion (never fail ingest)",
            timeout_s,
            vault_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: provider call failed (vault=%s): %s — no suggestion",
            vault_id,
            exc,
        )
        return None
    finally:
        # I7: cost logged to the run ledger regardless of outcome.
        logger.info(
            "schema_suggestion provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    parsed = _parse_schema_pattern(raw)
    if parsed is None:
        logger.debug(
            "generate_schema_suggestion: model found no new codifiable convention (vault=%s)",
            vault_id,
        )
        return None

    convention, why, addition = parsed

    # ── Persist ONE schema-suggestion ReviewItem ────────────────────────────────
    # rationale = human "why" + delimited exact markdown rule block to append on approve.
    rationale = f"{why}{_SCHEMA_ADDITION_MARKER}{addition}"
    source_page_id = written_pages[0].id if written_pages else None
    content_key = _content_key(
        vault_id=vault_id,
        item_type=_SCHEMA_SUGGESTION_TYPE,
        proposed_title=convention,
    )
    try:
        item = await enqueue_review(
            vault_id=vault_id,
            item_type=_SCHEMA_SUGGESTION_TYPE,
            proposal_origin="lint",
            proposed_title=convention,
            rationale=rationale,
            source_page_id=(uuid.UUID(str(source_page_id)) if source_page_id is not None else None),
            content_key=content_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: failed to enqueue suggestion (vault=%s): %s",
            vault_id,
            exc,
        )
        return None

    logger.info(
        "generate_schema_suggestion: vault=%s emitted schema-suggestion convention=%r item=%s",
        vault_id,
        convention,
        item.id,
    )
    return item


async def _apply_suggestion_to_file(
    item: ReviewItem,
    *,
    target_filename: str,
    marker: str,
    label: str,
) -> None:
    """
    Shared apply helper for the two vault-file co-evolution suggestions (R9-3 purpose.md /
    R9-4 schema.md). Appends the suggested block (the text after *marker* in `item.rationale`,
    falling back to proposed_title) to `vault/<target_filename>`, then bumps data_version and
    notifies the graph cache (the same seam write_wiki_page uses). Idempotency is the caller's
    concern (the item is marked `created` in the same transaction path); this function only
    performs the filesystem append + version bump.

    Parameterized by target file so purpose.md and schema.md share ONE code path (R9-4 AC:
    "factor the shared apply logic … into one helper parameterized by target file"). The only
    per-type inputs are the filename, the rationale marker, and a log label.

    Raises on write failure — the caller (create_page_from_review) converts to 502 and leaves
    the item pending (no partial state).
    """
    addition = _extract_addition(item.rationale, marker) or (item.proposed_title or "").strip()
    if not addition:
        raise RuntimeError(f"{label} has no addition text to apply")

    target_path = settings.vault_root / target_filename
    # Read existing (tolerant), append with a clean separator, write back.
    existing = ""
    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if not existing or existing.endswith("\n\n"):
        sep = ""
    elif existing.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    new_content = f"{existing}{sep}{addition.rstrip()}\n"
    target_path.write_text(new_content, encoding="utf-8")

    # Bump data_version (same monotonic +1 as the ingest write seam, AC-F16dv-2). Column-scoped
    # UPDATE (portable, no ORM full-entity select) so a partial vault_state schema still bumps.
    from sqlalchemy import update as _sa_update

    async with _db.get_session() as session:
        await session.execute(
            _sa_update(VaultState)
            .where(VaultState.vault_id == settings.vault_id)
            .values(
                data_version=VaultState.data_version + 1,
                updated_at=datetime.now(UTC),
            )
        )

    # Notify graph cache of the bump (best-effort; skipped when the cache is not ready).
    try:
        _cache = runtime_state.graph_cache()

        if _cache is not None:
            # Read ONLY data_version (portable column-scoped select — avoids ORM full-entity
            # selects that would require every VaultState column in narrow test schemas).
            async with _db.get_session() as session:
                new_version = (
                    await session.execute(
                        select(VaultState.data_version).where(
                            VaultState.vault_id == settings.vault_id
                        )
                    )
                ).scalar_one_or_none() or 0
            _cache.notify_bump(new_version)
    except Exception:  # noqa: BLE001
        logger.debug("%s: graph cache notify_bump skipped (cache not ready)", label)

    logger.info(
        "%s: appended %d chars to %s (vault=%s item=%s)",
        label,
        len(addition),
        target_filename,
        item.vault_id,
        item.id,
    )


async def apply_purpose_suggestion(item: ReviewItem) -> None:
    """
    Apply a `purpose-suggestion` to vault/purpose.md (R9-3 approve/create action).

    Thin wrapper over the shared _apply_suggestion_to_file helper (R9-4 refactor): appends the
    suggested section (the block after _PURPOSE_ADDITION_MARKER in `rationale`) to purpose.md,
    bumps data_version, and notifies the graph cache. Raises on write failure.
    """
    await _apply_suggestion_to_file(
        item,
        target_filename="purpose.md",
        marker=_PURPOSE_ADDITION_MARKER,
        label="apply_purpose_suggestion",
    )


async def apply_schema_suggestion(item: ReviewItem) -> None:
    """
    Apply a `schema-suggestion` to vault/schema.md (R9-4 approve/create action).

    Thin wrapper over the shared _apply_suggestion_to_file helper: appends the suggested rule
    block (the text after _SCHEMA_ADDITION_MARKER in `rationale`) to schema.md, bumps
    data_version, and notifies the graph cache. Raises on write failure. schema.md changes affect
    FUTURE ingest classification/validation — see settings.schema_suggestion_enabled docstring.
    """
    await _apply_suggestion_to_file(
        item,
        target_filename="schema.md",
        marker=_SCHEMA_ADDITION_MARKER,
        label="apply_schema_suggestion",
    )


def _extract_addition(rationale: str | None, marker: str) -> str | None:
    """Return the exact markdown addition stored after *marker* in *rationale*, else None."""
    if not rationale or marker not in rationale:
        return None
    addition = rationale.split(marker, 1)[1].strip()
    return addition or None


def _extract_purpose_addition(rationale: str | None) -> str | None:
    """Purpose-specific wrapper over _extract_addition (kept for the R9-3 test surface)."""
    return _extract_addition(rationale, _PURPOSE_ADDITION_MARKER)


def _extract_schema_addition(rationale: str | None) -> str | None:
    """Schema-specific wrapper over _extract_addition (R9-4 apply/test surface)."""
    return _extract_addition(rationale, _SCHEMA_ADDITION_MARKER)
