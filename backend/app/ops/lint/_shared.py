"""
Shared types + cross-cutting constants for the ``app.ops.lint`` package (BE-REFAC-2).

Pure refactor split of the former monolithic ``app/ops/lint.py`` (~2650 lines) into:
  detectors.py    — deterministic structural checks (NO provider call — I1)
  fixes.py        — deterministic fix appliers (human-gated apply seam — ADR-0037 §5)
  semantic.py     — the LLM-backed opt-in semantic lint pass (I6/I7)
  persistence.py  — lint_findings / lint_runs reads + writes
  __init__.py     — public API (unchanged surface) + orchestration entry points

This module holds the dataclasses and value-sets shared across those submodules so none of
them has to import from ``__init__`` (which would be circular). Never import ``app.ops.lint``
(the package) from here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.models import LintFinding, LintRun

# $1 cost-anomaly threshold — same as the ingest path (ADR-0009 §3 / ADR-0037 §4).
COST_ANOMALY_THRESHOLD_USD: float = 1.00

# Accepted value sets (app-side enum-by-convention, no DB CHECK — ADR-0037 §3.1).
VALID_CATEGORIES = frozenset(
    {
        "orphan-page",
        "broken-wikilink",
        "missing-xref",
        "contradiction",
        "stale-claim",
        "missing-page",
        # L1 — no-outlinks: a page with zero outgoing wikilinks (ADR-0058 §L1).
        "no-outlinks",
        # L2 — suggestion: a question or source worth adding to the wiki (ADR-0058 §L2).
        "suggestion",
    }
)
VALID_SEVERITIES = frozenset({"info", "warning", "error"})
# `superseded` (v1.5.x): a terminal status set by a NEW scan on the prior run's still-OPEN
# findings that it recomputed — llm_wiki recomputes lint fresh each run (clearLintItems), so a
# fixed issue simply vanishes. Synapse persists findings, so we emulate the fresh recompute by
# closing stale open findings instead of accumulating them. Distinct from human `dismissed`.
VALID_STATUSES = frozenset({"open", "applied", "dismissed", "superseded"})

# Which categories each scan phase RECOMPUTES — drives the category-aware supersede so a
# deterministic-only scan (semantic=False) never closes semantic findings it did not re-check.
DETERMINISTIC_CATEGORIES = frozenset(
    {"orphan-page", "broken-wikilink", "no-outlinks", "missing-xref"}
)
SEMANTIC_CATEGORIES = frozenset({"contradiction", "stale-claim", "missing-page", "suggestion"})

# Categories whose apply step is FLAG-ONLY (no deterministic safe fix — ADR-0037 §5).
# stale-claim → apply is a no-op status change to 'applied' with a resolution_note (the human
# still has to fix it by editing the wiki).
# broken-wikilink WITHOUT a suggestion CREATES A STUB page for the missing target
# (_create_broken_link_stub, L4/ADR-0058 §L4) — no longer flag-only.
# suggestion — always flag-only (semantic category; no deterministic fix).
# no-outlinks and orphan-page are handled specially in apply_lint_fix:
#   - if suggested_target/suggested_page_id present → apply a real fix
#   - otherwise → fall back to flag-only
# ADR-0067 D4/P0-4: `contradiction` is NO LONGER flag-only — an applied contradiction AUTHORS a
# genuine open-question `type=query` page (the only sanctioned query generator besides chat-save).
FLAG_ONLY_CATEGORIES = frozenset({"stale-claim", "suggestion"})

# Bounded reads (I7 — never an unbounded scan).
ORPHAN_SCAN_MAX_PAGES: int = 1_000
BROKEN_SCAN_MAX_LINKS: int = 1_000  # L1 / I7 — cap for broken-wikilink scan
NO_OUTLINKS_SCAN_MAX_PAGES: int = 1_000  # L1 / I7 — cap for no-outlinks scan
CANDIDATE_TITLES_MAX: int = 500

# Mapping: lint category → review item_type (L6 / ADR-0037 B1).
# broken-wikilink → missing-page (the dangling target may not exist; review queue surfaces it).
CATEGORY_TO_ITEM_TYPE: dict[str, str] = {
    "broken-wikilink": "missing-page",
    "missing-page": "missing-page",
    "contradiction": "contradiction",
    "stale-claim": "suggestion",
    "orphan-page": "suggestion",
    "missing-xref": "suggestion",
    "no-outlinks": "suggestion",  # L1 / ADR-0058 §L1
    "suggestion": "suggestion",  # L2 / ADR-0058 §L2
}

# Maximum ids per batch (I7 — bounded operation).
BATCH_MAX_IDS: int = 200


# ── Public result types ────────────────────────────────────────────────────────


@dataclass
class LintScanResult:
    """Return value of run_lint_scan (ADR-0037 §3.3)."""

    run_id: uuid.UUID
    status: Literal["completed", "error"]
    iterations_used: int
    findings_count: int
    total_cost_usd: float
    error_message: str | None


@dataclass
class LintFindingsPage:
    """Paginated result for GET /lint/findings."""

    items: list[LintFinding]
    total: int
    limit: int
    offset: int
    severity_totals: dict[str, int]


@dataclass
class LintRunsPage:
    """Paginated result for GET /lint/runs."""

    items: list[LintRun]
    total: int
    limit: int
    offset: int


# ── Finding DTO (semantic provider call contract — ADR-0037 §4.3) ──────────────


@dataclass
class FindingDTO:
    """
    One structured finding emitted by the deterministic checks or the semantic provider call.

    target_title resolves to target_page_id at persist time (for missing-xref / stale-claim).
    suggested_target / suggested_page_id: L2 — the best tolerant-resolver match for
    broken-wikilink findings (NULL for all other categories).
    """

    category: Literal[
        "orphan-page",
        "broken-wikilink",
        "missing-xref",
        "contradiction",
        "stale-claim",
        "missing-page",
        "no-outlinks",
        "suggestion",
    ]
    severity: str
    description: str
    target_title: str | None = None
    target_page_id: uuid.UUID | None = None
    proposed_action: str | None = None
    suggested_target: str | None = None  # L2
    suggested_page_id: uuid.UUID | None = None  # L2


# ── L5 — batch result types ──────────────────────────────────────────────────────


@dataclass
class BatchFindingResult:
    """Per-item result within a batch operation response (L5)."""

    id: str
    status: str  # "ok" | "error"
    detail: str | None


@dataclass
class BatchFindingsResponse:
    """Response for POST /lint/findings/batch (L5)."""

    results: list[BatchFindingResult]
    ok_count: int
    error_count: int
