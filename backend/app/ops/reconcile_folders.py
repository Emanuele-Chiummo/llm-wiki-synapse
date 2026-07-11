"""
ops/reconcile_folders.py — Reconcile wiki page physical folder vs. declared type [K1,I1].

Bounded, idempotent, DRY-RUN-BY-DEFAULT op that physically moves wiki pages whose
filesystem folder does not match the subfolder implied by their ``type`` frontmatter
(``entities/``, ``concepts/``, ``sources/``, ``synthesis/``, ``comparisons/``,
``queries/``).

Root cause: ``ops/reclassify_types.py`` updates ``pages.page_type`` and the frontmatter
``type:`` key but deliberately does NOT move the file (it is a metadata-only op). This
reconcile sweep is the complementary step: read the current ``type`` from DB, move the
file to the correct subfolder, and update the DB + Qdrant payload so every data store
agrees. The slug (filename stem) is NEVER changed — wikilinks resolve by title/slug, not
path (K5), so ``[[Foo]]`` keeps resolving to the same page after the move.

Architecture mirrors ``ops/migrate_lint_query_stubs.py``:
  - Module-level single-flight state (409 guard from the endpoint).
  - Deterministic — zero LLM calls; ``total_cost_usd = 0.0`` always.
  - Bounded by ``max_pages`` (no per-page cost, no token budget, I7).
  - One ``data_version`` bump at the end for the whole batch (never per-page, I1).
  - Idempotent: re-running on already-moved pages finds 0 candidates (I1).
  - ``apply=False`` (default): builds and returns the plan without touching any file or
    DB row. ``apply=True``: performs moves + updates DB + updates Qdrant + reconnects
    wikilinks.
  - Collision safety: if the destination path already exists, skip + log (never overwrite).

Reuse points (explicit, no re-implementation):
  - ``reresolve_dangling_links``  — wiki/links.py  (wikilink reconnect after batch, K5)
  - ``bump_version``              — ingest/orchestrator.py  (data_version bump, I1)
  - ``upsert_vector``             — ingest/orchestrator.py  (Qdrant payload file_path update)
  - ``_sha256``                   — ingest/orchestrator.py  (content hash recompute)
  - ``type_subdir``               — ingest/schemas.py  (folder routing by PageType)
  - ``PageType``                  — ingest/schemas.py  (valid type guard; excludes reserved)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import get_session

logger = logging.getLogger(__name__)

# ── Reserved filenames — never moved regardless of type (K3/F3/K4) ────────────
# These pages live at wiki/<name>.md (root level) and must not be relocated.
_RESERVED_FILENAMES: frozenset[str] = frozenset({"overview.md", "index.md", "log.md"})

# Reserved page_type values — never candidates even if they land in a subfolder.
# PageType enum covers user-content types; these are the catalogue/meta-only types.
_RESERVED_TYPES: frozenset[str] = frozenset({"overview", "index", "log", "untyped"})

# ── Bounds (I7) ───────────────────────────────────────────────────────────────

DEFAULT_MAX_PAGES: int = 200
MAX_PAGES_HARD_CAP: int = 1_000

_COST_ANOMALY_THRESHOLD_USD: float = 0.01  # always 0.0 for this op; guard kept for parity


# ── Result DTOs ───────────────────────────────────────────────────────────────


@dataclass
class ReconcilePlanItem:
    """One candidate page: what the reconcile sweep WOULD do (dry-run output)."""

    page_id: str  # str-ified UUID for JSON serialisation
    slug: str  # filename stem (unchanged — slug resolution is folder-independent)
    old_path: str  # current file_path in DB and on disk
    new_path: str  # target file_path after the move
    page_type: str  # the type driving the folder decision
    title: str | None  # page title for human readability in the plan


@dataclass
class ReconcileSummary:
    """Outcome of one reconcile_folders run."""

    processed: int = 0
    moved: int = 0
    skipped: int = 0
    failed: int = 0
    collision_skips: int = 0  # destination already existed — skipped, never overwritten
    by_folder: dict[str, int] = field(default_factory=dict)  # target subfolder → move count
    plan: list[ReconcilePlanItem] = field(default_factory=list)
    total_cost_usd: float = 0.0  # always 0.0 — no LLM
    stopped_reason: str = "complete"  # complete | maxpages | error
    max_pages: int = 0
    apply: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "moved": self.moved,
            "skipped": self.skipped,
            "failed": self.failed,
            "collision_skips": self.collision_skips,
            "by_folder": dict(self.by_folder),
            "plan": [
                {
                    "page_id": p.page_id,
                    "slug": p.slug,
                    "old_path": p.old_path,
                    "new_path": p.new_path,
                    "page_type": p.page_type,
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
class _ReconcileState:
    """Module-level single-flight state (read by the endpoint to 409 / report)."""

    is_running: bool = False
    last_summary: ReconcileSummary | None = None
    current: dict[str, Any] = field(default_factory=dict)


_state = _ReconcileState()


def is_running() -> bool:
    """True if a reconcile run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> ReconcileSummary | None:
    """Return the summary of the most recently COMPLETED reconcile run (None if never run)."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None) -> int:
    """Freeze + clamp the run bound (I7). None → module default; over cap → clamped."""
    default = int(getattr(settings, "reconcile_folders_max_pages", DEFAULT_MAX_PAGES))
    mp = default if max_pages is None else int(max_pages)
    return max(1, min(mp, MAX_PAGES_HARD_CAP))


# ── Public run entrypoint ─────────────────────────────────────────────────────


async def run_reconcile(
    vault_id: str,
    *,
    apply: bool = False,
    max_pages: int | None = None,
) -> ReconcileSummary:
    """
    Run one bounded folder-reconcile sweep for *vault_id*.

    ``apply=False`` (default) is dry-run: builds and returns the plan without touching
    any file or DB row. ``apply=True`` performs the actual moves.

    Sets the single-flight flag for its whole duration; a concurrent call while
    :func:`is_running` should be rejected by the endpoint (409). Never raises — a
    fatal error is recorded as ``stopped_reason="error"``.
    """
    mp = clamp_bounds(max_pages)
    summary = ReconcileSummary(max_pages=mp, apply=apply)

    _state.is_running = True
    _state.current = {"max_pages": mp, "apply": apply}

    try:
        await _run_inner(vault_id=vault_id, max_pages=mp, apply=apply, summary=summary)
    except Exception as exc:  # noqa: BLE001 — never propagate into a background task
        summary.stopped_reason = "error"
        logger.warning("reconcile-folders: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    # Completion log line (I7 parity — same shape as sibling ops)
    logger.info(
        "reconcile-folders: processed=%d moved=%d skipped=%d failed=%d "
        "collision_skips=%d cost_usd=%.4f stopped_reason=%s by_folder=%s apply=%s vault=%s",
        summary.processed,
        summary.moved,
        summary.skipped,
        summary.failed,
        summary.collision_skips,
        summary.total_cost_usd,
        summary.stopped_reason,
        summary.by_folder,
        apply,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: reconcile-folders total_cost_usd=%.4f > $%.2f (vault=%s)",
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
    summary: ReconcileSummary,
) -> None:
    """Bounded per-page loop (I7). Bumps data_version once at the end if anything moved."""
    from app.ingest.orchestrator import bump_version  # noqa: PLC0415
    from app.ingest.schemas import PageType, type_subdir  # noqa: PLC0415

    candidates, hit_sql_limit = await _load_candidates(vault_id, max_pages)
    moved_any = False

    for page in candidates:
        summary.processed += 1

        # ── Derive expected folder (defence-in-depth: _load_candidates already
        # filters invalid types, but keep the guard for robustness) ────────────
        try:
            pt = PageType(page.page_type)
        except ValueError:
            summary.skipped += 1
            logger.debug(
                "reconcile-folders: page=%s page_type=%r not a valid PageType — skip",
                page.id,
                page.page_type,
            )
            continue

        expected_folder = type_subdir(pt)
        old_path: str = page.file_path
        slug = Path(old_path).stem  # slug unchanged — only folder moves
        new_path = f"wiki/{expected_folder}/{slug}.md"

        plan_item = ReconcilePlanItem(
            page_id=str(page.id),
            slug=slug,
            old_path=old_path,
            new_path=new_path,
            page_type=page.page_type,
            title=page.title,
        )
        summary.plan.append(plan_item)

        if not apply:
            # Dry-run: record the plan item and continue without touching anything.
            continue

        # ── Apply: move file + update DB + re-embed ────────────────────────────
        try:
            await _move_page(page, new_path)
            moved_any = True
            summary.moved += 1
            summary.by_folder[expected_folder] = summary.by_folder.get(expected_folder, 0) + 1
            logger.debug(
                "reconcile-folders: moved %s → %s (type=%s)", old_path, new_path, page.page_type
            )
        except FileExistsError:
            # Destination already occupied — skip, never overwrite (defensive, I1).
            summary.collision_skips += 1
            logger.warning(
                "reconcile-folders: destination already exists: %s → %s — skip (never overwrite)",
                old_path,
                new_path,
            )
        except Exception as exc:  # noqa: BLE001 — per-page, non-fatal
            summary.failed += 1
            logger.warning("reconcile-folders: failed to move %s (skipped): %s", old_path, exc)

    # Post-apply: reconnect wikilinks + single data_version bump (I1 — once per batch).
    if apply and moved_any:
        await _reconnect_links()
        await bump_version()

    # Stopped-reason: SQL hit its limit → there may be more pages to reconcile.
    if summary.stopped_reason == "complete" and hit_sql_limit:
        summary.stopped_reason = "maxpages"


# ── Candidate loading (I1 — indexed query, no full-rescan) ───────────────────


async def _load_candidates(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
    """
    Return ``(mismatch_candidates, hit_sql_limit)``.

    SQL pre-filter (I1 — uses indexes on vault_id, deleted_at, file_path, page_type):
      - Live wiki pages (deleted_at IS NULL) under a subfolder (``wiki/%/%``).
      - Non-null, non-reserved page_type (excludes overview / index / log / untyped).
      - Excludes root-level meta files by name (NOT LIKE clauses — portable SQL).
      - LIMIT max_pages ordered by updated_at DESC (most recently reclassified first).

    Python filter: compare the actual filesystem folder derived from ``file_path`` against
    ``type_subdir(PageType(page_type))``. Only folder-mismatch pages are returned as
    candidates. Pages already in the correct folder are silently dropped here (idempotent).

    ``hit_sql_limit=True`` means the SQL LIMIT was reached; there may be more unscanned
    wiki pages. The caller reports ``stopped_reason=maxpages`` in that case.

    PORTABLE SQL — uses only LIKE / NOT LIKE / NOT IN; no Postgres-only operators (memory
    note: raw-SQL SQLite tests vs Postgres runtime; green SQLite tests prove portability).
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.ingest.schemas import PageType, type_subdir  # noqa: PLC0415
    from app.models import Page  # noqa: PLC0415

    not_reserved = tuple(_RESERVED_TYPES)

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page)
                    .where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.page_type.is_not(None),
                        Page.page_type.not_in(not_reserved),
                        # Must be in a wiki subfolder (at least two '/' after "wiki/")
                        Page.file_path.like("wiki/%/%"),
                        # Exclude root-level meta pages by name (portable NOT LIKE)
                        Page.file_path.not_like("%/overview.md"),
                        Page.file_path.not_like("%/index.md"),
                        Page.file_path.not_like("%/log.md"),
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

    hit_sql_limit: bool = len(rows) >= max_pages

    # Python-side mismatch filter: keep only pages whose current folder ≠ expected folder.
    candidates: list[Any] = []
    for page in rows:
        if page.page_type is None:
            continue  # SQL WHERE guards this; mypy narrowing guard
        try:
            pt = PageType(page.page_type)
        except ValueError:
            continue  # unknown type — not our responsibility
        expected = type_subdir(pt)
        current = _folder_from_path(page.file_path)
        if current is not None and current != expected:
            candidates.append(page)

    return candidates, hit_sql_limit


