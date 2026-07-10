# Program v1.5 — LLM Wiki 1:1 Parity

> **Goal:** Synapse desktop UI = 1:1 mirror of the current nashsu/llm_wiki build.
> **Spec:** `docs/reference/LLMWIKI-LIVE-UI-MAP-2026-07-10.md` (live map). **Decision:** ADR-0066.
> **Branch:** `release/v1.5.0-llmwiki-parity`. **Started:** 2026-07-10.
> Each phase gate: green tests · architect review · docs (D-artifacts / ER if schema) · live preview · owner checkpoint.

| Phase | Title | Depends on | Status |
|-------|-------|-----------|--------|
| **P0** | Foundations — ADR-0066, I9 amendment, this tracker | — | ✅ done |
| **P1** | Vault config & Files — editable purpose/schema + whole-vault tree + Open project folder | P0 | ⏳ in progress |
| **P2** | Multi-vault Project Launcher — ⇄ rail entry, New/Open/Recent, backend vault registry + active-vault switch | P0 | ▫ todo |
| **P3** | Settings parity — Image Captioning, Network proxy, Scheduled Import (external), Source Watch types, MinerU toggle, multi-provider web search, IA decision | P0 | ▫ todo |
| **P4** | Chat composer — Skills · AnyTXT · Fast/Standard/Deep/Local-first pills | P0 | ▫ todo |
| **P5** | Skills view — rail #10 scan/enable/disable/rescan | P0 | ▫ todo |
| **BR** | Brand v1.0 integration — new logo art, dark app-icon, Geist, gradient tokens (parallel stream, own worktree) | — | ⏳ in progress (bg agent) |

> **BR stream** runs in parallel in an isolated git worktree (avoids clobbering feature-phase
> uncommitted work). Spec: `docs/reference/BRAND-v1-INTEGRATION.md`. Merges after review + preview.

## Acceptance per phase

**P1** — `purpose.md`/`schema.md` open as editable pages (render ↔ Edit like any wiki page, save
persists to disk via a write path); a whole-vault file tree (raw/ + wiki/ + purpose/schema) with an
"Open project folder" action; `MetaFileView` read-only drawer removed or upgraded. Tests: save
round-trips; tree lists the full vault.

**P2** — Launcher reachable from a ⇄ bottom-rail icon: New Project (scaffold raw/wiki/purpose/schema),
Open Project (folder picker), Recent Projects (name+path, switch). Backend: multi-vault registry,
active-vault selection, `vault_id` honored end-to-end. Tests: create/open/switch; queries scoped per vault.

**P3** — Settings mirror LLM Wiki subsections (see map §11). Cloud providers (Tavily/SerpApi/
Firecrawl/Brave, MinerU) opt-in + off by default + upload warning (ADR-0066). Source Watch adds
.doc/.odt/.rtf/.odp/.ods/.csv/.html/.mdx + grouped checkboxes + excluded folders + max size.

**P4** — Chat composer gains Skills toggle, AnyTXT toggle, and the 4 retrieval-mode pills, wired to
the chat/retrieval backend.

**P5** — Skills view scans skill folders, lists cards (name/provider/desc), enable/disable/rescan,
gates which skills Chat may use.
