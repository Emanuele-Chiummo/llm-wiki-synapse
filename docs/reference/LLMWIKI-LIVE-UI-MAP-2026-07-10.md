# LLM Wiki — Live UI + Function Map (2026-07-10) & Synapse 1:1 Delta

> **Method:** live control of the desktop LLM Wiki app (bundle `com.llmwiki.app`, vault
> `~/Documents/00_Personal/01_Wiki/LLM Wiki`), every view + every Settings subsection opened and
> screenshotted. **This supersedes the stale audits** in `llm_wiki-audit/` and
> `SYNAPSE-VS-LLMWIKI-PARITY.md`, which were anchored to llm_wiki **v0.5.4**. The live app is a
> much newer build (Skills, MinerU, multi-provider web search, image captioning, 15-vendor LLM
> catalog, `801/804 pages` graph header).
> **Synapse side** is code-anchored (frontend `NavRail.tsx`, `SettingsPanel.tsx`, `MetaFileView`,
> single-vault posture) — the desktop React UI, NOT the native mobile wrapper.
>
> Legend: ✅ match · 🟡 partial · ❌ missing in Synapse · ⭐ Synapse-diverges-by-design (invariant).

---

## 1. Left icon rail (primary nav)

| # | LLM Wiki | Synapse (`NavRail.tsx`) | Status |
|---|----------|-------------------------|--------|
| 1 | Home (grid) → Review + Deep Research dashboard | `home` (House) | ✅ |
| 2 | Chat | `chat` (MessageSquare) | ✅ |
| 3 | Wiki (page render/edit) | `pages` (FileText) | ✅ |
| 4 | Files (whole-vault tree) | `sources` (FolderOpen) — raw-source browser only | 🟡 scope differs (see §5) |
| 5 | Search | `search` | ✅ |
| 6 | Graph | `graph` (Share2) | ✅ |
| 7 | Wiki Lint | `lint` (ClipboardCheck) | ✅ |
| 8 | Review (99+ badge) | `review` (ListChecks) | ✅ |
| 9 | Deep Research (globe) | `deep-search` (Globe) | ✅ |
| 10 | **Skills** (sparkle) | — | ❌ **no Skills view** |
| — | Settings (gear, bottom) | `settings` | ✅ |
| — | **Project Switcher (two-arrows ⇄, very bottom)** | — | ❌ **the multi-vault entry** |

**Top tabs (Wiki/Files):** LLM Wiki splits the left panel into `Knowledge` (page tree) and `Files`
(filesystem) tabs. Synapse uses separate rail items (`pages` vs `sources`) instead of tabs.

---

## 2. Home / Dashboard
LLM Wiki: center = **Review** list (count badge, `Refresh`, `Clear resolved`, bulk: Select
pending / Mark selected resolved / Dismiss selected); each item = checkbox + type icon + title +
description + optional `Pages:` refs + **Deep Research / Create Page / Skip**. Right = **Deep
Research** panel (`Enter a research topic…` + send, task list). → Synapse Home + ReviewQueueView +
DeepSearchView. ✅ (verify bulk-action parity).

## 3. Chat
Composer toolbar: **Attach image · Web search (toggle) · AnyTXT search (toggle) · Skills** + mode
pills **Fast / Standard / Deep / Local first** + Send. Left = conversation list (`+ New Chat`).
→ Synapse chat exists (B2 composer). 🟡 check: `Skills` button, `AnyTXT`, and the 4 mode pills.

## 4. Wiki page (render + edit)
- **Render:** book icon + title + **Edit**; frontmatter as chips (TYPE badge, date, tag pills);
  `More` expander + `updated:`; body = GFM with blue `[[wikilinks]]`. Tabbed (closeable `x`).
- **Edit:** raw markdown **source** editor (monospace/CodeMirror); top-right toggles to
  **Done** (eye/preview). → Synapse CodeMirror 6 (I4). ✅ (verify tag-chip + `More` parity).

## 5. Files (vault tree) — ⚠ key gap
LLM Wiki `Files` tab shows the **entire vault**: `raw/{assets,sources}`, `wiki/{comparisons,
concepts,entities,media,queries,sources,synthesis, index.md, log.md, overview.md}`, **`purpose.md`**,
**`schema.md`**, + **`Open project folder`**. Clicking `purpose.md`/`schema.md` opens them as
**editable** pages (render + Edit, same as any wiki page).
- Synapse `sources` (SourcesView) = raw-source browser only; `purpose.md`/`schema.md` are shown
  **read-only** in `MetaFileView` drawer (WS-D8). 🟡→❌ **purpose/schema not editable in-app**;
  no single whole-vault file tree with `Open project folder`.

