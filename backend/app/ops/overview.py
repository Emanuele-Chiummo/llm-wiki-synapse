"""
ops/overview.py — overview.md regeneration surface (ADR-0078 + drain-callback refinement).

Overview.md is NOT regenerated per-document by the ingest pipeline (ADR-0078 §3).
It IS regenerated ONCE per queue-drain via the ``on_drained`` callback in ``app.main``
(ADR-0078 refinement, v1.7.0) and on demand via POST /ops/overview/regenerate.

This module exposes the single public function used by both callers:

    regenerate_overview(analysis=None, origin_source="")

The implementation delegates to ``app.ingest.orchestrator._update_overview`` (kept there
for test coupling and backward compatibility of monkeypatches).

After a successful overwrite ``_update_overview`` bumps ``data_version`` once so the SSE
``/events`` channel notifies the frontend (post-2.1.1 fix; see docs/adr/0089-…).

Invariants:
  I1 — reads only a bounded SELECT of existing page titles (capped, vault-scoped).
  I5 — valid Obsidian frontmatter in the generated file.
  I6 — provider resolved via the same seam (resolve_provider_config("ingest")).
  I7 — bounded: exactly ONE provider call per regeneration; degrade-safe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis

logger = logging.getLogger(__name__)


async def regenerate_overview(
    analysis: Analysis | None = None,
    origin_source: str = "",
) -> None:
    """
    Regenerate vault/wiki/overview.md via a single bounded provider call (I6/I7).

    Sanctioned callers:
      - POST /ops/overview/regenerate (manual/on-demand trigger)
      - ``app.main._queue_drain_sweep`` (once per queue-drain; ADR-0078 refinement v1.7.0)
      - Scheduled-ops if added in future

    The pipeline does NOT call this per-document (ADR-0078 §3 ownership).

    Delegates to ``app.ingest.orchestrator._update_overview`` which holds the full
    implementation (provider resolution, timeout, degrade-safe overwrite, Page indexing,
    and data_version bump on successful write).
    Kept as a thin delegation here so:
      - The ops/overview boundary owns the ``POST /ops/overview/regenerate`` surface.
      - Test monkeypatches targeting ``orch._update_overview`` continue to work.
      - Circular imports are avoided (ops imports from orch, not the other way around).

    Args:
        analysis:      Optional Analysis from a prior ingest run; used to seed the overview
                       with topic/entity/language context. None (default) on the manual/CLI
                       path — the implementation degrades to titles-only.
        origin_source: Label used in log messages only; empty string is safe.
    """
    import app.ingest.orchestrator as _orch

    await _orch._update_overview(analysis, origin_source)
