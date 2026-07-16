"""
Vault skeleton bootstrap — idempotent, called once on startup (K1, I5, AC-K7-1/2).

Creates the 3-layer vault structure (K1):
  vault/raw/sources/            — watched dir (immutable at runtime)
  vault/raw/assets/             — binary assets
  vault/wiki/                   — Obsidian-compatible output dir (I5)
    index.md                    — catalogue entry-point (K3, llm_wiki parity)
    log.md                      — append-only ingest history (K4, llm_wiki parity)
    overview.md                 — high-level summary stub
    entities/, concepts/, sources/, queries/, synthesis/, comparisons/
    .obsidian/app.json           — minimal valid Obsidian config (AC-K7-1)
  vault/schema.md               — frontmatter rules (AC-K1-3)
  vault/purpose.md              — vault goal stub (AC-K1-4)

Scenario support (v1.7.0 WS-E onboarding parity):
  When bootstrap_vault_at is called with scenario_id, the schema.md and purpose.md
  are OVERWRITTEN with scenario-specific content, extra wiki/ subdirectories are
  created, and the index.md is built with per-type sections (including custom types).
  output_language is accepted for signature symmetry but is NOT written to disk —
  the caller (projects.py) persists it to vault_state.

All service-written .md files carry valid YAML frontmatter (AC-K7-2, I5).
This module NEVER writes to vault/raw/ (AC-K1-5).
"""

from __future__ import annotations

import json
import logging
from datetime import date
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

`wiki/log.md` is an append-only activity log **maintained automatically by Synapse — never
generate, overwrite or edit it** (it is not a page you author). Each ingest appends one entry:

