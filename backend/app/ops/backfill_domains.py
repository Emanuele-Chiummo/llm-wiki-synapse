"""
Domain backfill (ADR-0054 §4, F18 / R12-2 — I1/I6/I7).

Backfills ``domain/*`` tags onto pages ingested before the vocabulary existed (or after the
owner edited it). Runs as a bounded background task (deep-research precedent, ADR-0024): the
backend-owned ``POST /ops/backfill-domains`` endpoint freezes the bounds, schedules
:func:`run_backfill` as an ``asyncio.create_task``, and reports the frozen bounds + polls the
module-level single-flight state exposed here.

This module owns ONLY the run function + the single-flight state. It registers NO endpoint
(the endpoint lives in the backend-engineer-owned main.py).

Bounds (I7):
  * ``max_pages`` — the candidate SELECT is ``LIMIT max_pages`` (bounded query, no full scan),
    ordered ``updated_at DESC`` (most-recently-touched first).
  * ``token_budget`` — checked at the TOP of each per-page iteration; stop before spending when
    reached (under-spend, never over) → ``stopped_reason="budget"``.
  * ``force`` — default False skips pages already carrying a ``domain/*`` tag (idempotent, §4.3);
    True re-classifies every candidate (the §3.3 merge is still idempotent per page).

Cost: one ``UsageAccumulator`` for the whole run; ``total_cost_usd`` logged (I7). One
``data_version`` bump at the end if any page changed (§4.3 — never once-per-page).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.db import get_session
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator

logger = logging.getLogger(__name__)

# $1 cost-anomaly threshold — same as the ingest path (ADR-0009 §3 / ADR-0054 §4.2).
_COST_ANOMALY_THRESHOLD_USD = 1.00

# Bounds defaults (ADR-0054 §4.1 / scope R12-2). max_pages HARD upper cap = 2000.
DEFAULT_MAX_PAGES = 500
MAX_PAGES_HARD_CAP = 2_000
DEFAULT_TOKEN_BUDGET = 60_000


# ── Result + single-flight state ──────────────────────────────────────────────


@dataclass
class BackfillSummary:
    """Outcome of one backfill run (ADR-0054 §4.1 completion log line)."""

    processed: int = 0
    tagged: int = 0
    skipped: int = 0
    failed: int = 0
    total_cost_usd: float = 0.0
    stopped_reason: str = "complete"  # complete | budget | maxpages | dormant | error
    max_pages: int = 0
    token_budget: int = 0
    force: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "tagged": self.tagged,
            "skipped": self.skipped,
            "failed": self.failed,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "stopped_reason": self.stopped_reason,
            "max_pages": self.max_pages,
            "token_budget": self.token_budget,
            "force": self.force,
        }


@dataclass
class _BackfillState:
    """Module-level single-flight state (read by the endpoint to 409 / report)."""

    is_running: bool = False
    last_summary: BackfillSummary | None = None
    # Frozen bounds of the currently-running (or last-started) run, for the 202 echo.
    current: dict[str, Any] = field(default_factory=dict)


_state = _BackfillState()


def is_running() -> bool:
    """True if a backfill is currently in flight (single-flight guard, ADR-0054 §4.1)."""
    return _state.is_running


def get_last_summary() -> BackfillSummary | None:
    """Return the summary of the most recently COMPLETED backfill run (None if never run)."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None, token_budget: int | None) -> tuple[int, int]:
    """
    Freeze + clamp the run bounds (ADR-0054 §4.1). ``None`` → settings/module default; a value
    over the hard cap is clamped (never exceeded). Returns ``(max_pages, token_budget)``.
    """
    default_max = int(getattr(settings, "domain_backfill_max_pages", DEFAULT_MAX_PAGES))
    default_budget = int(getattr(settings, "domain_backfill_token_budget", DEFAULT_TOKEN_BUDGET))
    mp = default_max if max_pages is None else int(max_pages)
    mp = max(1, min(mp, MAX_PAGES_HARD_CAP))
    tb = default_budget if token_budget is None else int(token_budget)
    tb = max(1, tb)
    return mp, tb


# ── Run ────────────────────────────────────────────────────────────────────────


