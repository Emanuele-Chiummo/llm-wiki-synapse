# Synapse ⇄ llm_wiki — UI/UX Alignment Plan (2026-07)

> **Purpose:** close every user-visible gap found by the 5-area screenshot-driven audit
> (2026-07-06) so Synapse behaves **exactly like nashsu/llm_wiki** (feature-for-feature),
> keeping Synapse's superior internals (I1–I9). Companion to
> `SYNAPSE-VS-LLMWIKI-PARITY.md` (backend/behavioral parity — closed at v1.3); this plan
> covers the **UI affordances** the live llm_wiki screens revealed as still missing.
>
> **Method:** 5 parallel code audits (Lint, Graph, Reader, Sources, Chat) against the
> Synapse codebase + llm_wiki source (`chat-input.tsx`, `lint-view.tsx`, `lint-fixes.ts`).
> Every gap below is code-anchored: PRESENT items are NOT re-listed.
>
> **Rule inherited from the parity program:** mirror the user-facing PROCESS; keep the
> better internals; real backend — no UI stubs.

---

## Batch overview (proposed execution order)

| Batch | Area | Priority | Why first |
|---|---|---|---|
| **B1** | Lint overhaul | **P0** | Largest visible gap; the live llm_wiki screen (1161 issues, batch bar, suggested targets) has no Synapse equivalent |
| **B2** | Chat composer | **P0** | Attach image + Web search + retrieval modes — the entire composer toolbar is missing |
| **B3** | Graph header + Sources | P1 | Header stats/search/filter; folder import/delete + footer count |
| **B4** | Reader polish | P2 | Tag overflow, updated-line; smallest gaps |

Every batch: green tests + preview-verified (live browser) + docs gate (I8) before merge.

---

## B1 — LINT (P0) — owner: [BE]+[AI]+[FE] — STATUS: ✅ SHIPPED feat/b1-lint-parity

> **Batch closed 2026-07-06.** All 10 lint gaps (L1–L10) shipped in branch
> `feat/b1-lint-parity`. Governing ADR: ADR-0058 (accepted 2026-07-06).
> Parity doc updated: section 19b added to `SYNAPSE-VS-LLMWIKI-PARITY.md`.
> Sequence diagram updated: `docs/sequences/lint-fix.mmd`.

llm_wiki reference: header `issue-count badge` + `Semantic (LLM)` checkbox + `Run Lint`;
batch bar `Select all / Fix selected / Send selected to Review / Ignore selected`;
severity group headers (`⚠ Warnings (741)`); per-row `Open` + `Fix`; green
`Suggested target:` strip; `Delete` on orphans.

