# marker-converter — ServiceNow doc connector (Increment 1)

Converts a ServiceNow module PDF (docs.servicenow.com export) into a structured,
**cited**, wikilinked Markdown tree ready for the LLM wiki.

```
raw/sources/servicenow/
  ServiceNow.md                       ← vendor hub (type: entity)
  itam/
    IT Asset Management.md            ← module hub (type: concept)
    sam/
      Software Asset Management.md    ← feature hub (type: concept)
      software-license-metrics.md     ← section page (type: reference, cited)
      downgrade-rights.md
      …
```

Each section page carries YAML frontmatter (`type/tool/module/feature/sources`), a
`[[ServiceNow]] › [[ITAM]] › [[SAM]]` breadcrumb, the cleaned body (tables preserved),
and a page-number citation footer. **ServiceNow is the default tool**: every module and
feature links back to `[[ServiceNow]]`, so the import lands as one connected graph.

## Pipeline
1. **Bookmarks** (`pypdfium2`) → clean hierarchy + page numbers: module (L0) → feature (L1)
   → group (L2) → section (L3).
2. **Convert** each section's page range with **Marker** (models loaded once; MPS/CUDA aware).
3. **Clean**: collapse `<br>`/`<ul><li>` in table cells, drop copyright/trademark boilerplate,
   merge `(continued)` headings, strip page-anchor spans, tidy headings.
4. **Format**: frontmatter + breadcrumb + body + citation footer.
5. **Hubs**: vendor → module → feature entity pages.

## Setup & run (Apple Silicon)
```bash
python3.13 -m venv .venv
./.venv/bin/pip install -r requirements.txt      # downloads torch + surya models on first run
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py \
    --pdf ~/Downloads/servicenow-australia-it-asset-management-enus.pdf \
    --module-code ITAM --module-title "IT Asset Management" \
    --feature "Software Asset Management" \
    --sections "Software license metrics,Downgrade Rights"     # omit --sections for the whole feature
```
Output goes to `./out/servicenow/…` (a staging dir — **not** the live vault, so the watcher
doesn't ingest mid-build). Review, then copy into `vault/raw/sources/`.

Performance: ~6 s/page on MPS (table recognition falls back to CPU). Convert per-feature,
not the whole 2500-page book at once.

## Roadmap
- **Inc.1 (this)** — offline core: PDF → structured cited tree. ✅
- **Inc.2** — registration: deterministic reference pages + one LLM synthesis page per feature (Ollama).
- **Inc.3** — inside Synapse: endpoint + UI panel + APScheduler + incremental (hash) + cascade.
- **Inc.4** — auto-discovery/download from docs.servicenow.com.
