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

## Auto mode (large books, `--auto`)

For a big multi-module export where you don't want to hand-write `--module-title` /
`--feature` / domain-map entries, add `--auto`. It **derives** the module and feature codes
from the PDF's own bookmark outline (curated map → acronym → slug — e.g. *IT Operations
Management* → `ITOM`, *Now Assist for ITOM* → `NAFI`) and splits **every** module in the book.
`--auto` defaults `--file-depth` to **2** → **one file per L2 chapter/group** (override with an
explicit `--file-depth`). Zero pre-configuration; new books just work.

```bash
# One file per chapter (L2) for the entire book, auto-derived codes:
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py --auto \
    --pdf ~/Downloads/servicenow-australia-it-operations-management-enus.pdf \
    --out ./out-itom            # a staging dir to review before copying into the vault

# Proof one chapter first (fast) with --sections, then run the whole book:
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py --auto \
    --pdf ~/Downloads/…itom….pdf --sections "Exploring ITOM/OT SU Licensing" --out ./out-itom
```

> A 5000-page book at ~6 s/page is ~9 h of Marker compute — splitting doesn't reduce the total,
> but it makes the run **resumable per chapter** and produces small, citable source files. The
> hash-gate in `--watch-dir` mode means a re-drop never re-converts an already-done PDF.

**Drop-and-forget (the "upload a big file" flow):** run the daemon with `--auto` and just drop
PDFs into the watch dir — each new file is auto-split per chapter and converted, no per-book
flags:

```bash
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py --auto \
    --watch-dir ~/Downloads/sn-pdfs --out /path/to/vault/raw/sources --interval-minutes 60
```

## Scheduler daemon mode (R7-7)

The `--watch-dir` flag turns the connector into a bounded scheduler daemon: each tick it
converts **new** PDFs found in `<watch-dir>` into `<out>/servicenow/…` and then sleeps
`--interval-minutes` minutes before the next tick.

```
PDF in watch-dir
     │
     ▼ tick (bounded: max 20 files/tick, I7)
  hash gate ─── already seen? ──► skip
     │
     ▼ new
  Marker convert (section by section)
     │
     ▼
  out/servicenow/<module>/<feature>/<slug>.md
     │
     ▼ Synapse watcher picks up (raw/sources → wiki pages)
  POST /ingest/file  OR  watchdog
```

### Start the daemon

```bash
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py \
    --watch-dir ~/Downloads/sn-pdfs \
    --out /path/to/vault/raw/sources \
    --interval-minutes 60 \
    --module-code ITAM --module-title "IT Asset Management"
```

- Drop new PDFs into `~/Downloads/sn-pdfs/` at any time; they are picked up on the next tick.
- State is persisted in `out/.sn_connector_state.json` (SHA-256 → output path). Survives
  restarts; a PDF is never re-converted unless the state file is deleted.
- At most **20 PDFs per tick** (I7 cap). Increase with `--max-files` if needed.
- `--auto-download` is a no-op stub gated behind `SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1`.
  It logs a clear "not implemented" warning. Provide PDFs manually in the watch dir.

### launchd plist (macOS / TrueNAS Jails)

Save as `~/Library/LaunchAgents/com.synapse.sn-connector.plist` and load with `launchctl`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.synapse.sn-connector</string>

    <key>ProgramArguments</key>
    <array>
        <!-- Absolute path to the Marker venv python -->
        <string>/absolute/path/to/tools/marker-converter/.venv/bin/python</string>
        <string>/absolute/path/to/tools/marker-converter/servicenow_connector.py</string>
        <string>--watch-dir</string>
        <string>/Users/you/Downloads/sn-pdfs</string>
        <string>--out</string>
        <string>/path/to/vault/raw/sources</string>
        <string>--interval-minutes</string>
        <string>60</string>
        <string>--module-code</string>
        <string>ITAM</string>
        <string>--module-title</string>
        <string>IT Asset Management</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TORCH_DEVICE</key>
        <string>mps</string>
    </dict>

    <!-- Run at load and keep alive (launchd restarts it after each sleep-loop tick exits) -->
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/sn-connector.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/sn-connector.err</string>
</dict>
</plist>
```

```bash
# Load
launchctl load ~/Library/LaunchAgents/com.synapse.sn-connector.plist

# Check status
launchctl list com.synapse.sn-connector

# Unload
launchctl unload ~/Library/LaunchAgents/com.synapse.sn-connector.plist
```

> Note: The daemon itself is an infinite sleep loop; `KeepAlive` ensures launchd restarts it
> if the process exits unexpectedly. On TrueNAS SCALE, use a cron job or a Docker Compose
> service with `restart: unless-stopped` instead of launchd.

## Roadmap
- **Acquisition + split (this)** — PDF → structured cited source tree. ✅
- **Scheduler daemon** — `--watch-dir` bounded daemon mode. ✅ (R7-7)
- **Ingest** — via the STANDARD Synapse pipeline (LLM). The deterministic verbatim-register
  path was tried and REMOVED (it produced invalid page types and broke the wiki).
- **Auto-download** — future increment (stub behind `SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1`).
