"""
Type re-classification op (SPRINT-v1.2 tail — the TYPE twin of the domain backfill; I1/I6/I7).

Re-assigns each page's ``type:`` frontmatter (and ``pages.page_type`` in Postgres) per the
freshly-curated ``vault/schema.md`` rules (K8 — the human curates the rules, the LLM applies
them). Mirrors the architecture of ``ops/backfill_domains.py``: a bounded background run whose
backend-owned ``POST /ops/reclassify-types`` endpoint freezes the bounds, schedules
:func:`run_reclassify` as an ``asyncio.create_task``, and polls the module-level single-flight
state exposed here.

This module owns ONLY the run function + the single-flight state. It registers NO endpoint
(the endpoint lives in the backend-engineer-owned main.py).

Candidate selection (SPRINT-v1.2 tail):
  * default — pages whose ``page_type`` is NULL / ``'untyped'`` / ``'concept'`` (the suspicious
    bulk that a coarse first-pass classifier tends to over-produce).
  * ``force=True`` — ALL non-reserved wiki pages (widens the sweep). ``overview`` / ``index``
    are NEVER candidates in either mode (reserved catalogue types, K3/F3).

Per page: ONE bounded provider call (I7) embedding the schema.md type rules + title + a body
excerpt → ``{"type": "<one of entity|concept|source|query|synthesis|comparison>"}``. STRICT
validation: an out-of-vocabulary / malformed answer is SKIPPED and counted as ``failed``. If the
proposed type equals the current one, the page is counted ``skipped`` (no write).

Write-back mirrors ``apply_domain_tags`` (tolerant frontmatter round-trip, byte-exact for every
other key) but rewrites the ``type:`` key + persists ``pages.page_type``. One ``data_version``
bump at the end if anything changed (never once-per-page).

Cost: one ``UsageAccumulator`` for the whole run; ``total_cost_usd`` logged (I7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.db import get_session
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import INDEX_TYPE, OVERVIEW_TYPE, PageType
from app.ops._llm import (
    bounded_chat_collect,
    loads_json_lenient,
    resolve_operation_provider,
)

logger = logging.getLogger(__name__)

# $1 cost-anomaly threshold — same as the ingest / domain-backfill path (ADR-0009 §3).
_COST_ANOMALY_THRESHOLD_USD = 1.00

# Bounds defaults (mirror ops/backfill_domains). max_pages HARD upper cap = 2000.
DEFAULT_MAX_PAGES = 500
MAX_PAGES_HARD_CAP = 2_000
DEFAULT_TOKEN_BUDGET = 60_000

# Body slice cap for the classification prompt (~3k chars — the TYPE twin's excerpt budget).
_CONTENT_CHAR_CAP = 3_000

# The six user-content types the classifier may choose from (STRICT vocabulary).
_VALID_TYPES: tuple[str, ...] = tuple(t.value for t in PageType)
# Reserved catalogue types — NEVER touched, in either default or force mode (K3/F3).
_RESERVED_TYPES: frozenset[str] = frozenset({OVERVIEW_TYPE, INDEX_TYPE})

# Pages already EXAMINED this process (changed, confirmed-same or failed). Without this
# memory, confirmed-'concept' pages stay candidates forever and the updated_at-DESC head
# is re-billed on every run (observed live: run 25 = 68 processed / 0 changed / $0.60).
# Process-lifetime by design: a backend restart re-examines, which is safe (idempotent
# writes) just not free. force=True clears it (explicit full re-sweep).
_examined_ids: set[Any] = set()  # native Page.id values (UUID) — never str (Postgres uuid binds)
# Default "suspicious" candidate types (NULL is handled separately in the query).
_DEFAULT_CANDIDATE_TYPES: frozenset[str] = frozenset({"untyped", PageType.CONCEPT.value})


# ── Result + single-flight state ──────────────────────────────────────────────


@dataclass
class ReclassifySummary:
    """Outcome of one type re-classification run (completion log line)."""

    processed: int = 0
    changed: int = 0
    skipped: int = 0
    failed: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    stopped_reason: str = "complete"  # complete | budget | maxpages | error
    max_pages: int = 0
    token_budget: int = 0
    force: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "changed": self.changed,
            "skipped": self.skipped,
            "failed": self.failed,
            "by_type": dict(self.by_type),
            "total_cost_usd": round(self.total_cost_usd, 4),
            "stopped_reason": self.stopped_reason,
            "max_pages": self.max_pages,
            "token_budget": self.token_budget,
            "force": self.force,
        }


@dataclass
class _ReclassifyState:
    """Module-level single-flight state (read by the endpoint to 409 / report)."""

    is_running: bool = False
    last_summary: ReclassifySummary | None = None
    # Frozen bounds of the currently-running (or last-started) run, for the 202 echo.
    current: dict[str, Any] = field(default_factory=dict)


_state = _ReclassifyState()


def is_running() -> bool:
    """True if a reclassify run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> ReclassifySummary | None:
    """Return the summary of the most recently COMPLETED reclassify run (None if never run)."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None, token_budget: int | None) -> tuple[int, int]:
    """
    Freeze + clamp the run bounds (I7). ``None`` → settings/module default; a value over the hard
    cap is clamped (never exceeded). Returns ``(max_pages, token_budget)``.
    """
    default_max = int(getattr(settings, "reclassify_max_pages", DEFAULT_MAX_PAGES))
    default_budget = int(getattr(settings, "reclassify_token_budget", DEFAULT_TOKEN_BUDGET))
    mp = default_max if max_pages is None else int(max_pages)
    mp = max(1, min(mp, MAX_PAGES_HARD_CAP))
    tb = default_budget if token_budget is None else int(token_budget)
    tb = max(1, tb)
    return mp, tb


# ── Run ────────────────────────────────────────────────────────────────────────


async def run_reclassify(
    *,
    vault_id: str,
    max_pages: int | None = None,
    token_budget: int | None = None,
    force: bool = False,
) -> ReclassifySummary:
    """
    Run ONE bounded type re-classification (SPRINT-v1.2 tail). Sets the single-flight flag for its
    whole duration; a concurrent call while :func:`is_running` should be rejected by the endpoint
    (409).

    Loads the schema.md type rules once (via the same vault-context loader the orchestrator uses),
    resolves the ingest provider (I6 — no hardcoded backend), then iterates the bounded candidate
    set: one classifier call per page, STRICT validation, write-back via :func:`apply_page_type`
    when the proposed type differs. Bumps ``data_version`` once at the end if any page changed.
    Never raises — a fatal error is recorded as ``stopped_reason="error"``.
    """
    from app.ingest.context import _load_vault_context  # noqa: PLC0415

    mp, tb = clamp_bounds(max_pages, token_budget)
    summary = ReclassifySummary(max_pages=mp, token_budget=tb, force=force)

    _state.is_running = True
    _state.current = {"max_pages": mp, "token_budget": tb, "force": force}
    try:
        resolved = await resolve_operation_provider(vault_id)
        if resolved is None:
            summary.stopped_reason = "error"
            logger.warning(
                "reclassify-types: no ingest provider resolved (vault=%s) — abort (I6)", vault_id
            )
            _state.last_summary = summary
            return summary
        provider, _config_row = resolved

        # schema.md (+ purpose.md) content — the K8 human-curated rules the classifier must follow.
        vault_context = _load_vault_context()

        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        await _run_inner(
            vault_id=vault_id,
            provider=provider,
            vault_context=vault_context,
            max_pages=mp,
            token_budget=tb,
            force=force,
            accumulator=accumulator,
            summary=summary,
        )
        summary.total_cost_usd = round(accumulator.total_cost_usd, 4)
    except Exception as exc:  # noqa: BLE001 — never propagate into the background task
        summary.stopped_reason = "error"
        logger.warning("reclassify-types: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    # Completion log line — the authoritative summary (I7).
    logger.info(
        "reclassify-types: processed=%d changed=%d skipped=%d failed=%d cost_usd=%.4f "
        "stopped_reason=%s by_type=%s vault=%s",
        summary.processed,
        summary.changed,
        summary.skipped,
        summary.failed,
        summary.total_cost_usd,
        summary.stopped_reason,
        summary.by_type,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: reclassify-types total_cost_usd=%.4f exceeds $%.2f (vault=%s) — "
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
    vault_context: str,
    max_pages: int,
    token_budget: int,
    force: bool,
    accumulator: UsageAccumulator,
    summary: ReclassifySummary,
) -> None:
    """Bounded per-page loop (I7). Bumps ``data_version`` once at the end if anything changed."""
    from app.ingest.orchestrator import (  # noqa: PLC0415
        _read_body_for_classification,
        apply_page_type,
        bump_version,
    )

    if force:
        # Explicit full re-sweep: forget what was examined in previous runs.
        _examined_ids.clear()

    candidates = await _load_candidate_pages(vault_id, max_pages, force)
    changed_any = False

    for page in candidates:
        # ── budget gate BEFORE spending on this page (I7) ───────────────────────
        if accumulator.total_tokens >= token_budget:
            summary.stopped_reason = "budget"
            break

        # Reserved types are never candidates (defence-in-depth: the query already excludes them).
        if (page.page_type or "") in _RESERVED_TYPES:
            summary.skipped += 1
            continue

        summary.processed += 1
        try:
            body = _read_body_for_classification(page)
            proposed = await classify_page_type(
                provider,
                page_title=page.title or "",
                page_content=body,
                vault_context=vault_context,
            )
            if proposed is None:
                # STRICT validation failed (malformed / out-of-vocabulary) → skip, count failed.
                # Examined anyway: a page the model cannot type will not get better next run;
                # force=True re-opens it.
                _examined_ids.add(page.id)
                summary.failed += 1
                logger.debug("reclassify-types: page=%s no valid type (skipped)", page.id)
                continue
            if proposed == (page.page_type or ""):
                # Same type ⇒ no write (idempotent, counted as skipped) — and EXAMINED,
                # so the next run advances to fresh pages instead of re-billing this one.
                _examined_ids.add(page.id)
                summary.skipped += 1
                continue
            await apply_page_type(page, proposed)
            _examined_ids.add(page.id)
            changed_any = True
            summary.changed += 1
            summary.by_type[proposed] = summary.by_type.get(proposed, 0) + 1
            logger.debug("reclassify-types: page=%s type=%s", page.id, proposed)
        except Exception as exc:  # noqa: BLE001 — per-page non-fatal (NOT examined: retryable)
            summary.failed += 1
            logger.warning(
                "reclassify-types: classification failed for page=%s (skipped): %s", page.id, exc
            )
    else:
        # for-loop completed without break → all candidates processed. With the examined-set
        # exclusion, an EMPTY candidate list means the sweep is genuinely done (complete);
        # a full page means there may be more (maxpages).
        if summary.stopped_reason == "complete" and len(candidates) >= max_pages:
            summary.stopped_reason = "maxpages"

    # Single data_version bump for the whole batch (never per-page).
    if changed_any:
        await bump_version()


# ── Classification (STRICT single-type) ───────────────────────────────────────


async def classify_page_type(
    provider: InferenceProvider,
    page_title: str,
    page_content: str,
    vault_context: str,
) -> str | None:
    """
    Classify a page into EXACTLY ONE of the six user-content types (STRICT).

    ONE bounded ``provider.chat()`` call (I6/I7) — the same backend-neutral surface the domain
    tagger / enrich_wikilinks / review use (no new ABC method, no isinstance branch). The prompt
    EMBEDS the vault schema.md rules so classification follows the human-curated K8 rules.

    Returns the chosen type string (one of ``_VALID_TYPES``) or ``None`` when the provider output
    is malformed or names a type outside the vocabulary (the caller counts ``None`` as ``failed``
    and skips the page — never a silent mis-write).
    """
    instruction = _build_instruction(
        page_title=page_title,
        page_content=page_content,
        vault_context=vault_context,
    )
    raw = await bounded_chat_collect(provider, instruction)
    return _parse_type(raw)


def _parse_type(raw: str) -> str | None:
    """
    Parse the classification JSON and validate STRICTLY against ``_VALID_TYPES``.

    Accepts ``{"type": "<name>"}`` (preferred) or a bare JSON string. The name is matched
    case-insensitively to the canonical lowercase type value; anything else → ``None``. Never
    raises.
    """
    obj = loads_json_lenient(raw)
    value: Any
    if isinstance(obj, dict):
        value = obj.get("type", obj.get("page_type"))
    elif isinstance(obj, str):
        value = obj
    else:
        return None
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    return key if key in _VALID_TYPES else None


# ── Prompt + provider surface ─────────────────────────────────────────────────


def _build_instruction(*, page_title: str, page_content: str, vault_context: str) -> str:
    """
    Deterministic single-type classification prompt. The model must pick EXACTLY ONE type from the
    six and return ``{"type": "<name>"}``. The vault schema.md rules are embedded verbatim so the
    classification follows the human-curated K8 rules.
    """
    body = (page_content or "").strip()[:_CONTENT_CHAR_CAP]
    type_block = "\n".join(f"- {t}" for t in _VALID_TYPES)
    schema_block = (vault_context or "").strip() or "(no schema.md available)"
    return (
        "You are the page-type classification step of a self-organizing wiki. You are given a "
        "page (title + body excerpt) and the vault's SCHEMA RULES. Decide which SINGLE type this "
        "page should have, following the schema rules.\n\n"
        "IMPORTANT RULES:\n"
        "  - Choose EXACTLY ONE type from the list below. Never invent a type.\n"
        "  - Follow the vault schema rules when deciding the type.\n"
        "  - Return the type name verbatim (exact lowercase spelling) from the list.\n\n"
        f"# Valid types (the ONLY valid answers)\n{type_block}\n\n"
        f"# Vault schema rules\n{schema_block}\n\n"
        f"# Page title\n{page_title}\n\n"
        f"# Page body excerpt\n{body}\n\n"
        'Return ONLY a JSON object with a single key "type" whose value is the chosen type name '
        "(exactly one of the valid types). Return no prose, only the JSON object."
    )


# ── Candidate query + provider resolution ─────────────────────────────────────


async def _load_candidate_pages(vault_id: str, max_pages: int, force: bool) -> list[Any]:
    """
    Bounded indexed read of live wiki pages, most-recently-touched first.

    Default mode: pages whose ``page_type`` is NULL / ``'untyped'`` / ``'concept'`` (the suspicious
    bulk). ``force=True``: ALL non-reserved wiki pages. Reserved types (``overview`` / ``index``)
    are excluded in BOTH modes. The ``LIMIT`` is the structural bound (I7).
    """
    from sqlalchemy import or_, select

    from app.models import Page

    filters = [
        Page.vault_id == vault_id,
        Page.deleted_at.is_(None),
        Page.file_path.like("wiki/%"),
        Page.title.is_not(None),
        Page.page_type.not_in(tuple(_RESERVED_TYPES)),
    ]
    if not force:
        # NULL page_type OR one of the suspicious default types.
        filters.append(
            or_(
                Page.page_type.is_(None),
                Page.page_type.in_(tuple(_DEFAULT_CANDIDATE_TYPES)),
            )
        )
    if _examined_ids:
        # Skip pages already examined this process — the fix for the observed
        # stuck loop (same updated_at-DESC head re-billed every run).
        filters.append(Page.id.not_in(tuple(_examined_ids)))

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page).where(*filters).order_by(Page.updated_at.desc()).limit(max_pages)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            session.expunge(r)
    return rows
