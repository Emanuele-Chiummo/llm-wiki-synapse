"""
ops/migrate_lint_query_stubs.py — ADR-0067 D1 / P0-5 remediation.

Bounded, idempotent, DRY-RUN-BY-DEFAULT migration that cleans the legacy pollution
where Wiki Lint historically wrote missing wikilink targets as ``type=query`` stubs
(body: old placeholder sentence, tags ``[stub, lint]``, under ``wiki/queries/``).
PROD has 133 such pages; they must become entity/concept pages so the graph hub
structure and ``queries/`` semantics are both restored (ADR-0067 D1, QP-Q4, LN-D1).

Architecture mirrors ``ops/reclassify_types.py``:
  - Module-level single-flight state exposed for the endpoint (409 guard).
  - Deterministic — zero LLM / provider calls; ``total_cost_usd = 0.0`` always.
  - Bounded by ``max_pages`` (no token budget: no per-page cost).
  - One ``data_version`` bump at the end for the whole batch (never per-page, I1).
  - Idempotent: re-running on already-moved pages finds 0 candidates (I1).
  - ``--dry-run`` (default ``apply=False``): reports the plan, writes nothing.
  - ``--apply``: performs the move, updates DB + Qdrant, reconnects wikilinks.

Type inference:
  Reuses ``_infer_stub_page_type`` from ``app.ops.lint`` (NOT re-implemented here).
  It applies the cheapest-first heuristic: legal-suffix → ENTITY; all-caps acronym
  → ENTITY; any title-cased word → ENTITY; otherwise CONCEPT.  NEVER QUERY.

Wikilink safety:
  ``wiki/links.reresolve_dangling_links`` is called after all moves.  Resolution is
  title-based (not path-based), so moving ``wiki/queries/aws.md`` to
  ``wiki/entities/aws.md`` with the same ``title: AWS`` keeps all ``[[AWS]]`` links
  resolving to the same page id (verified by the ``_ResolverMaps.by_title`` chain in
  ``wiki/links.py:157``).

Reuse points (explicit, no re-implementation):
  - ``_infer_stub_page_type``      — lint.py  (heuristic type classifier)
  - ``reresolve_dangling_links``   — wiki/links.py  (wikilink reconnect)
  - ``bump_version``               — ingest/orchestrator.py  (data_version bump)
  - ``upsert_vector``              — ingest/orchestrator.py  (Qdrant payload update)
  - ``_sha256``                    — ingest/orchestrator.py  (content hash)
  - ``_slugify``                   — ingest/orchestrator.py  (filename stem)
  - ``type_subdir``                — ingest/schemas.py  (folder routing)
  - ``PageType``                   — ingest/schemas.py  (type enum)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import get_session

logger = logging.getLogger(__name__)

# ── Sentinel strings (body detection — detect BOTH historical forms) ──────────

# The legacy placeholder written by the old _create_broken_link_stub before ADR-0067.
# grep 'Created by Wiki Lint as a placeholder' = 133/133 on prod (QP-Q4).
LEGACY_PLACEHOLDER_BODY: str = "Created by Wiki Lint as a placeholder for a missing wikilink target"

# The current stub body written by the updated _create_broken_link_stub (post ADR-0067 D1).
# Stubs under wiki/queries/ after the lint-fix code was corrected would carry this body.
CURRENT_STUB_BODY: str = (
    "Stub created by Wiki Lint for a referenced but not-yet-written page. " "Enrich or merge."
)

# ── Bounds ────────────────────────────────────────────────────────────────────

# Hard caps — production has 133 stubs; 500 is a safe upper bound.
DEFAULT_MAX_PAGES: int = 200
MAX_PAGES_HARD_CAP: int = 500

# ── Cost-anomaly (always 0.0 here; kept for structural parity) ────────────────
_COST_ANOMALY_THRESHOLD_USD: float = 0.01  # should never be reached


# ── Result DTO ────────────────────────────────────────────────────────────────


@dataclass
class MigrationPlanItem:
    """One candidate stub: what the migration WOULD do (dry-run output)."""

    slug: str
    old_path: str
    new_path: str
    inferred_type: str  # "entity" | "concept"
    title: str


@dataclass
class MigrationSummary:
    """Outcome of one migrate_lint_query_stubs run."""

    processed: int = 0
    moved: int = 0
    skipped: int = 0
    failed: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    plan: list[MigrationPlanItem] = field(default_factory=list)
    total_cost_usd: float = 0.0  # always 0.0 — deterministic, no LLM
    stopped_reason: str = "complete"  # complete | maxpages | error
    max_pages: int = 0
    apply: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "moved": self.moved,
            "skipped": self.skipped,
            "failed": self.failed,
            "by_type": dict(self.by_type),
            "plan": [
                {
                    "slug": p.slug,
                    "old_path": p.old_path,
                    "new_path": p.new_path,
                    "inferred_type": p.inferred_type,
                    "title": p.title,
                }
                for p in self.plan
            ],
            "total_cost_usd": round(self.total_cost_usd, 4),
            "stopped_reason": self.stopped_reason,
            "max_pages": self.max_pages,
            "apply": self.apply,
        }


# ── Single-flight state ───────────────────────────────────────────────────────


@dataclass
class _MigrationState:
    is_running: bool = False
    last_summary: MigrationSummary | None = None
    current: dict[str, Any] = field(default_factory=dict)


_state = _MigrationState()


def is_running() -> bool:
    """True if a migration run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> MigrationSummary | None:
    """Return the summary of the most recently COMPLETED migration run."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None) -> int:
    """Freeze + clamp the run bound (I7). None → module default; over cap → clamped."""
    default = int(getattr(settings, "migrate_lint_stubs_max_pages", DEFAULT_MAX_PAGES))
    mp = default if max_pages is None else int(max_pages)
    return max(1, min(mp, MAX_PAGES_HARD_CAP))


# ── Public run entrypoint ─────────────────────────────────────────────────────


async def run_migration(
    vault_id: str,
    *,
    apply: bool = False,
    max_pages: int | None = None,
) -> MigrationSummary:
    """
    Run one bounded migration of lint-stub query pages → entity/concept (P0-5).

    ``apply=False`` (default) is dry-run: builds and returns the plan without touching
    any file or DB row.  ``apply=True`` performs the actual moves.

    Sets the single-flight flag for its whole duration; a concurrent call while
    :func:`is_running` should be rejected by the endpoint (409).  Never raises —
    a fatal error is recorded as ``stopped_reason="error"``.
    """
    mp = clamp_bounds(max_pages)
    summary = MigrationSummary(max_pages=mp, apply=apply)

    _state.is_running = True
    _state.current = {"max_pages": mp, "apply": apply}

    try:
        await _run_inner(
            vault_id=vault_id,
            max_pages=mp,
            apply=apply,
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 — never propagate into a background task
        summary.stopped_reason = "error"
        logger.warning("migrate-lint-stubs: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    # Completion log line (I7 parity)
    logger.info(
        "migrate-lint-stubs: processed=%d moved=%d skipped=%d failed=%d "
        "cost_usd=%.4f stopped_reason=%s by_type=%s apply=%s vault=%s",
        summary.processed,
        summary.moved,
        summary.skipped,
        summary.failed,
        summary.total_cost_usd,
        summary.stopped_reason,
        summary.by_type,
        apply,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: migrate-lint-stubs total_cost_usd=%.4f > $%.2f (vault=%s)",
            summary.total_cost_usd,
            _COST_ANOMALY_THRESHOLD_USD,
            vault_id,
        )
    return summary


# ── Inner loop ────────────────────────────────────────────────────────────────


async def _run_inner(
    *,
    vault_id: str,
    max_pages: int,
    apply: bool,
    summary: MigrationSummary,
) -> None:
    """Bounded per-page loop (I7). Bumps data_version once at the end if anything moved."""
    from app.ingest.orchestrator import bump_version  # noqa: PLC0415
    from app.ops.lint import _infer_stub_page_type  # noqa: PLC0415  (D1 reuse — do NOT copy)

    candidates = await _load_candidate_stubs(vault_id, max_pages)

    if len(candidates) >= max_pages:
        # The DB limit was hit; there may be more candidates beyond this batch.
        # We report this AFTER the loop (like reclassify_types) so the final
        # moved/processed counts are correct.
        pass

    moved_any = False

    for page in candidates:
        summary.processed += 1
        title = page.title or ""

        # ── Derive target type (deterministic, no LLM) ────────────────────────
        new_type_enum = _infer_stub_page_type(title)
        new_type = new_type_enum.value  # "entity" | "concept"

        # ── Compute new path ──────────────────────────────────────────────────
        from app.ingest.schemas import type_subdir  # noqa: PLC0415

        new_subdir = type_subdir(new_type_enum)
        # slug = filename stem (unchanged — slug resolution is folder-independent)
        old_path = page.file_path  # e.g. "wiki/queries/aws.md"
        stem = Path(old_path).stem
        new_path = f"wiki/{new_subdir}/{stem}.md"

        plan_item = MigrationPlanItem(
            slug=stem,
            old_path=old_path,
            new_path=new_path,
            inferred_type=new_type,
            title=title,
        )
        summary.plan.append(plan_item)

        if not apply:
            # Dry-run: record the plan item and continue.
            continue

        # ── Apply: move file + update DB ──────────────────────────────────────
        try:
            await _move_and_retype(page, new_type, new_path)
            moved_any = True
            summary.moved += 1
            summary.by_type[new_type] = summary.by_type.get(new_type, 0) + 1
            logger.debug(
                "migrate-lint-stubs: moved %s → %s (type=%s)", old_path, new_path, new_type
            )
        except Exception as exc:  # noqa: BLE001 — per-page, non-fatal
            summary.failed += 1
            logger.warning("migrate-lint-stubs: failed to move %s (skipped): %s", old_path, exc)

    # After the loop: post-apply actions (idempotent even if partially applied).
    if apply and moved_any:
        # Re-resolve dangling links ONCE after all moves: any [[Title]] that was
        # targeting a now-moved page will connect to the new page id (title unchanged).
        await _reconnect_links()

        # Single data_version bump for the whole batch (never per-page, I1).
        await bump_version()

    # Stopped reason
    if summary.stopped_reason == "complete" and len(candidates) >= max_pages:
        summary.stopped_reason = "maxpages"


# ── Candidate loading ─────────────────────────────────────────────────────────


async def _load_candidate_stubs(vault_id: str, max_pages: int) -> list[Any]:
    """
    Bounded indexed read of live query pages under wiki/queries/ (I1 — no full-rescan).

    Candidate filter (two independent conditions — either is sufficient):
      1. ``tags`` JSONB array contains BOTH ``"stub"`` AND ``"lint"`` strings.
      2. Body text contains either the legacy or current placeholder sentence.

    Condition 1 is evaluated in Python after a narrow SQL pre-filter on
    ``type='query' AND file_path LIKE 'wiki/queries/%'``.  CAST(col AS TEXT) LIKE
    is used so the same query works on SQLite (tests) and Postgres (prod) — the
    JSONB ``@>`` operator is Postgres-only (memory note: Raw-SQL SQLite tests vs
    Postgres runtime).

    Already-moved pages (no longer under wiki/queries/) naturally drop out of the
    query on re-run → idempotent without an ``_examined_ids`` set.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import Page  # noqa: PLC0415

    # SQL pre-filter: narrow to query-typed pages under wiki/queries/ (index scan, I1).
    # cast(Page.tags, String) is portable — CAST(jsonb AS text) on Postgres,
    # CAST(json AS TEXT) on SQLite.
    # Note: the LIKE checks are done in Python below for portability and correctness.
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page)
                    .where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.file_path.like("wiki/queries/%"),
                        Page.page_type == "query",
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

    # Python-side stub filter: tags check (fast, no file I/O) first; body check fallback.
    vault_root = settings.vault_root
    results: list[Any] = []
    for page in rows:
        if _is_lint_stub(page, vault_root):
            results.append(page)

    return results