| ID | Gap | Deliverable | Closure |
|---|---|---|---|
| **L1** | No `broken-wikilink` category — dangling links tracked in DB but never surfaced | New **deterministic** finding category `broken-wikilink` derived from `links.dangling=True` (`backend/app/wiki/links.py`). Zero LLM cost (I7). This is what fills llm_wiki's "741 warnings". | ✅ shipped feat/b1-lint-parity · `backend/app/ops/lint.py` · ADR-0058 §2.1 |
| **L2** | No structured suggested target | New columns on `lint_findings`: `suggested_target` (text) + `suggested_page_id` (FK pages, nullable) — computed at scan time via the tolerant resolver. Green strip in UI. Alembic migration 0024 + `make er` (I8). | ✅ shipped feat/b1-lint-parity · `backend/app/models.py` + migration 0024 · ADR-0058 §3 |
| **L3** | `Fix` for broken-wikilink | `apply_lint_fix` new branch: rewrite dangling `[[target]]` → suggested title (one link, one page, one bump — I1/I5). No suggestion → flag-only. | ✅ shipped feat/b1-lint-parity · `backend/app/ops/lint.py` · ADR-0058 §2.2 |
| **L4** | No `Open` (navigate to page) | Per-row `Open` button wired to `selectPage(target_page_id, "tree")` (wire-only; seam already existed). For broken-wikilink: opens referencing page. | ✅ shipped feat/b1-lint-parity · `frontend/src/components/lint/LintView.tsx` · ADR-0058 §2.6 |
| **L5** | No multi-select + batch bar | `selectedFindings: Set<id>` in lintStore + `Select all` + batch bar. Backend: `POST /lint/findings/batch {ids[], action}` (cap ≤ 200, sequential — I7). | ✅ shipped feat/b1-lint-parity · `backend/app/routers/lint.py` + `frontend/src/components/lint/LintView.tsx` · ADR-0058 §2.3 |
| **L6** | No lint→Review bridge | `POST /lint/findings/{id}/send-to-review` → `review.enqueue_review()` (category→item_type map); FNV-1a idempotent. | ✅ shipped feat/b1-lint-parity · `backend/app/routers/lint.py` · ADR-0058 §2.3–§2.4 |
| **L7** | No severity group headers | `Errors (N) / Warnings (N) / Info (N)` synthetic rows inside TanStack-virtualised list (I4). | ✅ shipped feat/b1-lint-parity · `frontend/src/components/lint/LintView.tsx` · ADR-0058 §2.6 |
| **L8** | No `Semantic (LLM)` toggle | `POST /lint/scan?semantic=bool` (default true); `false` → deterministic-only, free. Checkbox in header, persisted in settingsStore. | ✅ shipped feat/b1-lint-parity · `backend/app/routers/lint.py` · ADR-0058 §2.3 |
| **L9** | No `Delete` for orphan pages | `DELETE /pages/{id}`: two-stage UI confirm; soft-delete + Qdrant remove + file delete + index.md/log.md cleanup + dead-wikilink→plain-text (cascade-delete seam, I5); one bump (I1). K8-safe. | ✅ shipped feat/b1-lint-parity · `backend/app/routers/pages.py` · ADR-0058 §2.5 |
| **L10** | No category/severity filters | `GET /lint/findings?category=&severity=` enum-validated params + filter chips in UI. | ✅ shipped feat/b1-lint-parity · `backend/app/routers/lint.py` + `frontend/src/components/lint/LintView.tsx` · ADR-0058 §2.3 |

**Invariants:** I1 (single-page edits, one bump), I4 (virtualized+headers), I5 (body-only
wikilink writes), I6 (semantic pass stays provider-neutral), I7 (batch caps; L1 is free),
K8 (delete = human double-confirm; fixes stay human-gated).
**ADR:** ADR-0058 (accepted 2026-07-06) — extends ADR-0037; see `docs/adr/0058-lint-parity-extension.md`.

**Live-preview findings (2026-07-06, 986-page real vault):**
- ✅ **Fixed in-session:** deterministic findings were being crushed by the semantic cost-cap
  (`LINT_MAX_FINDINGS=50`) — broken-wikilink (150 findings) was truncated to zero behind 109
  orphans. Cap now bounds the semantic tail only; broken-wikilink ordered first. (ADR-0058 §2.1a;
  `backend/app/ops/lint.py`.) Re-verified: 150 broken-wikilink + 109 orphan render, grouped,
  batch bar + Open/Send-to-review/Delete all present, cost $0.0000 (semantic off).
- 🟡 **Follow-up L11 (P2):** severity group header shows *loaded* count (50) not the per-severity
  *total* (llm_wiki shows "Warnings (741)" = total). Needs `GET /lint/findings` to return
  per-severity totals, or the header to read `findingsTotal`. Frontend-only. [FE]
- ℹ️ **Suggested-target strip:** correct but data-dependent — this vault's remaining 150 dangling
  links are genuinely unresolvable (the resolvable ones were already reconnected by a prior
  `POST /links/reresolve`), so `suggested_target` is NULL for all. The green strip is proven by
  frontend unit tests; it will render live on any vault with near-miss dangling links.

---

## B2 — CHAT COMPOSER (P0) — owner: [AI]+[BE]+[FE] (+[SA] ADR) — STATUS: ✅ SHIPPED feat/b2-chat-composer