## 6. Vault configuration = `purpose.md` + `schema.md`
There is **no dedicated vault-config settings page** in LLM Wiki. The vault is configured by
editing two markdown files: `purpose.md` (**Project Purpose**: Goal / Key Questions / Scope
in-out / Thesis) and `schema.md` (rules). Both are normal editable wiki pages. Synapse structure
is identical (F2) but **read-only in-app** → alignment = make them editable like any page.

## 7. Graph
Header pills: **`801/804 pages` · `2438/2823 links` · `3 hidden`**; toolbar: Search · Filter ·
Reset · **Type** · **Community** · **Insights (13)** · Refresh; zoom in/out/fit; **Node Types**
legend (Entity 202, Concept 458, Source 132, Synthesis 4, Overview 1, Comparison 5, Index 1,
Log 1 = 804). → Synapse GraphViewer mirrors pills/toolbar/legend. ✅.
**Validated the 1.4.1 hidden-count fix:** LLM Wiki `total (804) ≈ shown (801)`, `hidden = 3`
because it counts only graph-eligible pages. Synapse's phantom "233 hidden" was `total_nodes`
counting raw/query rows; the fix aligns the denominator → hidden ≈ 0. ✅ confirmed live.

## 8. Wiki Lint
`Run Lint`, **Semantic (LLM)** toggle, bulk (Select all / Fix selected / Send selected to Review /
Ignore selected), sections (`Warnings (744)`…), per-issue: type (Broken Link) + message +
`Suggested target:` + **Open / Fix**. → Synapse lint (B1). ✅ (verify Semantic toggle + suggested-
target + send-to-review).

## 9. Review / Deep Research / Search
- **Review** (icon 8): same list as Home center.
- **Deep Research** (globe): topic input + task list (right rail everywhere).
- **Search**: single center box `Search wiki pages… (Enter to search)` (semantic + token).
→ Synapse has all three. ✅ (spot-check controls).

## 10. Skills — ❌ missing in Synapse
Full-screen view: scans `.llm-wiki/skills, ~/.claude/skills, ~/.codex/skills, ~/.agents/skills`;
search box; `1 enabled / 1 discovered`; **Enable all / Disable all / Rescan**; per-skill card
(name + provider tag + description + **Enabled** toggle). Lets Chat use agent skills. **No Synapse
equivalent.**

---

## 11. Settings — subsection-by-subsection

| LLM Wiki subsection | Contents (live) | Synapse (`SettingsPanel`) | Status |
|---|---|---|---|
| **General** | Launch at startup; On window close: Ask / **Hide** / Quit | desktop-shell only — Synapse is web/Tauri; N/A on web | 🟡 Tauri-only |
| **LLM Models** | 1 row/vendor, mutually-exclusive toggles, `configured`/`active` badges, expand per vendor; 15 vendors (Anthropic, Claude Code CLI, Codex CLI, OpenAI, Gemini, Azure, DeepSeek, Atlas, Groq, xAI, NVIDIA NIM, Kimi×3) | `providers` / `SectionLlmModels` (v1.4 vendor catalog) | ✅ |
| **Embeddings** | Enable vector search; Endpoint; API key (opt); Model; Output dimensionality (Gemini); Custom request headers; Chunking (max chunk size) | `embeddings` / `SectionEmbeddings` + runtime keys | ✅ (verify custom headers + dimensionality) |
| **Image Captioning** | Enable captioning at ingest (vision LLM on extracted images, cache by hash) | — (Synapse has vision path G-P2-1) | 🟡 no dedicated toggle page |
| **External Information Sources** | Deep Research mode (Web/AnyTXT/Both); AnyTXT local; Web providers: Ollama, Tavily, SerpApi, SearXNG, Firecrawl, Brave | Synapse = **SearXNG only** (I9) | ⭐ divergence-by-invariant |
| **Network** | Enable proxy; Proxy URL; Bypass local | — | ❌ no proxy settings |
| **Source Watch** | Monitor + auto-ingest toggles; **Allowed file types** by group (Docs/Presentations/Spreadsheets/Web/Data-config); max auto-ingest size (MB); Excluded folders | `sourceWatch` / `SectionSourceWatch` | 🟡 narrower allowed-types set (see §12) |
| **Scheduled Import** | Enable; Monitor directory (external); Scan interval; Scan Now; last scan | Synapse `OpsScheduleCard` / import scheduler | 🟡 verify external-dir + Scan Now |
| **MinerU PDF** | Enable MinerU **cloud** parser | Synapse = **Marker local** (`pdf_extractor`) | ⭐ divergence-by-design (superset) |
| **API + MCP** | Enable local HTTP API; allow-without-token; LAN access; status+base URL; access token (generate/reveal/copy); `curl … /api/v1/projects` | `apiMcp` / `SectionApiMcp` | ✅ |
| **Output** | AI Output Language (Auto/lang); Conversation History Length (2/4/6/8/10/20) | `SectionOutput` | ✅ |
| **Interface** | UI Language (English/中文); Theme (Light/Dark/System); Interface zoom % | `SectionInterface` (IT/EN) | ✅ (lang set differs: IT/EN vs EN/中文) |
| **Maintenance** | **Detect duplicate entities/concepts** → Scan for duplicates (guided merge) | `maintenance` / `SectionMaintenance` | 🟡 verify dedupe-merge tool |
| **Changelog** | renders CHANGELOG | `changelog` | ✅ |
| **About** | version/info | `about` | ✅ |

