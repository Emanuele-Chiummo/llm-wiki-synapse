"""
ops/overview.py — manual overview.md regeneration (ADR-0078, WS-B aggregate ownership).

Overview.md is NO LONGER regenerated automatically by the ingest pipeline (ADR-0078).
Instead, this module exposes a single public function:

    regenerate_overview(analysis=None, origin_source="")

which is called from POST /ops/overview/regenerate. The implementation delegates to
``app.ingest.orchestrator._update_overview`` (kept there for test coupling and backward
compatibility of monkeypatches).

Invariants:
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

    This is the ONLY sanctioned way to trigger overview regeneration as of ADR-0078.
    Ingest no longer calls this automatically; callers are:
      - POST /ops/overview/regenerate (manual trigger)
      - Future scheduled-ops (if added)

    Delegates to ``app.ingest.orchestrator._update_overview`` which holds the full
    implementation (provider resolution, timeout, degrade-safe overwrite, Page indexing).
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