def _is_lint_stub(page: Any, vault_root: Path) -> bool:
    """
    Return True if *page* is a lint-generated stub (tags-first, body-fallback).

    Priority:
      1. Tags contain both ``"stub"`` and ``"lint"`` → True (no file I/O).
      2. File body contains either placeholder sentence → True.
      3. Otherwise → False (not a stub or unreadable — leave untouched).
    """
    # Fast path: check JSONB tags in-memory.
    tags: list[str] = page.tags or []
    if "stub" in tags and "lint" in tags:
        return True

    # Slow path: read file body (handles stubs without tags, e.g. hand-edited).
    abs_path = (vault_root / page.file_path).resolve()
    try:
        body = abs_path.read_text(encoding="utf-8")
    except OSError:
        return False

    return LEGACY_PLACEHOLDER_BODY in body or CURRENT_STUB_BODY in body


# ── Move + retype ─────────────────────────────────────────────────────────────


async def _move_and_retype(page: Any, new_type: str, new_rel_path: str) -> None:
    """
    Move *page* from ``wiki/queries/<slug>.md`` to ``wiki/<type_subdir>/<slug>.md``,
    rewriting the frontmatter ``type`` key and updating the DB row.

    Steps:
      1. Read old file (frontmatter + body via python-frontmatter).
      2. Rewrite ``type:`` in frontmatter.
      3. If body contains the LEGACY placeholder sentence, upgrade it to the current
         stub body (keeps the page usable after migration).
      4. Write new file at the new path; create parent dirs if needed.
      5. Delete old file.
      6. UPDATE ``pages`` row: ``file_path``, ``page_type``, ``content_hash``.
      7. Re-embed via ``upsert_vector`` to keep the Qdrant payload's ``type`` in sync.

    No ``data_version`` bump here — the caller does ONE bump after the whole batch (I1).
    """
    import frontmatter as _fm  # python-frontmatter  # noqa: PLC0415
    from sqlalchemy import update as sa_update  # noqa: PLC0415

    from app.ingest.orchestrator import _sha256, upsert_vector  # noqa: PLC0415
    from app.models import Page  # noqa: PLC0415

    vault_root = settings.vault_root
    old_abs = (vault_root / page.file_path).resolve()
    new_abs = (vault_root / new_rel_path).resolve()

    if not old_abs.exists():
        raise FileNotFoundError(f"migrate-lint-stubs: source file missing: {old_abs}")

    # ── 1. Read + parse ───────────────────────────────────────────────────────
    raw_text = old_abs.read_text(encoding="utf-8")
    post = _fm.loads(raw_text)

    # ── 2. Rewrite frontmatter type ───────────────────────────────────────────
    post["type"] = new_type

    # ── 3. Upgrade legacy body if present ────────────────────────────────────
    body: str = post.content or ""
    if LEGACY_PLACEHOLDER_BODY in body:
        title = page.title or post.get("title", "")
        post.content = (
            f"# {title}\n\n{CURRENT_STUB_BODY}\n"
            if title
            else body.replace(LEGACY_PLACEHOLDER_BODY, CURRENT_STUB_BODY)
        )

    # ── 4. Write new file ─────────────────────────────────────────────────────
    new_text = _fm.dumps(post) + "\n"
    new_bytes = new_text.encode("utf-8")
    new_abs.parent.mkdir(parents=True, exist_ok=True)
    new_abs.write_text(new_text, encoding="utf-8")

    # ── 5. Delete old file ────────────────────────────────────────────────────
    old_abs.unlink()

    # ── 6. Update DB row (file_path + page_type + content_hash) ──────────────
    new_hash = _sha256(new_bytes)
    async with get_session() as session:
        await session.execute(
            sa_update(Page)
            .where(Page.id == page.id)
            .values(
                file_path=new_rel_path,
                page_type=new_type,
                content_hash=new_hash,
            )
        )

    # ── 7. Re-embed (Qdrant payload type update, I1) ──────────────────────────
    body_for_embedding = _fm.loads(new_text).content
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=new_rel_path,
        title=page.title,
        page_type=new_type,
    )


