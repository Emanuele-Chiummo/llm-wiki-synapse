"""
Scheduled schema review op (R12-8, K8 — K6 co-evolution beyond llm_wiki).

This module is the SCHEDULED wrapper around the existing generate_schema_suggestion
machinery in app.ops.review. It is invoked by OpsScheduler when schema_review_schedule
is not "off", or immediately via POST /ops/schedules/schema_review/run-now.

DESIGN DECISIONS (R12-8):

1.  REUSE, not reinvent (I9):
    generate_schema_suggestion() in review.py already does everything we need:
    reads schema.md, snapshots frontmatter, makes ONE bounded provider call, files the
    ReviewItem, and implements anti-spam (skip if a pending schema-suggestion exists).
    We wrap it here with a synthetic written_pages list built from a RECENT VAULT SAMPLE
    (last N pages by created_at DESC, capped at SCHEMA_REVIEW_SAMPLE_SIZE) so the core
    logic stays identical and tested.

2.  SCHEDULE PATH BYPASSES SCHEMA_SUGGESTION_ENABLED (R12-8 design decision):
    The SCHEMA_SUGGESTION_ENABLED env var gates the AUTOMATIC post-ingest trigger.
    An explicit schedule (schema_review_schedule != "off") or a run-now click is explicit
    user intent — the operator deliberately chose to enable periodic reviews. Honouring
    SCHEMA_SUGGESTION_ENABLED here would make the schedule silently do nothing, violating
    the principle of least surprise. This bypass is documented in ops_scheduler.py and here.
    It does NOT bypass the anti-spam guard (pending-item dedup — that guard always applies).

3.  ANTI-SPAM (inherited from generate_schema_suggestion, Throttle 1):
    generate_schema_suggestion() skips (zero cost) when a schema-suggestion is already
    pending for the vault. This dedup is sufficient and is NOT bypassed here.

4.  BOUNDS (I7):
    Exactly ONE provider call, no loop, no retry (inherited from generate_schema_suggestion).
    Token budget from the resolved provider_config row.

5.  NEVER AUTO-EDITS schema.md (K8 / I7 human gate):
    The operation only PROPOSES changes. Human must click "Create" (approve) in the
    Review queue. schema.md is never touched by this scheduled pass.

6.  VAULT SAMPLE CONSTRUCTION:
    generate_schema_suggestion() expects a list[Page] of the "written pages this run".
    For the scheduled path we substitute a sample of RECENT vault pages (last N by
    created_at DESC, bounded by SCHEMA_REVIEW_SAMPLE_SIZE, default 20). This gives the
    provider a representative cross-section of how the vault has evolved, without a
    full-rescan (I1 — we query existing DB rows, never re-read every file).
    The origin_source is set to "(schema_review_schedule)" for auditability.

Public surface
--------------
run_schema_review(vault_id) → None  — called by OpsScheduler._default_schema_review_fn
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db import get_session
from app.models import Page

logger = logging.getLogger(__name__)

# Number of recent pages to feed into the schema-pattern analysis.
# Bounded (I7) — never a full-rescan (I1).
SCHEMA_REVIEW_SAMPLE_SIZE: int = 20


async def run_schema_review(vault_id: str) -> None:
    """
    Scheduled schema review pass (R12-8).

    Builds a sample of recent vault pages (last SCHEMA_REVIEW_SAMPLE_SIZE by
    created_at DESC), then calls generate_schema_suggestion() which:
      - Checks the anti-spam guard (skip if a pending schema-suggestion exists).
      - Makes ONE bounded provider call comparing frontmatter patterns vs schema.md.
      - Files a schema-suggestion ReviewItem if a new convention is detected.
      - Never edits schema.md (human approves in the Review queue — K8).

    SCHEDULE GATE NOTE: this function does NOT check SCHEMA_SUGGESTION_ENABLED.
    The env flag gates the automatic post-ingest trigger only. An explicit schedule
    or run-now is explicit user intent (R12-8 design decision — see module docstring §2).

    Non-fatal: errors are logged and re-raised so OpsScheduler records them in
    last_status (I7 — the scheduler never crashes on op failure).
    """
    # ── Build vault sample (recent pages, bounded — I1: DB read only, no file re-scan) ──
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Page)
                .where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                )
                .order_by(Page.created_at.desc())
                .limit(SCHEMA_REVIEW_SAMPLE_SIZE)
            )
            recent_pages: list[Page] = list(result.scalars().all())
    except Exception as exc:
        logger.error(
            "run_schema_review: failed to fetch recent pages (vault=%s): %s", vault_id, exc
        )
        raise

    if not recent_pages:
        logger.info(
            "run_schema_review: vault=%s has no pages yet — skip (nothing to review)", vault_id
        )
        return

    logger.info(
        "run_schema_review: vault=%s sample_size=%d (scheduled pass, R12-8)",
        vault_id,
        len(recent_pages),
    )

    # ── Delegate to generate_schema_suggestion (I9 — reuse, do NOT reinvent) ────────
    # The anti-spam guard (Throttle 1: skip if pending schema-suggestion exists) lives
    # inside generate_schema_suggestion and is always enforced, even on the scheduled path.
    # We bypass the SCHEMA_SUGGESTION_ENABLED gate intentionally (R12-8 §2 decision above).
    from app.ops.review import (
        generate_schema_suggestion,  # noqa: PLC0415 — deferred to avoid circular
    )

    try:
        item = await generate_schema_suggestion(
            vault_id=vault_id,
            written_pages=recent_pages,
            origin_source="(schema_review_schedule)",
        )
    except Exception as exc:
        logger.error(
            "run_schema_review: generate_schema_suggestion failed (vault=%s): %s",
            vault_id,
            exc,
        )
        raise

    if item is not None:
        logger.info(
            "run_schema_review: emitted schema-suggestion item=%s (vault=%s)",
            item.id,
            vault_id,
        )
    else:
        logger.info(
            "run_schema_review: no suggestion emitted (vault=%s) — "
            "either pending item exists (anti-spam), no provider, or no new convention found",
            vault_id,
        )