> **Batch closed 2026-07-06.** C1/C2/C3 shipped; C4 declined (⛔ do-not-mirror). Governing
> ADR: ADR-0059 (accepted, NO OBJECTIONS). Parity doc §19c added; sequence diagram
> `docs/sequences/chat-retrieval-web.mmd`. Code: `backend/app/ingest/provider/*` (vision
> gate + Message.images), `backend/app/chat/web_context.py` + `rag/retrieval.py` presets +
> `routers/chat.py` (request schema) + `routers/status.py` (supports_vision), migration
> **0025** (messages.images, chained on B1's 0024 — merge B2 after B1),
> `frontend/src/components/chat/MessageInput.tsx` + `MarkdownView.tsx` + `decorateCitations.ts`.
>
> **Live-preview verified (986-page real vault):** composer renders all 3 controls
> (Allega immagine / Web / Veloce·Standard·Profondo·Locale prima); attach-image enabled via
> `GET /status supports_vision:true` (CLI/claude-haiku); **C3 end-to-end** — a "Profondo"
> query returned 200 OK with deep retrieval (200 citations, `[14]`/`[185]` rendered).
> Deferred to full-infra env: C1 image round-trip (file upload + vision call) and C2 web
> search (SearXNG lives on TrueNAS, unreachable from local Docker). Tests: 51 backend
> (35 composer + 16 provider images) + 1822 frontend, all green.
> **Note (I3):** the `[G3]` dev-guard fires on the test conversation's two identical "ciao"
> messages (same content × StrictMode > 2) — MarkdownView is I3-clean (renderMarkdown once,
> decorations are string-passes); pre-existing guard sensitivity, not a B2 regression (P3).

llm_wiki reference (`chat-input.tsx`): Attach image (multimodal, previews, count/size
caps) · Web search toggle (emerald dot) · AnyTxt toggle (Windows-only, greyed when
unavailable) · agent-mode segmented `Fast | Standard | Deep | Local first` · Send/Stop.

| ID | Gap | Deliverable | Design notes |
|---|---|---|---|
| **C1** | No image attach in chat | Composer "Attach image" (picker + inline thumbnails + caps `CHAT_MAX_IMAGES`, `CHAT_MAX_IMAGE_BYTES`); `ChatMessageIn.images[]` (base64+mime) in `POST /chat/stream`; provider `chat()` passes images only when `capabilities().supports_vision` — otherwise composer button disabled with tooltip (capability-aware, I6). Persist images ref in `messages` row for history/regenerate. | Vision plumbing exists in ingest (`caption_image`) — reuse the provider surface, do NOT duplicate |
| **C2** | No Web-search toggle | Toggle in composer → `use_web_search: bool` on ChatStreamRequest. When on: bounded SearXNG call (`ops/searxng.py`, I9) → top-N results fetched/stripped (reuse deep-research `_fetch_max_chars`) → injected as a **separate, labeled context block** with its own citation namespace (`[W1]…`). Bounds: `CHAT_WEB_MAX_RESULTS`, `CHAT_WEB_FETCH_MAX_CHARS`, cost logged (I7). | **ADR required:** amends ADR-0050 (wiki-only retrieval) — web block is additive and clearly separated; wiki citations `[n]` unchanged |
| **C3** | No retrieval modes | Segmented `Fast / Standard / Deep / Local first` → `retrieval_mode` on ChatStreamRequest, mapped to **deterministic presets** (keeps Synapse's ⭐ single-pass pipeline; llm_wiki uses agent rounds — we mirror the user-facing effect, not the internals): **Fast** = vector-only k=4, no expansion; **Standard** = today (k=8, depth 1); **Deep** = k=12, depth 2 (hard max), larger retrieval slice of the F14 budget; **Local first** = web-search suppressed unless local retrieval returns < threshold results (only meaningful with C2 on). Persisted per-conversation default in settingsStore. | **ADR required:** mode-preset table frozen in ADR (no per-request arbitrary depths — I7). Deep respects `expansion_depth ≤ 2` hard cap |
| **C4** | AnyTXT toggle | **DO-NOT-MIRROR (documented).** AnyTXT is a Windows-only local indexing service; Synapse targets TrueNAS/Docker + macOS. Parity doc gets a ⛔/N-A row with rationale. (Optional future: ripgrep-based raw/ full-text search — out of scope.) | Decision row only, no code |

**Invariants:** I3 (images never re-render per token; thumbnails static), I6 (vision gated
on capabilities, no isinstance), I7 (web fetch + image caps bounded, cost logged), I9
(SearXNG only). **ADR:** new ADR "Chat retrieval modes + web context block" ([SA]).

---

## B3 — GRAPH HEADER (P1) — owner: [FE]+[BE] — STATUS: ✅ SHIPPED feat/b3-graph-look

> **Shipped 2026-07-06 (same branch as B3-LOOK).** GR1 stats chips
> (`{shown}/{total_nodes} pagine · {shown}/{edges.length} link · {hidden} nascosti`; backend
> `GET /graph` gains `total_nodes`/`total_edges`, `routers/graph.py`), GR2 in-graph search
> (find + camera-center), GR3 type filter (client visibility, I2-safe — hides nodes+incident
> edges; `graphStore.filterNodeTypes`), GR4 Reset, GR7 fullscreen, GR5 legend counts. Also a
> **declutter pass** on B3-LOOK (user feedback "più caotico"): hub K 10→6, labels truncated
> to ≤18ch, `labelRenderedSizeThreshold` 8→11, edge-cull 0.22→0.30 / 0.32→0.42.
> **Link-chip denominator = `edges.length`** (full graph edge set 4213), NOT backend
> `total_edges` (link-rows 3397) — the graph edge set is a wikilink∪source superset (ADR-0016).
> **Live-verified:** at rest legible clusters; filter→Entity shows 176/986 pages, 810 nascosti,
> clean subgraph. Tests: 93 frontend (26 new graphHeader) + 22 backend green.
> **Open polish (P3):** at-rest link chip shows `4213/4213` (GL1-culled edges not counted as
> "hidden" — only filter is); Insights panel open-by-default covers ~1/3 screen (default-collapse).

> **GR9 — "Comunità" mode groups by DOMAIN, merged (SHIPPED, user follow-ups "duplicati" +
> "non aggiungere una nuova voce"):** the second color toggle stays labeled "Comunità" but now
> colors + aggregates by the node's own domain (`GraphNode.domain` = first in-vocab `domain/*`
> tag, backend). Legend = ONE row per domain (SAM/Procurement/ServiceNow/Regolamentazioni/TPRM +
> "Senza dominio"), no duplicate Louvain rows. Default color mode = this. No third toggle added.
> Centroid overlays label per-domain. Louvain community still computed (used by Insights only).
> Also: **Insights panel collapsed by default** (was covering ~1/3 of the canvas). Live-verified:
> 2 toggles, 6 clean domain rows, no raw i18n. **P3 polish:** a domain centroid label near the top
> edge can overlap the header (clamp to canvas). Tests: 62 backend + 1861 frontend green.

> **GR8 — Named communities (superseded by GR9 for the color toggle; per-community
> `dominant_domain`/`label` still computed for Insights). Original: user request "le comunità nominative come i domini"):**
> each Louvain community is labeled by its **dominant domain** (F18 `domain/*` tags →
> `effective_domain_vocabulary`), fallback = top-degree page title, else "Comunità {id}".
> Backend: `engine.py` step 4f computes label/dominant_domain/top_page in recompute (I1/I2 —
> no extra scan, cached); `GraphCommunityResponse` gains those fields. Frontend: Community-mode
> legend shows the names + `graphCommunityUtils.computeCommunityCentroids` renders per-cluster
> HTML label overlays projected via `sigma.graphToViewport` (rAF-throttled, I2 — never mutates
> coords). Live-verified: SAM/Procurement/Regolamentazioni/TPRM in legend + on clusters.
> **Also fixed:** GR1's `total_edges` COUNT broke `test_louvain_community.py` (fixture lacked
> `links` table + `tags` column) — fixture repaired (21/21 green).