def _folder_from_path(file_path: str) -> str | None:
    """
    Extract the wiki subfolder name from a relative file_path.

    ``"wiki/concepts/foo.md"``  → ``"concepts"``
    ``"wiki/foo.md"``           → ``None``  (vault root level — not reconcilable)
    ``"wiki/a/b/foo.md"``       → ``"a"``   (first level below wiki/ is canonical)
    """
    parts = file_path.split("/")
    # Expect at least ["wiki", "<subfolder>", "<slug>.md"]
    if len(parts) >= 3 and parts[0] == "wiki":
        return parts[1]
    return None


# ── Move + DB + Qdrant ────────────────────────────────────────────────────────


async def _move_page(page: Any, new_rel_path: str) -> None:
    """
    Move *page* from its current path to *new_rel_path* (same slug, different folder).

    The frontmatter ``type:`` key is NOT touched — it is already correct (reclassify_types
    set it). Only the physical folder changes; bytes are written unchanged.

    Steps:
      1. Resolve absolute paths from ``settings.vault_root``.
      2. Guard: source must exist → ``FileNotFoundError`` if missing.
      3. Guard: destination must NOT exist → ``FileExistsError`` if it does (caller skips).
      4. Read raw bytes; recompute sha256 (bytes unchanged, hash is same as before).
      5. Write bytes to new path; create parent dirs if needed (``exist_ok=True``).
      6. Delete old file.
      7. UPDATE ``pages`` row: ``file_path`` + ``content_hash`` (idempotent values).
      8. Re-embed via ``upsert_vector`` to sync Qdrant payload's ``file_path`` field.

    No ``data_version`` bump here — the caller does ONE bump after the whole batch (I1).
    """
    import frontmatter as _fm  # python-frontmatter  # noqa: PLC0415
    from sqlalchemy import update as sa_update  # noqa: PLC0415

    from app.ingest.orchestrator import _sha256, upsert_vector  # noqa: PLC0415
    from app.models import Page  # noqa: PLC0415

    vault_root = settings.vault_root
    old_abs = (vault_root / page.file_path).resolve()
    new_abs = (vault_root / new_rel_path).resolve()

    # ── Source must exist ──────────────────────────────────────────────────────
    if not old_abs.exists():
        raise FileNotFoundError(f"reconcile-folders: source file missing: {old_abs}")

    # ── Destination must NOT exist (never overwrite) ───────────────────────────
    if new_abs.exists():
        raise FileExistsError(
            f"reconcile-folders: destination already exists: {new_abs} "
            f"(would have overwritten; source left intact: {old_abs})"
        )

    # ── Read bytes + compute hash ──────────────────────────────────────────────
    raw_bytes = old_abs.read_bytes()
    new_hash = _sha256(raw_bytes)

    # ── Write at new location + delete old ────────────────────────────────────
    new_abs.parent.mkdir(parents=True, exist_ok=True)
    new_abs.write_bytes(raw_bytes)
    old_abs.unlink()

    # ── Update DB row: file_path + content_hash ────────────────────────────────
    async with get_session() as session:
        await session.execute(
            sa_update(Page)
            .where(Page.id == page.id)
            .values(
                file_path=new_rel_path,
                content_hash=new_hash,
            )
        )

    # ── Re-embed (Qdrant payload file_path update, I1) ────────────────────────
    # The content bytes are unchanged, so the embedding vector is the same.
    # We call upsert_vector to sync the Qdrant payload's file_path field.
    try:
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        body_for_embedding = _fm.loads(raw_text).content
    except Exception:  # noqa: BLE001 — embedding is best-effort; degraded text is fine
        body_for_embedding = raw_bytes.decode("utf-8", errors="replace")

    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=new_rel_path,
        title=page.title,
        page_type=page.page_type,
    )


async def _reconnect_links() -> None:
    """
    Re-resolve all dangling wikilinks after the move batch (K5 / ADR-0037 B1).

    Title-based resolution means moved pages (same title/slug, new folder) reconnect
    automatically — ``[[Foo]]`` still resolves to the page with title ``Foo`` regardless
    of its physical folder. One bulk query, no N+1 (I1).
    """
    from app.wiki.links import reresolve_dangling_links  # noqa: PLC0415

    try:
        async with get_session() as session:
            reconnected = await reresolve_dangling_links(session)
        logger.info("reconcile-folders: reresolve_dangling_links reconnected %d links", reconnected)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconcile-folders: reresolve_dangling_links failed: %s", exc)


# ── CLI guard ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(
        description=(
            "Reconcile wiki page physical folders vs. declared type [K1,I1]. "
            "DRY-RUN by default — pass --apply to perform actual moves."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Perform the actual moves (default: dry-run only).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=(
            f"Maximum wiki pages to scan per run "
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
        summary = await run_reconcile(
            args.vault_id,
            apply=args.apply,
            max_pages=args.max_pages,
        )
        print(json.dumps(summary.as_dict(), indent=2))

    asyncio.run(_main())