async def run_backfill(
    *,
    vault_id: str,
    max_pages: int | None = None,
    token_budget: int | None = None,
    force: bool = False,
) -> BackfillSummary:
    """
    Run ONE bounded domain backfill (ADR-0054 §4). Sets the single-flight flag for its whole
    duration; a concurrent call while :func:`is_running` should be rejected by the endpoint (409).

    Dormant vocabulary ⇒ returns immediately (``stopped_reason="dormant"``), zero provider calls
    (I6, Do-NOT #2). Otherwise iterates the bounded candidate set, classifies each page against
    the vocabulary, merges ``domain/*`` tags, and writes back via the shared ``apply_domain_tags``
    primitive (I1, no second-per-page bump). Bumps ``data_version`` once at the end if any page
    changed. Never raises — a fatal error is recorded as ``stopped_reason="error"``.
    """
    from app.config_overrides import effective_domain_vocabulary  # noqa: PLC0415

    mp, tb = clamp_bounds(max_pages, token_budget)
    summary = BackfillSummary(max_pages=mp, token_budget=tb, force=force)

    _state.is_running = True
    _state.current = {"max_pages": mp, "token_budget": tb, "force": force}
    try:
        vocabulary = effective_domain_vocabulary()
        if not vocabulary:
            summary.stopped_reason = "dormant"
            logger.info("backfill-domains: vocabulary dormant — nothing to do (vault=%s)", vault_id)
            _state.last_summary = summary
            return summary

        resolved = await _resolve_provider(vault_id)
        if resolved is None:
            summary.stopped_reason = "error"
            logger.warning(
                "backfill-domains: no ingest provider resolved (vault=%s) — abort (I6)", vault_id
            )
            _state.last_summary = summary
            return summary
        provider, _config_row = resolved

        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        await _run_inner(
            vault_id=vault_id,
            provider=provider,
            vocabulary=vocabulary,
            max_pages=mp,
            token_budget=tb,
            force=force,
            accumulator=accumulator,
            summary=summary,
        )
        summary.total_cost_usd = round(accumulator.total_cost_usd, 4)
    except Exception as exc:  # noqa: BLE001 — never propagate into the background task
        summary.stopped_reason = "error"
        logger.warning("backfill-domains: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    # Completion log line — the authoritative summary (ADR-0054 §4.1, I7).
    logger.info(
        "backfill-domains: processed=%d tagged=%d skipped=%d failed=%d cost_usd=%.4f "
        "stopped_reason=%s vault=%s",
        summary.processed,
        summary.tagged,
        summary.skipped,
        summary.failed,
        summary.total_cost_usd,
        summary.stopped_reason,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: backfill-domains total_cost_usd=%.4f exceeds $%.2f (vault=%s) — "
            "investigate runaway/misconfiguration",
            summary.total_cost_usd,
            _COST_ANOMALY_THRESHOLD_USD,
            vault_id,
        )
    return summary


async def _run_inner(
    *,
    vault_id: str,
    provider: InferenceProvider,
    vocabulary: list[str],
    max_pages: int,
    token_budget: int,
    force: bool,
    accumulator: UsageAccumulator,
    summary: BackfillSummary,
) -> None:
    """The bounded per-page loop (I7). Bumps ``data_version`` once at the end if anything ran."""
    from app.ingest.domain_tagger import (  # noqa: PLC0415
        classify_page_domains,
        has_domain_tag,
        merge_domain_tags,
    )
    from app.ingest.orchestrator import (  # noqa: PLC0415
        _read_body_for_classification,
        apply_domain_tags,
        bump_version,
    )

    candidates = await _load_candidate_pages(vault_id, max_pages)
    changed_any = False

    for page in candidates:
        # ── budget gate BEFORE spending on this page (I7, ADR-0054 §4.2) ────────
        if accumulator.total_tokens >= token_budget:
            summary.stopped_reason = "budget"
            break

        # ── idempotency: skip already-domain-tagged pages unless force (§4.3) ──
        if not force and has_domain_tag(page.tags):
            summary.skipped += 1
            continue

        summary.processed += 1
        try:
            body = _read_body_for_classification(page)
            classified = await classify_page_domains(
                provider,
                page_title=page.title or "",
                page_content=body,
                vocabulary=vocabulary,
            )
            merged = merge_domain_tags(page.tags, classified)
            if merged != (page.tags or []):
                await apply_domain_tags(page, merged)
                changed_any = True
            summary.tagged += 1
            logger.debug("backfill-domains: page=%s domains=%s", page.id, classified)
        except Exception as exc:  # noqa: BLE001 — per-page non-fatal (§3.4 discipline)
            summary.failed += 1
            logger.warning(
                "backfill-domains: classification failed for page=%s (skipped): %s", page.id, exc
            )
    else:
        # for-loop completed without break → all candidates processed.
        if summary.stopped_reason == "complete" and len(candidates) >= max_pages:
            summary.stopped_reason = "maxpages"

    # Single data_version bump for the whole batch (§4.3 — one debounced recompute, not per-page).
    if changed_any:
        await bump_version()


# ── Candidate query + provider resolution ─────────────────────────────────────


async def _load_candidate_pages(vault_id: str, max_pages: int) -> list[Any]:
    """
    Bounded indexed read of live wiki pages, most-recently-touched first (ADR-0054 §4.2). The
    ``domain/*`` skip (idempotency) is applied in the loop (dialect-portable JSONB predicates are
    avoided here so the query stays SQLite-test-clean); the ``LIMIT`` is the structural bound (I7).
    """
    from sqlalchemy import select

    from app.models import Page

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page)
                    .where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.file_path.like("wiki/%"),
                        Page.title.is_not(None),
                    )
                    .order_by(Page.updated_at.desc())
                    .limit(max_pages)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            session.expunge(r)
    return rows


async def _resolve_provider(vault_id: str) -> tuple[InferenceProvider, Any] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6 — no hardcoded backend; "no
    provider" → None). Mirrors ops/enrich_wikilinks.py::_resolve_provider.
    """
    from app.provider_config_service import (  # noqa: PLC0415
        ConfigNotFoundError,
        resolve_provider_config,
    )

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("backfill-domains: provider resolution failed (vault=%s): %s", vault_id, exc)
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("backfill-domains: provider build failed (vault=%s): %s", vault_id, exc)
        return None
    return provider, config_row