llm_wiki reference: header `801/804 pages · 2438/2823 links · 3 hidden` + Search +
Filter + Reset + Type/Community + `Insights 13` + refresh; collapsible Node-Types legend
with per-type counts; permanent labels on hubs.

| ID | Gap | Deliverable |
|---|---|---|
| **GR1** | No header stats | `GraphHeader.tsx` above the canvas: `shown/total pages`, `shown/total links`, orange `N hidden` chip. Backend: add `total_nodes`, `total_edges` to `GET /graph` (routers/graph.py:186 — counts of live pages/links pre-filter). Shown = post-filter client counts. |
| **GR2** | No in-graph search | Header search input (prefix/fuzzy on node titles, client-side — nodes already in store) → select + animated camera center on match; Esc clears. |
| **GR3** | No type/community filter | Filter popover: multi-select node types + communities → client-side visibility (hide nodes + incident edges). **I2-safe: visibility only, never re-layout.** Store slice `filterNodeTypes/filterCommunities`. Hidden count feeds GR1. |
| **GR4** | No Reset | Header `Reset` = clear filters + camera fit (compose existing `handleFit`). |
| **GR5** | Legend: no counts, not collapsible | Per-type node counts in legend rows (client reduce) + collapse/expand header (matches llm_wiki bottom-left panel). |
| **GR6** | No permanent hub labels | `forceLabel=true` for top-K degree nodes (K≈8, threshold configurable) — matches llm_wiki's always-visible labels on hubs. |
| **GR7** | No fullscreen | Fullscreen button (browser Fullscreen API on graph container). |