**Settings IA note:** LLM Wiki = flat 15-item list. Synapse = grouped nav (providers/context/
sourceWatch/embeddings/apiMcp/appearance{Interface+Output}/maintenance/changelog/about + runtime-
config sections). Alignment decision needed: flatten to mirror, or keep grouped.

---

## 12. Concrete gaps to close (draft, pending scope confirmation)
1. ❌ **Skills view** (rail #10) — discover/enable agent skills for Chat.
2. ❌ **Editable purpose.md / schema.md** in-app (currently read-only `MetaFileView`).
3. ❌ **Whole-vault Files tree** + `Open project folder` (vs raw-only SourcesView).
4. 🟡 **Source Watch allowed types** — add .doc/.odt/.rtf/.odp/.ods/.csv/.html/.mdx (Synapse:
   pdf/docx/pptx/xlsx only) + grouped checkbox UI + excluded-folders + max-size (ties to 1.4.1 Marker work).
5. 🟡 **Image Captioning** dedicated toggle page.
6. ❌ **Network proxy** settings page.
7. 🟡 **Chat composer**: `Skills` button, `AnyTXT` toggle, `Fast/Standard/Deep/Local first` pills.
8. 🟡 **Maintenance**: duplicate-entity detection + guided merge.
9. 🟡 **Settings IA**: decide flat-15 vs grouped.

## 13. Divergences to NOT mirror (invariant-protected — escalate before touching)
- ⭐ **Web search backend:** LLM Wiki offers Tavily/SerpApi/Firecrawl/Brave/Ollama; Synapse
  invariant **I9 = SearXNG only**. Mirroring the multi-provider picker violates I9.
- ⭐ **PDF parser:** MinerU **cloud** vs Synapse **Marker local** (privacy). Mirror the *toggle
  concept* ("high-quality PDF parser on/off"), not the cloud upload.

## 14. Multi-vault = Project Launcher/Switcher (RESOLVED)
Reached via the **two-arrows (⇄) icon at the very bottom of the rail** (below Settings). Full-screen
launcher:
- Title `LLM Wiki` + tagline "Build and maintain your personal knowledge base with LLMs".
- **`+ New Project`** — create a new vault (choose a folder → scaffolds `raw/`, `wiki/`,
  `purpose.md`, `schema.md`).
- **`Open Project`** — folder picker to open an existing vault.
- **Recent Projects** list — name + path, click to switch active vault. Live entries:
  `LLM Wiki` and `LLM Wiki Local` (both under `~/Documents/00_Personal/01_Wiki/`).
Each **project = a vault folder**. This maps to the HTTP API's `/api/v1/projects`.
→ **Synapse gap:** Synapse is single-vault (FastAPI single `VAULT_ID`; `vault_id` column plumbed
but unused). Alignment = (a) backend: multi-vault registry + active-vault switch + new/open;
(b) frontend: launcher screen + ⇄ rail icon + recent-projects store. **Large cross-stack feature.**

---

## 15. Confirmed scope (user, 2026-07-10)
- **Multi-vault:** build the Project Launcher/Switcher in Synapse (⇄ bottom icon → New/Open/Recent).
- **Invariant divergences:** **mirror LLM Wiki literally** — add multi-provider web search
  (Tavily/SerpApi/Firecrawl/Brave/Ollama alongside SearXNG) and the MinerU cloud PDF toggle.
  → **Requires amending CLAUDE.md invariants I9 (SearXNG-only) + the Marker-only posture, with an
  ADR.** Escalation acknowledged by the owner.
- **Priority gaps (all):** Settings parity · Chat composer · editable purpose/schema + Files tree ·
  Skills view.
- **Sequencing note:** this is a v1.5-scale program, distinct from the ready 1.4.1 patch
  (Marker chunking + graph-count fix).
