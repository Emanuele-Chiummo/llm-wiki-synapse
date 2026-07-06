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


# ── vault/schema.md — the rules the ingest AI + curators follow (K1 layer 3) ──
# nashsu/llm_wiki-aligned contract, adapted to Synapse reality (lang, F3 required sources,
# index/log/overview meta exception). Seeds NEW vaults only (never overwrites, I5).

_SCHEMA_MD = """# Wiki Schema

> The rules the ingest AI and human curators follow when writing pages in `wiki/`.
> Synapse keeps `wiki/` a valid Obsidian vault (I5): YAML frontmatter + `[[wikilinks]]`.

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, documents ingested |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per vault) |
| index | wiki/ | Auto-maintained catalogue of all pages |
| log | wiki/ | Append-only ingest history |

## Naming Conventions

- Files: `kebab-case.md`, slug derived from the page title (unicode-tolerant).
- Entities: match the official name (e.g. `openai.md`, `gpt-4.md`).
- Concepts: descriptive noun phrases (e.g. `chain-of-thought.md`).
- Sources: include author + year in the title so the slug reads `author-year-topic`
  (e.g. `wei-2022-chain-of-thought.md`).
- Queries: phrase the title as the question (e.g. `does-scale-improve-reasoning.md`).

## Frontmatter

Every wiki page carries YAML frontmatter delimited by `---`:

```yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
lang: en            # ISO-639-1, matches the page language (F3)
sources: []         # source file paths / URLs this page derives from (F3 traceability)
tags: []            # 3-6 concise, lowercase, reusable navigation tags (K6)
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

- `type`, `title`, `lang`, `sources` are required on generated pages (I5/K6/F3).
  `index.md`, `log.md`, `overview.md` only require `type` + `title`.
- `sources` MUST be non-empty on content pages — it is the F3 traceability guarantee
  (a page with no source is invalid).
- `created` is set once and preserved across re-generation; `updated` advances each write.

Source pages MAY additionally carry:

```yaml
authors: []
year: YYYY
url: ""
venue: ""
```

## Index Format

`wiki/index.md` lists all pages grouped by type. Each entry:

```
- [[page-slug]] — one-line description
```

## Log Format

`wiki/log.md` is append-only, newest activity at the bottom, grouped by day:

```
## YYYY-MM-DD

- HH:MM:SSZ · indexed · concept · [[Page Title]] — wiki/concepts/page-title.md
```

## Cross-referencing Rules

- Link between pages with `[[page-title]]` (Obsidian-compatible).
- Every entity and concept appears in `wiki/index.md`.
- Queries link the sources and concepts they draw on.
- Synthesis pages cite contributing sources via `sources:` / `related:`.

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction on the relevant concept/entity page.
2. Create or update a query page to track the open question.
3. Link both sources from the query page.
4. Resolve in a synthesis page once evidence is sufficient.
"""


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
    _write_if_absent(vault / "schema.md", _SCHEMA_MD)

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