**Invariants:** I2 (filtering = render-visibility only; FA2 stays server-side), I3.

### B3-LOOK — Graph visual-parity levers (why Synapse's graph "looks different" from llm_wiki)

> **GL1/GL2/GL3 SHIPPED feat/b3-graph-look (2026-07-06). ADR-0060.** Render-only, I2 preserved
> (no client layout). `frontend/src/api/graphTransform.ts` (`edgeVisibilityThreshold`,
> `computeTopKHubs`, `densityScale`) + `GraphViewer.tsx` (edgeReducer hide/hover-reveal, hub
> `forceLabel`, `labelRenderedSizeThreshold` 13→8). Tests: 47 graph-transform + 22 no-client-layout
> + 142 graph green. **Live-verified (816-node real vault):** hub labels visible
> (Category Management Framework, SAM ITAM Overview…), nodes down-scaled, weak edges culled →
> legible clusters instead of the uniform "ball". Remaining spherical envelope = GL4 (seed),
> deferred spike. GL5/GL6 declined (see rows).

> Root-caused 2026-07-06 (code-anchored, `engine.py:_forceatlas2_layout` + `graphTransform.ts`
> vs llm_wiki `graph-view.tsx`). **The data is the same** (identical 4-signal weights
> `3·direct + 4·source + 1.5·AA + 1·type`; ~6 avg degree post-dangling-fix). The gulf is
> **render/layout**, not content. Architect verdict: do the 3 safe high-ROI levers now;
> treat the seed change as a measured spike; do NOT touch the edge-set model or the FA2 lib.

| ID | Lever | Do it? | Change | Prio | Risk |
|---|---|---|---|---|---|
| **GL1** | **Cull weak edges** | ✅ **yes — highest ROI** | `edgeReducer` (or build-time) hides edges with `normalizedWeight < edgeVisibilityThreshold(n)` — revealed on hover/highlight. Mirrors llm_wiki. Render-only → **I2-safe** (no layout/coord/weight change). Turns the hazy ball into legible clusters; alone closes ~60-70% of the perceived gap. | **P1** | Low |
| **GL2** | **Hub labels + lower at-rest threshold** | ✅ yes (= GR6) | `forceLabel=true` on top-K degree nodes; drop `labelRenderedSizeThreshold` 13→~8 at fit-view. Anonymous dots → readable map. | **P1** | Low |
| **GL3** | **Node density down-scale** | ✅ yes (bundle) | Multiply node size by `√(150/n)` for large graphs (llm_wiki parity), reducing overlap. Modest but cheap+safe. | **P2** | Low |
| **GL4** | **Seed: circle → seeded random-in-disc** | ⚠️ **spike only** | The circular `layout_circle()` seed is what leaves the spherical envelope — BUT it is a deliberate **ADR-0045 determinism** choice (enables server-side coord cache, I2, no-jump). Replace with a **numpy-seeded** random-in-disc init to keep determinism; **measure** (60 iters at ~800 nodes may not escape the ring regardless — may also need an iteration bump for the 400-1000 bucket). Requires an **ADR-0045 amendment**. Not a blind change. | **P2 (spike)** | Med (touches ADR-0045/I2) |
| **GL5** | **Edge-set = wikilink ∪ shared-source** | ❌ **no** (opt. view toggle) | ADR-0016; deliberate, improves retrieval. Do NOT change the model for looks. GL1 already hides the weak source-clique noise. At most a P2 "wikilinks-only" **view toggle** in the graph header (render filter, I2-safe). | P2 (toggle only) | — |
| **GL6** | **Swap FA2 library** (fa2_modified → graphology) | ❌ **no** | High risk, marginal gain. The library is not the problem. | — | — |

