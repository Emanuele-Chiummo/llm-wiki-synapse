# marker-converter — ServiceNow doc connector (acquisition + split)

Converts a ServiceNow module PDF (docs.servicenow.com export) into small, **structured,
cited SOURCE files** that feed the **normal Synapse ingest**. The connector's job is
acquisition + splitting (and, later, scheduling); the wiki itself is built by the standard
LLM ingest, which assigns valid page types and links — so imports never break the type system.

```
raw/sources/servicenow/
  itam/
    sam/
      software-license-metrics.md    ← SOURCE file (structured + page citation)
      downgrade-rights.md
      …
```

Each file is a **raw source**, not a finished wiki page:
- **No `type:`** — the ingest LLM classifies pages into valid wiki types (entity/concept/…).
  Forcing e.g. `type: reference` breaks the type system (learned the hard way).
- **No hub pages, no forced `[[wikilinks]]`** — linking to ServiceNow/modules is LLM-driven
  via the ingest context catalogue.
- **Kept**: module/feature folders, `tool/module/feature` frontmatter *hints* (harmless —
  Synapse ignores unknown fields, but they give the LLM context), and the page citation in the
  body (`> Fonte: … p.NN`) so provenance survives.

## Pipeline
1. **Bookmarks** (`pypdfium2`) → module (L0) → feature (L1) → group (L2) → section (L3) + pages.
2. **Convert** each section's page range with **Marker** (models loaded once; MPS/CUDA aware).
3. **Clean**: collapse `<br>`/`<ul><li>` in table cells, drop copyright boilerplate, merge
   `(continued)`, strip page-anchor spans, tidy headings.
4. **Emit** a source-only `.md` (frontmatter hints + body + citation footer).

## Setup & run (Apple Silicon)
```bash
python3.13 -m venv .venv
./.venv/bin/pip install -r requirements.txt      # downloads torch + surya models on first run
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py \
    --pdf ~/Downloads/servicenow-australia-it-asset-management-enus.pdf \
    --module-code ITAM --module-title "IT Asset Management" \
    --feature "Software Asset Management" \
    --group "Exploring Software Asset Management" \   # or --sections "A,B"; omit for the whole feature
    --out /path/to/vault/raw/sources                 # or a staging dir to review first
```
Then index with the **normal ingest**: the watcher picks up files under `raw/sources/`, or call
`POST /sources/ingest-all`. The LLM does analyze→generate → typed, linked wiki pages.

Performance: ~6 s/page on MPS (table recognition falls back to CPU). Convert per-feature/group,
not the whole 2500-page book at once (full SAM ≈ 1181 pages).

## Roadmap
- **Acquisition + split (this)** — PDF → structured cited source tree. ✅
- **Ingest** — via the STANDARD Synapse pipeline (LLM). The deterministic verbatim-register
  path was tried and REMOVED (it produced invalid page types and broke the wiki).
- **Schedule** — run the connector periodically (download → convert → drop into raw/sources) so
  the normal ingest auto-picks-up; + auto-discovery/download from docs.servicenow.com.
