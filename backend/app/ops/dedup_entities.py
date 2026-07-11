"""
ops/dedup_entities.py — ADR-0067 D5 retrofit sweep.

Clusters live ``type=entity`` pages by their EXACT canonical key
(``_resolve_canonical_entity_key`` imported from ingest/orchestrator.py — NEVER
re-implemented here).  Any key shared by ≥2 live pages is an alias cluster (e.g. three
AWS variant pages).  FUZZY / EMBEDDING similarity is NEVER a merge trigger: two genuinely
different entities (``Deloitte`` vs ``Deloitte Italia``) MUST NOT collide.

Public API
----------
    run_dedup(vault_id, *, apply=False, propose_to_review=True, max_clusters=None)
                              → DedupSummary

Modes (applied in precedence order if combined)
-----------------------------------------------
  dry-run  (apply=False, propose_to_review=False):
      Report clusters only — zero writes, zero Qdrant ops, zero DB mutations.

  propose  (apply=False, propose_to_review=True, DEFAULT):
      Enqueue each cluster as a ``duplicate`` review item via ``enqueue_review``
      (F9 / human-gated K8).  One idempotent UPSERT per cluster (content_key dedup).

  apply    (apply=True):
      For each cluster pick a canonical target (shortest title → highest inbound-
      degree tiebreak), union sources[], merge bodies (deterministic concat; cost=0),
      repoint inbound wikilinks, soft-delete alias pages (DB + Qdrant).  One
      ``data_version`` bump for the whole batch at the end (I1).

Safety (Q5 / ADR-0067 D5)
--------------------------
  EXACT normalized-key match only.  Clusters that are only fuzzy-similar (different
  canonical keys) are NOT touched — they are a separate problem (embedding similarity
  review is a future-sprint topic).

Structural mirrors: ops/migrate_lint_query_stubs.py
  – single-flight state (``is_running`` / ``get_last_summary``)
  – I7 bounds (``max_clusters`` + ``clamp_bounds``)
  – I1 (one ``data_version`` bump per batch, never per-page)
  – cost=0 (deterministic, no LLM calls); ``total_cost_usd`` always 0.0

Reuse points (explicit — no re-implementation)
----------------------------------------------
  ``_resolve_canonical_entity_key`` — ingest/orchestrator.py (canonical key function)
  ``bump_version``                  — ingest/orchestrator.py (data_version bump)
  ``_sha256``                       — ingest/orchestrator.py (content hash)
  ``persist_metadata``              — ingest/orchestrator.py (DB upsert)
  ``upsert_vector``                 — ingest/orchestrator.py (Qdrant embed + upsert)
  ``enqueue_review``                — ops/review.py (F9 human-gated propose seam)
  ``reresolve_dangling_links``      — wiki/links.py (bulk wikilink reconnect)
  ``delete_point``                  — qdrant_client.py (hard-delete Qdrant point)
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.db import get_session

logger = logging.getLogger(__name__)

# ── Bounds (I7) ───────────────────────────────────────────────────────────────

DEFAULT_MAX_CLUSTERS: int = 50
MAX_CLUSTERS_HARD_CAP: int = 500

# Cost sentinel — always 0.0 here (no LLM calls). Kept for structural parity.
_COST_ANOMALY_THRESHOLD_USD: float = 0.01

# Body shorter than this is treated as a stub / placeholder — not worth appending
# when merging alias bodies into the canonical body (deterministic, cost=0).
_MIN_ALIAS_BODY_CHARS: int = 40


# ── Result DTOs ───────────────────────────────────────────────────────────────


@dataclass
class DedupClusterInfo:
    """Report item for one alias cluster (reported in all modes)."""

    canonical_key: str
    canonical_title: str
    canonical_page_id: str  # str UUID
    member_titles: list[str]
    member_page_ids: list[str]  # str UUIDs


@dataclass
class DedupSummary:
    """Outcome of one run_dedup() call."""

    processed_clusters: int = 0
    merged_clusters: int = 0  # apply mode: clusters fully applied
    proposed_clusters: int = 0  # propose mode: review items enqueued
    skipped_clusters: int = 0
    failed_clusters: int = 0
    aliases_soft_deleted: int = 0
    clusters: list[DedupClusterInfo] = field(default_factory=list)
    total_cost_usd: float = 0.0  # always 0.0 — deterministic, no LLM
    stopped_reason: str = "complete"  # complete | maxclusters | error
    max_clusters: int = 0
    apply: bool = False
    propose_to_review: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed_clusters": self.processed_clusters,
            "merged_clusters": self.merged_clusters,
            "proposed_clusters": self.proposed_clusters,
            "skipped_clusters": self.skipped_clusters,
            "failed_clusters": self.failed_clusters,
            "aliases_soft_deleted": self.aliases_soft_deleted,
            "clusters": [
                {
                    "canonical_key": c.canonical_key,
                    "canonical_title": c.canonical_title,
                    "canonical_page_id": c.canonical_page_id,
                    "member_titles": c.member_titles,
                    "member_page_ids": c.member_page_ids,
                }
                for c in self.clusters
            ],
            "total_cost_usd": round(self.total_cost_usd, 4),
            "stopped_reason": self.stopped_reason,
            "max_clusters": self.max_clusters,
            "apply": self.apply,
            "propose_to_review": self.propose_to_review,
        }


# ── Single-flight state ───────────────────────────────────────────────────────


@dataclass
class _DedupState:
    is_running: bool = False
    last_summary: DedupSummary | None = None
    current: dict[str, Any] = field(default_factory=dict)


_state = _DedupState()


def is_running() -> bool:
    """True if a dedup run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> DedupSummary | None:
    """Return the summary of the most recently COMPLETED dedup run."""
    return _state.last_summary