**Sequence:** GL1 → GL2 → GL3 (safe, do together), then GL4 as a measured spike behind an
ADR-0045 amendment. **GL5/GL6 are deliberately declined** (do not degrade superior internals
for appearance). Owner: [FE] (GL1/GL2/GL3/GL5-toggle), [BE]+[SA] (GL4 spike + ADR).
**Acceptance:** side-by-side preview vs the llm_wiki graph screenshot; I2 preserved (coords
still server-side/cached, deterministic); no change to edge weights or the 4-signal formula.

---

## B3b — SOURCES (P1) — owner: [FE]+[BE]

| ID | Gap | Deliverable |
|---|---|---|
| **S1** | No "+ Folder" import | Header `+ Folder` button: `<input webkitdirectory>` → client walks FileList → sequential multipart uploads preserving relative paths (`POST /ingest/upload` gains optional `rel_dir`, path-safe via `resolve_under_sources`); reuses bulk-progress bar. Watcher/ingest picks files up as today. |
| **S2** | No per-folder delete | Trash on folder rows → two-stage confirm ("N files") → `DELETE /sources?path=<dir>` extended to directories (recursive, bounded `SOURCES_DELETE_MAX_FILES`, cascade per file — reuses existing per-file cascade path; I7 cap). |
| **S3** | No footer total | Footer bar: `{total} sources` (field already in `GET /sources` response — display-only) + existing Refresh. |

**Invariants:** I1 (deletes cascade per file through the existing seam), I7 (recursive
delete capped).

---

## B4 — READER (P2) — owner: [FE]

| ID | Gap | Deliverable |
|---|---|---|
| **R1** | Tags overflow (100 tags wrap forever) | Show first N (≈24) + `More (+K)` expander chip toggling full list (mirrors llm_wiki overview.md card). |
| **R2** | No monospace `updated:` line | Collapsible "More" metadata footer under the card: monospace `updated: <ISO>` (+ any extra frontmatter fields) — matches llm_wiki. `created_at` optional (needs API field — only if cheap). |
| **R3** | No filename bar + close X | **Optional / design-review:** Synapse navigates via tree; llm_wiki uses overlay-reader with close. Adopt slim filename bar (`file_path` + X → back to tree selection none) for visual parity. [SA] to confirm. |

---

## B5 — README delta (llm_wiki has evolved past the audit baseline v0.5.4)

> The current llm_wiki README (2026-07) reveals features **newer than the audit baseline**
> (`c03c6be`). Verified against Synapse code 2026-07-06.