```
## [YYYY-MM-DD] ingest | <source title>
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

_DEFAULT_PURPOSE_MD = (
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
    "<!-- State the working hypothesis or thesis, if any. -->\n"
)


# ── index.md / log.md content builders ────────────────────────────────────────


def _build_index_md(extra_dirs: list[str] | None = None) -> str:
    """
    Build wiki/index.md content matching llm_wiki parity (WS-E, v1.7.0).

    Structure:
      YAML frontmatter (I5) + # Wiki Index heading +
      ## Recently Updated (code-owned bounded section for the ingest PR to append to) +
      ## Entities / Concepts / Sources / Queries / Comparisons / Synthesis (base) +
      custom sections derived from *extra_dirs* (e.g. "wiki/thesis" → "## Thesis")

    *extra_dirs* entries are "wiki/<name>" strings; the heading is title-cased with
    hyphens converted to spaces (e.g. "wiki/plot-threads" → "## Plot Threads").
    """
    base_sections = ["Entities", "Concepts", "Sources", "Queries", "Comparisons", "Synthesis"]
    extra_sections: list[str] = []
    for dir_path in extra_dirs or []:
        name = dir_path.rsplit("/", 1)[-1].replace("-", " ").title()
        extra_sections.append(name)

    lines: list[str] = [
        "---\ntype: index\ntitle: Wiki Index\n---\n",
        "# Wiki Index\n",
        "\n## Recently Updated\n",
    ]
    for section in base_sections + extra_sections:
        lines.append(f"\n## {section}\n")
    return "".join(lines)


def _build_log_md() -> str:
    """
    Build wiki/log.md with the initial 'Project created' entry (llm_wiki parity, WS-E).

    Format: Synapse YAML frontmatter (I5) + llm_wiki-style dated section.
    Uses the current local date (YYYY-MM-DD); no hardcoded date (I7 — no nondeterminism).
    """
    today = date.today().isoformat()
    return (
        "---\ntype: log\ntitle: Research Log\n---\n"
        "# Research Log\n\n"
        f"## {today}\n\n"
        "- Project created\n"
    )


# ── Public entry points ────────────────────────────────────────────────────────


def bootstrap_vault() -> None:
    """
    Ensure the boot vault's directory skeleton exists (``settings.vault_root``).

    Idempotent — safe to call on every startup.  Existing files are NOT overwritten.
    """
    bootstrap_vault_at(settings.vault_root)


def bootstrap_vault_at(
    vault: Path,
    *,
    scenario_id: str | None = None,
    output_language: str | None = None,
) -> None:
    """
    Ensure the full vault directory skeleton exists at *vault* (v1.5 P2 — multi-vault).

    Idempotent — creates only what is absent; existing files are NOT overwritten unless a
    *scenario_id* is given (see below). Used both for the boot vault (via
    :func:`bootstrap_vault`) and when creating a new project vault at an arbitrary path
    (``POST /projects``, ADR-0067).

    Parameters
    ----------
    vault:
        Absolute path to the vault root. Created if absent.
    scenario_id:
        Optional llm_wiki template id (WS-E, v1.7.0). When supplied the base scaffold is
        augmented: schema.md and purpose.md are **overwritten** with scenario-specific
        content; extra wiki/ subdirectories are created; index.md is built with per-type
        sections including custom-type headings.  Unknown ids are logged and fall back to
        the default schema/purpose (never raise). Validated before this call by the
        API layer (400 on unknown id there).
    output_language:
        ISO-639-1 code accepted for signature symmetry (WS-E). NOT written to disk here —
        the caller (``app.projects.create_project``) persists it to vault_state.
    """
    if output_language is not None:
        # Accepted for call-site symmetry; actual persistence is in projects.create_project.
        logger.debug(
            "bootstrap_vault_at: output_language=%r (not written to disk)", output_language
        )

    # ── raw/ (K1) — never written to by the service at runtime ────────────────
    _mkdir(vault / "raw" / "sources")  # watched dir
    _mkdir(vault / "raw" / "assets")

    # ── wiki/ base subdirectories ──────────────────────────────────────────────
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

    # ── Resolve scenario data (if scenario_id given) ──────────────────────────
    scenario_extra_dirs: list[str] = []
    if scenario_id is not None:
        from app.scenarios_data import SCENARIO_INDEX  # noqa: PLC0415

        sd = SCENARIO_INDEX.get(scenario_id)
        if sd is not None:
            scenario_extra_dirs = sd["extra_dirs"]
        else:
            logger.warning(
                "bootstrap_vault_at: unknown scenario_id %r — defaulting schema/purpose",
                scenario_id,
            )
            scenario_id = None  # fall back to defaults below

    # ── wiki/index.md (llm_wiki-parity structure, WS-E) ──────────────────────
    # If a scenario is applied, write unconditionally (includes custom sections).
    # Otherwise, write only if absent (_write_if_absent — idempotent for existing vaults).
    index_content = _build_index_md(scenario_extra_dirs if scenario_id else None)
    if scenario_id is not None:
        (wiki / "index.md").write_text(index_content, encoding="utf-8")
        logger.info("Wrote scenario index.md for %r at %s", scenario_id, wiki)
    else:
        _write_if_absent(wiki / "index.md", index_content)

    # ── wiki/log.md (llm_wiki-parity 'Project created' entry, WS-E) ──────────
    _write_if_absent(wiki / "log.md", _build_log_md())

    # ── wiki/overview.md ──────────────────────────────────────────────────────
    _write_if_absent(
        wiki / "overview.md",
        _frontmatter("overview", "Synapse Overview")
        + "\n"
        + "<!-- Auto-generated overview stub. Populated by the orchestrator (v0.2+). -->\n\n",
    )

    # ── vault/schema.md + vault/purpose.md (AC-K1-3/4) ────────────────────────
    # Scenario given → OVERWRITE with scenario content + create extra dirs.
    # No scenario   → _write_if_absent (default content; existing vaults unchanged).
    if scenario_id is not None:
        from app.scenarios_data import SCENARIO_INDEX  # noqa: PLC0415

        sd = SCENARIO_INDEX[scenario_id]  # safe: scenario_id is valid at this point
        (vault / "schema.md").write_text(sd["schema_md"], encoding="utf-8")
        (vault / "purpose.md").write_text(sd["purpose_md"], encoding="utf-8")
        logger.info("Wrote scenario schema.md + purpose.md for %r at %s", scenario_id, vault)

        # Create scenario-specific extra wiki/ subdirectories (idempotent)
        for extra in sd["extra_dirs"]:
            _mkdir(vault / extra)

        logger.info(
            "bootstrap_vault_at: applied scenario %r — extra dirs: %s",
            scenario_id,
            sd["extra_dirs"],
        )
    else:
        _write_if_absent(vault / "schema.md", _SCHEMA_MD)
        _write_if_absent(vault / "purpose.md", _DEFAULT_PURPOSE_MD)

    logger.info("Vault bootstrap complete at %s (scenario=%r)", vault, scenario_id)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        logger.info("Created %s", path)