async def _reconnect_links() -> None:
    """
    Re-resolve all dangling wikilinks after the move batch (K5 / ADR-0037 B1).

    One session, one bulk query — the same seam used by the broken-link stub fixer and
    POST /links/reresolve (no N+1, I1).  Title-based resolution means moved pages
    (same title, new folder) reconnect automatically.
    """
    from app.wiki.links import reresolve_dangling_links  # noqa: PLC0415

    try:
        async with get_session() as session:
            reconnected = await reresolve_dangling_links(session)
        logger.info(
            "migrate-lint-stubs: reresolve_dangling_links reconnected %d links", reconnected
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("migrate-lint-stubs: reresolve_dangling_links failed: %s", exc)


# ── CLI guard ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description=(
            "Migrate lint-stub query pages to entity/concept (ADR-0067 D1 / P0-5). "
            "DRY-RUN by default — pass --apply to perform the actual migration."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Perform the actual migration (default: dry-run only).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=(
            f"Maximum pages to process "
            f"(default: {DEFAULT_MAX_PAGES}, hard cap: {MAX_PAGES_HARD_CAP})."
        ),
    )
    parser.add_argument(
        "--vault-id",
        type=str,
        default=settings.vault_id,
        help=f"Vault identifier (default: {settings.vault_id!r} from VAULT_ID env).",
    )
    args = parser.parse_args()

    async def _main() -> None:
        summary = await run_migration(
            args.vault_id,
            apply=args.apply,
            max_pages=args.max_pages,
        )
        import json

        print(json.dumps(summary.as_dict(), indent=2))

    asyncio.run(_main())
