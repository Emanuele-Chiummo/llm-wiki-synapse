# Program v1.5 — LLM Wiki 1:1 Parity

> **Goal:** Synapse desktop UI = 1:1 mirror of the current nashsu/llm_wiki build.
> **Spec:** `docs/reference/LLMWIKI-LIVE-UI-MAP-2026-07-10.md` (live map). **Decision:** ADR-0066.
> **Branch:** `release/v1.5.0-llmwiki-parity`. **Started:** 2026-07-10.
> Each phase gate: green tests · architect review · docs (D-artifacts / ER if schema) · live preview · owner checkpoint.

| Phase | Title | Depends on | Status |
|-------|-------|-----------|--------|
| **P0** | Foundations — ADR-0066, I9 amendment, this tracker | — | ✅ done |
| **P1** | Vault config & Files — editable purpose/schema + whole-vault tree + Open project folder | P0 | ✅ **complete** — editable purpose/schema (Edit→CodeMirror→Save) · whole-vault "Vault" tab (`root=vault`) · "Open folder" (copy server-side path). All verified live. |
| **P2** | Multi-vault Project Launcher — ⇄ rail entry, New/Open/Recent, backend vault registry + active-vault switch | P0 | ✅ **complete** — ADR-0067; registry + create/open + `activate` runtime switch (14 tests); ⇄ launcher UI **verified live** (renders + `GET /projects` shows the active vault). Full end-to-end vault-switch to be smoke-tested against a non-shared backend. |
| **P3** | Settings parity — Image Captioning, Network proxy, Scheduled Import (external), Source Watch types, MinerU toggle, multi-provider web search, IA decision | P0 | ⏳ **planned** — 6 sized slices (P3-a…P3-f) grounded in the current code (see "P3 slice plan" below). Synapse settings already cover most of LLM Wiki, grouped differently; real gaps = Image Captioning page (S), Network proxy (M), wider Source-Watch types (M/L), **MinerU** + **multi-provider web search** (L, invariant-amended). |
| **P4** | Chat composer — Skills · AnyTXT · Fast/Standard/Deep/Local-first pills | P0 | ▫ todo |
| **P5** | Skills view — rail #10 scan/enable/disable/rescan | P0 | ▫ todo |
| **BR** | Brand v1.0 integration — new logo art, dark app-icon, Geist, gradient tokens | — | 🟡 integrated (2 TODOs: drop Geist woff2 into `frontend/src/assets/fonts/`; Tauri tray `set_icon_as_template` API when it lands) |

> **BR stream** was authored in an isolated worktree, then applied to the branch as a **cherry
> of only the branding commit's own files** (`git checkout <brandcommit> -- <files>`) — NOT a
> `git merge`, because the worktree branched from a STALE base (018f38c) and a merge would have
> reverted 1.4.1 + P1 on shared files. Verified zero overlap first. Spec:
> `docs/reference/BRAND-v1-INTEGRATION.md`. The definitive logo = the S-node mark in the owner's
> `__Synapse_Logo_Redesign.pdf`; the `Brand/png/` assets already match it 1:1.

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

## P3 — Settings parity: slice plan (grounded in the current code, 2026-07-10)

Synapse Settings are already RICHER and grouped, not LLM Wiki's flat-15 — most subsections already
exist, organised differently. The real gaps + sizing:

| # | Slice | Synapse today | Size | Notes |
|---|-------|---------------|------|-------|
| P3-a | **Image Captioning page** ✅ **DONE** | now runtime-overridable (S19/S20) + a settings page; verified live | **S** | Snapshot tests (FE+BE) updated 18→20. Commit `807965d`. |
| P3-b | **Network proxy page** | none | **M** | New config (enable/url/bypass-local) + wire an `httpx` proxy transport into outbound clients (LLM/embeddings/search/update). Applying the proxy everywhere is the work. |
| P3-c | **Source Watch wider types** | `_EXTRACTABLE_EXTENSIONS` = pdf/docx/pptx/xlsx only | **M/L** | LLM Wiki adds .doc/.odt/.rtf/.odp/.ods/.csv/.html/.mdx — needs real **extractors** for the new formats (not just a config flag) + grouped-checkbox UI + excluded-folders + max-size in the Source Watch page. |
| P3-d | **MinerU cloud PDF toggle** | `pdf` page has Marker (`pdf_extractor` pypdf/marker) | **L** | ADR-0066: add MinerU as a 3rd `pdf_extractor` value + a MinerU cloud client + opt-in/off-default + upload warning. Backend integration. |
| P3-e | **Multi-provider web search** | `SectionWebSearch` = **SearXNG-only** (ADR-0041) | **L** | ADR-0066: add Tavily/SerpApi/Firecrawl/Brave/Ollama-Web as opt-in providers alongside SearXNG — needs backend search adapters behind a provider seam + the multi-row UI (like the LLM Models catalog). The headline "mirror literally" item. |
| P3-f | **Settings IA** | grouped nav | **S (decision)** | Decide: keep Synapse's richer grouped IA, or flatten toward LLM Wiki's 15. Recommendation: **keep grouped** (Synapse has more surface); just ensure every LLM Wiki subsection has a home. |

**Recommended sequencing:** P3-a (small, visible parity win — but bundle the snapshot-test update) →
P3-b → then the two big invariant-amended integrations **P3-d (MinerU)** and **P3-e (multi-provider
web search)** each as its **own focused turn** (backend adapters + tests + live verify). P3-c (new
extractors) sized separately. P3-f is a one-line decision.