| ID | llm_wiki (README) | Synapse today | Verdict / deliverable | Prio |
|---|---|---|---|---|
| **D1** | Multimodal image ingestion: extract embedded images **from PDFs** (min 100px, cap/doc), vision captions, **image-aware search results with lightbox + jump-to-source** | Vision captions for standalone image files only (G-P2-1); `extract.py` has NO embedded-PDF image extraction; no image results/lightbox in SearchView | New pipeline: pypdf/Marker image extraction → assets + caption (reuse `image_captions` SHA-256 cache, `VISION_MAX_IMAGES_PER_RUN` I7) → captions embedded → SearchView image results + lightbox + jump-to-source. [AI]+[BE]+[FE] | P1 |
| **D2** | Local HTTP API `127.0.0.1:19828` token-protected: projects, files read, **reviews export/PATCH/bulk-resolve**, hybrid search, graph, sources rescan + **MCP server** with same surface + **installable agent skill** (`npx skills add llm_wiki_skill`) | REST is richer overall (full FastAPI + Bearer ADR-0052), but **MCP exposes only 4 tools** (`search_wiki, write_page, get_page, list_pages` — mcp/server.py:386-489); review has NO bulk-resolve/PATCH; no agent-skill package | (a) Extend FastMCP: `get_graph_neighborhood`, `list_reviews(status)`, `resolve_review(s)`, `trigger_source_rescan`, `read_source_file` (read-only default, write gated as today). (b) `POST /review/queue/bulk-resolve` + `PATCH /review/queue/{id}`. (c) Publish a **synapse-skill** repo (Claude Code/Codex skill hitting the REST/MCP surface, trigger-disciplined like llm_wiki_skill). [BE]+[AI] | P1 |
| **D3** | Deep Research from Graph Insights: LLM-optimized topic + **editable confirmation dialog** (topic + queries) before start | Gap button navigates only; `researchStore` has no seed/confirm (verified — no prefill) | Seed action: insight → LLM topic optimization (reads overview+purpose, bounded I7) → editable confirm dialog → run. Upgrades the parity-doc P2 leftover. [FE]+[AI] | P1 |
| **D4** | Multi-project: projects API, clipper project picker, create-project wizard | Single vault per instance (`vault_id="default"`, config.py:110; `vault_id` plumbed in DB) | **Decision required:** keep single-vault-per-deployment (documented ⭐, Docker-native) vs implement multi-vault. Recommendation: keep; revisit post-2.0. [SA] | decision |
| **D5** | KaTeX auto-wrap bare `\begin{env}` | Known P2 leftover (G-P1-2 note) | Fold into **B4** (R4). [FE] | P2 |
| **D6** | Tauri: macOS close-to-hide; Win/Linux close confirmation | No close-behavior config found in src-tauri | Small Tauri polish item. [FE] | P2 |
| **D7** | Search "both wiki/ AND raw/sources/" (README claim — was FALSE at v0.5.4; may be true now) | ADR-0050: wiki-only (mirrors v0.5.4 actual behavior) | Re-verify against **current** llm_wiki code; if now real, consider opt-in raw/ scope in the Search view only (chat citations stay wiki-only). [SA] | verify |
| **D8** | Providers: OpenAI, Anthropic, **Google**, Ollama, Custom | ApiProvider = Anthropic + OpenAI-compatible `base_url` (Gemini reachable via its OpenAI-compat endpoint) | Parity via base_url — document in USER.md; no code. | — |
| **D9** | Web fetch "no truncation"; 15-min timeout; LanceDB optional vector | Synapse: bounded fetch 20k (I7 ⭐), 60s configurable timeout ⭐, Qdrant/bge-m3 always-on with lexical fallback ⭐ | Already better — no action. | — |

> ⚠️ README reliability: the v0.5.4 audit proved several README claims false (2-hop decay,
> green edges, edge hover labels, raw/ search). Treat README-only claims as **unverified**
> until re-audited against current main (D7). Recommend re-baselining the llm_wiki audit.

---

## Decisions & do-not-mirror (this plan)

| Item | Decision |
|---|---|
| AnyTXT | ⛔ do-not-mirror (Windows-only service; platform N/A). Documented in parity doc. |
| Agentic chat rounds (llm_wiki 3–5 decision rounds) | Keep Synapse deterministic single-pass (⭐, cost-predictable); mirror the *user-facing* modes via presets (C3). |
| Graph filter | Client-side visibility only — never touches server layout (I2). |
| Lint auto-fixes | Still human-gated (K8); broken-wikilink Fix is the same safety class as missing-xref (bounded body edit). |

## Acceptance (per batch)

- Feature-for-feature match with the reference screenshots (side-by-side preview check).
- pytest + vitest green; new endpoints in regenerated `docs/api/openapi.json` (I8).
- ER regenerated if migrations (L2) — `make er`.
- ADRs: ADR-0037 amendment (B1), new chat-modes/web-block ADR (B2).
- Parity doc updated: new rows for L*/C*/GR*/S*/R* marked closed as they ship.

*End of plan.*