def clamp_bounds(max_clusters: int | None) -> int:
    """Freeze + clamp the run bound (I7). None → module default; over hard cap → clamped."""
    default = int(getattr(settings, "dedup_entities_max_clusters", DEFAULT_MAX_CLUSTERS))
    mc = default if max_clusters is None else int(max_clusters)
    return max(1, min(mc, MAX_CLUSTERS_HARD_CAP))


# ── Public API ────────────────────────────────────────────────────────────────


async def run_dedup(
    vault_id: str,
    *,
    apply: bool = False,
    propose_to_review: bool = True,
    max_clusters: int | None = None,
) -> DedupSummary:
    """
    Run one bounded dedup sweep of entity pages for *vault_id* (ADR-0067 D5).

    ``apply=False, propose_to_review=True`` (default):
        Enqueue each alias cluster as a ``duplicate`` review item (F9 / K8 human-gated).
        Idempotent — re-running on the same vault refreshes pending items.

    ``apply=False, propose_to_review=False``:
        Pure dry-run: return the cluster report, touch nothing.

    ``apply=True``:
        Merge each cluster: union sources[], concatenate unique bodies, repoint inbound
        wikilinks, soft-delete aliases.  One ``data_version`` bump at the end (I1).
        ``propose_to_review`` is ignored when ``apply=True``.

    Sets the single-flight flag (``is_running()``) for the whole duration; a concurrent
    call while running should be rejected by the caller (409).  Never raises — fatal
    errors are recorded as ``stopped_reason="error"`` and returned in the summary.
    """
    mc = clamp_bounds(max_clusters)
    summary = DedupSummary(max_clusters=mc, apply=apply, propose_to_review=propose_to_review)

    _state.is_running = True
    _state.current = {"max_clusters": mc, "apply": apply, "propose_to_review": propose_to_review}

    try:
        await _run_inner(
            vault_id=vault_id,
            max_clusters=mc,
            apply=apply,
            propose_to_review=propose_to_review,
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 — never propagate into a background task
        summary.stopped_reason = "error"
        logger.warning("dedup-entities: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    logger.info(
        "dedup-entities: processed=%d merged=%d proposed=%d failed=%d "
        "aliases_deleted=%d cost_usd=%.4f stopped_reason=%s apply=%s vault=%s",
        summary.processed_clusters,
        summary.merged_clusters,
        summary.proposed_clusters,
        summary.failed_clusters,
        summary.aliases_soft_deleted,
        summary.total_cost_usd,
        summary.stopped_reason,
        apply,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: dedup-entities total_cost_usd=%.4f > $%.2f (vault=%s)",
            summary.total_cost_usd,
            _COST_ANOMALY_THRESHOLD_USD,
            vault_id,
        )
    return summary


# ── Inner loop ────────────────────────────────────────────────────────────────


async def _run_inner(
    *,
    vault_id: str,
    max_clusters: int,
    apply: bool,
    propose_to_review: bool,
    summary: DedupSummary,
) -> None:
    """
    Bounded per-cluster loop (I7).  Bumps data_version ONCE at the end when
    anything was actually merged (never per-cluster, I1).
    """
    from app.ingest.orchestrator import bump_version  # noqa: PLC0415

    clusters = await _load_entity_clusters(vault_id, max_clusters)

    if len(clusters) >= max_clusters:
        summary.stopped_reason = "maxclusters"

    merged_any = False

    for canonical_key, cluster_pages in clusters:
        summary.processed_clusters += 1

        # Inbound-degree map for canonical target selection (one bulk query, I1).
        degree_map = await _inbound_degree([p.id for p in cluster_pages])
        canonical = _pick_canonical(cluster_pages, degree_map)
        aliases = [p for p in cluster_pages if str(p.id) != str(canonical.id)]

        cluster_info = DedupClusterInfo(
            canonical_key=canonical_key,
            canonical_title=canonical.title or "",
            canonical_page_id=str(canonical.id),
            member_titles=[p.title or "" for p in cluster_pages],
            member_page_ids=[str(p.id) for p in cluster_pages],
        )
        summary.clusters.append(cluster_info)

        if apply:
            # Apply mode: merge aliases into canonical.
            try:
                deleted = await _apply_cluster(canonical, aliases)
                merged_any = True
                summary.merged_clusters += 1
                summary.aliases_soft_deleted += deleted
                logger.debug(
                    "dedup-entities: merged key=%r canonical=%s aliases=%d",
                    canonical_key,
                    canonical.file_path,
                    len(aliases),
                )
            except Exception as exc:  # noqa: BLE001 — per-cluster, non-fatal
                summary.failed_clusters += 1
                logger.warning(
                    "dedup-entities: apply failed for key=%r (skipped): %s",
                    canonical_key,
                    exc,
                )

        elif propose_to_review:
            # Propose mode: enqueue a review item for human decision (K8 / F9).
            try:
                await _propose_cluster(vault_id, canonical_key, cluster_pages, canonical)
                summary.proposed_clusters += 1
            except Exception as exc:  # noqa: BLE001 — per-cluster, non-fatal
                summary.failed_clusters += 1
                logger.warning(
                    "dedup-entities: propose failed for key=%r (skipped): %s",
                    canonical_key,
                    exc,
                )

        # else: pure dry-run — cluster recorded in summary.clusters above; nothing else.

    # After the loop: post-apply actions (idempotent even if partially applied).
    if apply and merged_any:
        # Re-resolve ALL dangling links ONCE after the whole batch (title-based
        # resolution is unchanged; the canonical page keeps its title so all
        # [[Title]] links that targeted aliases will now resolve to the canonical).
        await _reconnect_links()

        # Single data_version bump for the whole batch — NEVER per-cluster (I1).
        await bump_version()

    # Final stopped_reason (don't override "error" or "maxclusters")
    if summary.stopped_reason == "complete" and len(clusters) >= max_clusters:
        summary.stopped_reason = "maxclusters"


# ── Cluster loading (indexed DB read, I1) ─────────────────────────────────────


async def _load_entity_clusters(
    vault_id: str,
    max_clusters: int,
) -> list[tuple[str, list[Any]]]:
    """
    Load live entity pages grouped by canonical key (ADR-0067 D5 / I1).

    Returns ``(canonical_key, [Page, ...])`` tuples for groups with ≥2 members,
    sorted by canonical_key for deterministic ordering.  Bounded by *max_clusters*
    (applied AFTER grouping so we don't load extra pages on every iteration — the
    total entity count is bounded by the vault, not the cluster count).

    Indexed query: ``page_type='entity' AND deleted_at IS NULL AND title IS NOT NULL``.
    No N+1, no full vault re-scan (I1).
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.ingest.orchestrator import _resolve_canonical_entity_key  # noqa: PLC0415
    from app.models import Page  # noqa: PLC0415

    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(Page)
                    .where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.page_type == "entity",
                        Page.title.is_not(None),
                    )
                    .order_by(Page.title)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            session.expunge(r)

    # Group by canonical key in Python (avoids JSONB aggregation complexity).
    groups: dict[str, list[Any]] = {}
    for page in rows:
        key = _resolve_canonical_entity_key(page.title or "")
        if not key:
            continue
        groups.setdefault(key, []).append(page)

    # Filter to alias clusters (≥2 members); sort for determinism.
    clusters: list[tuple[str, list[Any]]] = [
        (key, pages) for key, pages in sorted(groups.items()) if len(pages) >= 2
    ]

    # Cap at max_clusters AFTER grouping (preserve cluster atomicity).
    return clusters[:max_clusters]


# ── Inbound-degree query (bulk, I1) ──────────────────────────────────────────


async def _inbound_degree(page_ids: list[Any]) -> dict[Any, int]:
    """
    Return the inbound wikilink count for each page in *page_ids* (one bulk query, I1).

    Uses ``CAST(target_page_id AS TEXT)`` for portability (SQLite stores UUIDs as
    strings; Postgres uses a native UUID column).  The result map keys match the
    original *page_ids* values so ``degree_map[p.id]`` works regardless of UUID type.
    """
    if not page_ids:
        return {}

    from sqlalchemy import String, func, select  # noqa: PLC0415
    from sqlalchemy import cast as sa_cast  # noqa: PLC0415

    from app.models import Link  # noqa: PLC0415

    id_strs = [str(pid) for pid in page_ids]

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    sa_cast(Link.target_page_id, String).label("page_id_str"),
                    func.count(Link.id).label("cnt"),
                )
                .where(sa_cast(Link.target_page_id, String).in_(id_strs))
                .group_by(sa_cast(Link.target_page_id, String))
            )
        ).all()

    # Map str → count; then reverse-map back to original page_id type.
    str_to_count: dict[str, int] = {row[0]: row[1] for row in rows}
    return {pid: str_to_count.get(str(pid), 0) for pid in page_ids}


# ── Canonical target selection ────────────────────────────────────────────────


def _pick_canonical(
    cluster_pages: list[Any],
    degree_map: dict[Any, int],
) -> Any:
    """
    Pick the canonical target from a cluster (ADR-0067 D5).

    Primary sort: shortest title by character count (the most concise form of the name).
    Tiebreak: highest inbound wikilink degree (the most widely referenced page).

    ``AWS`` (3 chars) → beats ``Amazon Web Services`` (23 chars) → beats
    ``Amazon Web Services (AWS)`` (26 chars) → beats ``Amazon Web Services Inc.`` (25 chars).
    """

    def sort_key(p: Any) -> tuple[int, int]:
        title_len = len(p.title or "")
        degree = degree_map.get(p.id, 0)
        return (title_len, -degree)  # ascending title len; descending degree

    return min(cluster_pages, key=sort_key)


# ── Apply: merge cluster ──────────────────────────────────────────────────────


async def _apply_cluster(
    canonical: Any,
    aliases: list[Any],
) -> int:
    """
    Merge *aliases* into *canonical* (sources union, body concat, link repoint,
    soft-delete). Returns the number of alias pages soft-deleted.

    Steps (per-cluster):
      1. Union sources[] from all cluster members.
      2. Read canonical body; append meaningful alias bodies (deterministic concat,
         separated by ``---``; stubs / empty bodies skipped; cost_usd=0 always).
      3. Rewrite canonical file with merged body (frontmatter preserved).
      4. ``persist_metadata`` canonical row (unioned sources, new content_hash).
      5. ``upsert_vector`` canonical (Qdrant payload + embedding for merged text).
      6. Repoint inbound wikilinks from each alias → canonical (CAST-portable UPDATE).
      7. Soft-delete each alias: set ``deleted_at`` on the DB row + hard-delete Qdrant.
      8. Delete alias files from disk (best-effort; OSError logged, not fatal).

    No ``data_version`` bump here — the caller does ONE bump after the whole batch (I1).
    """
    import frontmatter as _fm  # noqa: PLC0415

    from app.ingest.orchestrator import _sha256, persist_metadata, upsert_vector  # noqa: PLC0415
    from app.qdrant_client import delete_point  # noqa: PLC0415

    vault_root = settings.vault_root

    # ── 1. Union sources ──────────────────────────────────────────────────────
    all_sources: list[str] = list(canonical.sources or [])
    for alias in aliases:
        for src in alias.sources or []:
            if src not in all_sources:
                all_sources.append(src)

    # ── 2 + 3. Read, merge, write canonical file ──────────────────────────────
    canonical_abs = (vault_root / canonical.file_path).resolve()
    canonical_text = ""
    if canonical_abs.exists():
        canonical_text = canonical_abs.read_text(encoding="utf-8")

    try:
        canonical_post = _fm.loads(canonical_text) if canonical_text else _fm.Post("")
        canonical_body = (canonical_post.content or "").strip()
    except Exception:
        canonical_body = ""

    extra_bodies: list[str] = []
    for alias in aliases:
        alias_abs = (vault_root / alias.file_path).resolve()
        if not alias_abs.exists():
            continue
        try:
            alias_text = alias_abs.read_text(encoding="utf-8")
            alias_post = _fm.loads(alias_text)
            alias_body = (alias_post.content or "").strip()
        except Exception as exc:  # noqa: BLE001 — best-effort body read; skip on any error
            logger.debug("dedup-entities: skipping alias body for %s: %s", alias.file_path, exc)
            continue
        # Skip trivially empty or stub-sized bodies, and skip pure duplicates.
        if len(alias_body) >= _MIN_ALIAS_BODY_CHARS and alias_body != canonical_body:
            extra_bodies.append(alias_body)

    if extra_bodies:
        parts = [canonical_body] + extra_bodies
        merged_body = "\n\n---\n\n".join(p for p in parts if p)
    else:
        merged_body = canonical_body

    # Rebuild the file with the merged body (frontmatter is preserved from canonical).
    try:
        if canonical_text:
            post = _fm.loads(canonical_text)
        else:
            post = _fm.Post("")
        post.content = merged_body
        new_text = _fm.dumps(post) + "\n"
    except Exception:
        # Last-resort fallback: don't corrupt the file.
        new_text = canonical_text or ""
        merged_body = canonical_body

    new_bytes = new_text.encode("utf-8")
    canonical_abs.parent.mkdir(parents=True, exist_ok=True)
    canonical_abs.write_text(new_text, encoding="utf-8")
    new_hash = _sha256(new_bytes)

    # ── 4. Persist canonical metadata (DB upsert) ─────────────────────────────
    await persist_metadata(
        page_id=canonical.id,
        vault_id=canonical.vault_id,
        file_path=canonical.file_path,
        title=canonical.title,
        page_type=canonical.page_type,
        sources=all_sources or None,
        tags=list(canonical.tags) if canonical.tags else None,
        content_hash=new_hash,
        source_mtime_ns=canonical.source_mtime_ns or 0,
    )

    # ── 5. Re-embed canonical (Qdrant upsert) ─────────────────────────────────
    await upsert_vector(
        page_id=canonical.id,
        text=merged_body,
        file_path=canonical.file_path,
        title=canonical.title,
        page_type=canonical.page_type,
    )

    # ── 6–8. Alias clean-up ────────────────────────────────────────────────────
    deleted_count = 0
    for alias in aliases:
        # 6. Repoint inbound wikilinks from alias → canonical (CAST-portable).
        try:
            repointed = await _repoint_links(alias.id, canonical.id)
            logger.debug(
                "dedup-entities: repointed %d links from %s → %s",
                repointed,
                alias.file_path,
                canonical.file_path,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("dedup-entities: link repoint failed for alias %s: %s", alias.id, exc)

        # 7. Soft-delete alias DB row.
        try:
            await _soft_delete_page(alias.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dedup-entities: soft-delete failed for alias %s: %s", alias.id, exc)

        # 7b. Hard-delete Qdrant point for alias (ADR-0005).
        try:
            await delete_point(alias.id)
        except Exception as exc:  # noqa: BLE001 — Qdrant may have no point for the alias
            logger.debug("dedup-entities: Qdrant delete skipped for alias %s: %s", alias.id, exc)

        # 8. Delete alias file from disk (best-effort).
        alias_abs = (vault_root / alias.file_path).resolve()
        if alias_abs.exists():
            try:
                alias_abs.unlink()
                logger.debug("dedup-entities: deleted alias file %s", alias.file_path)
            except OSError as exc:
                logger.warning(
                    "dedup-entities: file delete failed for %s: %s", alias.file_path, exc
                )

        deleted_count += 1

    return deleted_count


# ── Repoint inbound links (CAST-portable) ────────────────────────────────────


async def _repoint_links(
    alias_id: Any,
    canonical_id: Any,
) -> int:
    """
    Repoint all Link rows whose target_page_id = *alias_id* to *canonical_id*.

    Uses CAST(target_page_id AS TEXT) for WHERE + ORM mutation pattern for the SET
    so the query is portable across SQLite (tests) and Postgres (production).
    Returns the number of rows updated.
    """
    from sqlalchemy import String, select  # noqa: PLC0415
    from sqlalchemy import cast as sa_cast  # noqa: PLC0415

    from app.models import Link  # noqa: PLC0415

    alias_str = str(alias_id)

    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(Link).where(sa_cast(Link.target_page_id, String) == alias_str)
                )
            )
            .scalars()
            .all()
        )

        for link in rows:
            # Normalise to UUID so SQLAlchemy type-binds correctly on Postgres.
            link.target_page_id = (
                uuid.UUID(str(canonical_id)) if isinstance(canonical_id, str) else canonical_id
            )
            link.dangling = False  # canonical is live; no longer dangling

    return len(rows)


# ── Soft-delete alias page ────────────────────────────────────────────────────


async def _soft_delete_page(page_id: Any) -> None:
    """
    Tombstone the Page row for *page_id* (ADR-0005 soft-delete convention).
    Sets ``deleted_at = now(UTC)`` on the row.
    """
    from sqlalchemy import String, select  # noqa: PLC0415
    from sqlalchemy import cast as sa_cast  # noqa: PLC0415

    from app.models import Page  # noqa: PLC0415

    page_id_str = str(page_id)
    now = datetime.now(UTC)

    async with get_session() as session:
        row = await session.execute(select(Page).where(sa_cast(Page.id, String) == page_id_str))
        page = row.scalar_one_or_none()
        if page is not None:
            page.deleted_at = now


# ── Propose cluster to review queue ──────────────────────────────────────────


async def _propose_cluster(
    vault_id: str,
    canonical_key: str,
    cluster_pages: list[Any],
    canonical: Any,
) -> None:
    """
    Enqueue one alias cluster as a ``duplicate`` review item (F9 / K8 human-gated).

    Idempotent: the content_key is a stable 16-hex hash of
    ``"dedup:<vault_id>:<canonical_key>"`` so re-running refreshes pending items
    rather than inserting duplicates (ADR-0044 §3.4 via ``enqueue_review``).

    Proposed data:
      - ``item_type = "duplicate"``
      - ``proposed_title``   = canonical page title (the suggested merge target)
      - ``proposed_page_type`` = "entity"
      - ``rationale``        = human-readable cluster summary (member titles + IDs)
      - ``referenced_page_ids`` = all cluster member IDs (the [[wikilink]] seeds)
      - ``page_id``          = canonical page ID (primary conflict anchor)
    """
    from app.ops.review import enqueue_review  # noqa: PLC0415

    canonical_title = canonical.title or canonical_key
    member_titles = [p.title or "" for p in cluster_pages]
    ref_ids = [str(p.id) for p in cluster_pages]

    rationale = (
        f"Entity alias cluster detected (ADR-0067 D5). "
        f"Canonical key: {canonical_key!r}. "
        f"Members: {', '.join(repr(t) for t in member_titles)}. "
        f"Suggested canonical target: {canonical_title!r} "
        f"(shortest title / highest inbound-degree). "
        f"Apply dedup to merge, or Skip to keep separate."
    )

    # Stable content_key: sha256[:16] of "dedup:<vault_id>:<canonical_key>"
    raw_key = f"dedup:{vault_id}:{canonical_key}"
    content_key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]

    await enqueue_review(
        vault_id=vault_id,
        item_type="duplicate",
        proposed_title=canonical_title,
        proposed_page_type="entity",
        rationale=rationale,
        page_id=(
            canonical.id if isinstance(canonical.id, uuid.UUID) else uuid.UUID(str(canonical.id))
        ),
        content_key=content_key,
        referenced_page_ids=ref_ids,
    )


# ── Post-batch link reconnect ──────────────────────────────────────────────────


async def _reconnect_links() -> None:
    """
    Re-resolve all dangling wikilinks once after the whole apply batch (K5 / I1).

    Title-based resolution (same title → same page): alias pages that were soft-
    deleted keep their titles as ``target_title`` on dangling rows. Those links now
    resolve to the canonical page (same title, still live) via the tolerant 3-step
    matcher in ``reresolve_dangling_links``.

    One session, one bulk pass — no N+1 (I1).
    """
    from app.wiki.links import reresolve_dangling_links  # noqa: PLC0415

    try:
        async with get_session() as session:
            reconnected = await reresolve_dangling_links(session)
        logger.info("dedup-entities: reresolve_dangling_links reconnected %d links", reconnected)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dedup-entities: reresolve_dangling_links failed: %s", exc)


# ── CLI guard ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(
        description=(
            "Dedup entity pages by exact canonical key (ADR-0067 D5). "
            "DRY-RUN by default — pass --propose to enqueue review items or "
            "--apply to perform the actual merge."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Apply merges: union sources, concat bodies, repoint inbound wikilinks, "
            "soft-delete aliases (default: dry-run only)."
        ),
    )
    group.add_argument(
        "--propose",
        action="store_true",
        default=False,
        help=(
            "Enqueue each cluster as a 'duplicate' review item (F9 human-gated). "
            "Does NOT write pages. Idempotent."
        ),
    )
    parser.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help=(
            f"Maximum clusters to process "
            f"(default: {DEFAULT_MAX_CLUSTERS}, hard cap: {MAX_CLUSTERS_HARD_CAP})."
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
        summary = await run_dedup(
            args.vault_id,
            apply=args.apply,
            propose_to_review=args.propose,
            max_clusters=args.max_clusters,
        )
        print(json.dumps(summary.as_dict(), indent=2))

    asyncio.run(_main())
