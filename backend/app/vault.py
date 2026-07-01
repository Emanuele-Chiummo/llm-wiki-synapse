"""
Vault skeleton bootstrap — idempotent, called once on startup (K1, I5, AC-K7-1/2).

Creates the 3-layer vault structure (K1):
  vault/raw/sources/            — watched dir (immutable at runtime)
  vault/raw/assets/             — binary assets
  vault/wiki/                   — Obsidian-compatible output dir (I5)
    index.md                    — catalogue entry-point (K3)
    log.md                      — append-only ingest history (K4)
    overview.md                 — high-level summary stub
    entities/, concepts/, sources/, queries/, synthesis/, comparisons/
    .obsidian/app.json           — minimal valid Obsidian config (AC-K7-1)
  vault/schema.md               — frontmatter rules (AC-K1-3)
  vault/purpose.md              — vault goal stub (AC-K1-4)

All service-written .md files carry valid YAML frontmatter (AC-K7-2, I5).
This module NEVER writes to vault/raw/ (AC-K1-5).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# ── YAML frontmatter template ─────────────────────────────────────────────────

_FM = "---\ntype: {type}\ntitle: {title}\n---\n"


def _frontmatter(type_: str, title: str) -> str:
    return _FM.format(type=type_, title=title)


# ── Obsidian minimal config (AC-K7-1) ─────────────────────────────────────────

_OBSIDIAN_APP_JSON: dict[str, object] = {
    "legacyEditor": False,
    "livePreview": True,
    "defaultViewMode": "source",
    "vimMode": False,
}


# ── Public entry point ─────────────────────────────────────────────────────────


def bootstrap_vault() -> None:
    """
    Ensure the full vault directory skeleton exists.

    Idempotent — safe to call on every startup.  Existing files are NOT overwritten
    (creates only if absent).
    """
    vault = settings.vault_root

    # ── raw/ (K1) — never written to by the service at runtime ────────────────
    _mkdir(vault / "raw" / "sources")  # watched dir
    _mkdir(vault / "raw" / "assets")

    # ── wiki/ subdirectories ───────────────────────────────────────────────────
    wiki = vault / "wiki"
    for sub in ("entities", "concepts", "sources", "queries", "synthesis", "comparisons"):
        _mkdir(wiki / sub)

    # ── .obsidian/app.json (AC-K7-1, I5) ─────────────────────────────────────
    obsidian_dir = wiki / ".obsidian"
    _mkdir(obsidian_dir)
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        app_json.write_text(json.dumps(_OBSIDIAN_APP_JSON, indent=2) + "\n", encoding="utf-8")
        logger.info("Created %s", app_json)

    # ── wiki seed files with YAML frontmatter (AC-K7-2, I5) ──────────────────
    _write_if_absent(
        wiki / "index.md",
        _frontmatter("index", "Synapse Index")
        + "\n"
        + "<!-- Auto-maintained by Synapse. Human edits are preserved. -->\n\n"
        + "This is the catalogue entry-point for the Synapse knowledge graph (K3).\n",
    )

    _write_if_absent(
        wiki / "log.md",
        _frontmatter("log", "Synapse Ingest Log")
        + "\n"
        + "<!-- Append-only ingest history (K4). Do not edit manually. -->\n\n",
    )

    _write_if_absent(
        wiki / "overview.md",
        _frontmatter("overview", "Synapse Overview")
        + "\n"
        + "<!-- Auto-generated overview stub. Populated by the orchestrator (v0.2+). -->\n\n",
    )

    # ── vault/schema.md (AC-K1-3) ─────────────────────────────────────────────
    _write_if_absent(
        vault / "schema.md",
        "# Synapse Vault Schema\n\n"
        "Required frontmatter fields for every wiki page:\n\n"
        "| Field | Type | Required | Notes |\n"
        "|-------|------|----------|-------|\n"
        "| `type` | string | yes | entity, concept, source, query, synthesis, comparison |\n"
        "| `title` | string | yes | Human-readable page title |\n"
        "| `sources` | list[string] | no | Source file paths or URLs |\n"
        "| `tags` | list[string] | no | 3–6 concise, lowercase, reusable navigation tags (K6) |\n\n"
        "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
        "YAML frontmatter block must be delimited by `---` at lines 1 and N.\n",
    )

    # ── vault/purpose.md (AC-K1-4) ────────────────────────────────────────────
    _write_if_absent(
        vault / "purpose.md",
        "# Vault Purpose\n\n"
        "> Edit this file to define the goal of this Synapse vault.\n\n"
        "## Goal\n\n"
        "<!-- Describe the primary purpose of this vault. -->\n\n"
        "## Key Questions\n\n"
        "<!-- List the questions this vault should help answer. -->\n"
        "- ?\n\n"
        "## Scope\n\n"
        "<!-- Define what is in scope and out of scope. -->\n\n"
        "## Thesis\n\n"
        "<!-- State the working hypothesis or thesis, if any. -->\n",
    )

    logger.info("Vault bootstrap complete at %s", vault)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        logger.info("Created %s", path)
