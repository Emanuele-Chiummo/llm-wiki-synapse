# Architecture Decision Records

This index documents all architectural decisions made during Synapse development (v0.1–v0.9+).

Each ADR covers design choices, rationale, and implications. Newer decisions can supersede older ones — see the body of each ADR for status (Accepted / Superseded / Deprecated).

---

## Core Patterns & Schema (ADR-0001 through ADR-0011)

| # | Title | Status |
|---|-------|--------|
| **0001** | [Incremental indexing strategy (mtime-then-hash)](0001-incremental-indexing-strategy.md) | Accepted |
| **0002** | [Datastore split: Postgres for metadata/links, Qdrant for vectors](0002-datastore-split-postgres-qdrant.md) | Accepted |
| **0003** | [Thin ingest seam preserves F17 pluggable provider (I6)](0003-thin-ingest-seam-preserves-f17.md) | Accepted |
| **0004** | [Embedding dimension and embedding endpoint are configuration, never hardcoded](0004-embedding-dimension-config-policy.md) | Accepted |
| **0005** | [Soft-delete for pages and startup-seeded vault_state](0005-soft-delete-and-vault-state-seeding.md) | Accepted |
| **0006** | [POST /ingest/trigger response contract and startup behaviour](0006-ingest-trigger-response-and-startup-behavior.md) | Accepted |
| **0007** | [InferenceProvider ABC and capability-aware routing (I6)](0007-inference-provider-abc.md) | Accepted |
| **0008** | [provider_config + ingest_runs schema; secrets via env only (I6, §12)](0008-provider-config-schema.md) | Accepted |
| **0009** | [Bounded ingest loop: defaults, cost accounting, token-usage normalization (I7)](0009-bounded-loop-defaults.md) | Accepted |
| **0010** | [MCP server: stdio transport in v0.2; write_page reuses the ingest primitives (I6, I1, I9)](0010-mcp-transport-and-write-path.md) | Accepted |
| **0011** | [The ingest contract: Pydantic schemas for Analysis / WikiPage / Message / ProviderCapabilities](0011-ingest-contract-schemas.md) | Accepted |

---

## Graph & Rendering (ADR-0012 through ADR-0016)

| # | Title | Status |
|---|-------|--------|
| **0012** | [4-signal graph edge-weight formula (F4)](0012-graph-edge-weight-formula.md) | Accepted |
| **0013** | [Server-side FA2 layout, coordinate persistence, and determinism seed (I2)](0013-server-side-fa2-coord-persistence.md) | Accepted |
| **0014** | [GraphCache debounce, dataVersion trigger, and GET /graph contract (I2)](0014-graph-cache-debounce-and-graph-endpoint.md) | Accepted |
| **0015** | [No client-side layout: sigma.js viewer contract (I2 / I4 / I3)](0015-no-client-side-layout-sigma-contract.md) | Accepted |
| **0016** | [Obsidian-style graph: structural edges, real-connection sizing, type-as-modulator (F4)](0016-obsidian-graph-rendering.md) | Accepted |

---

## UI & Frontend Shell (ADR-0017 through ADR-0021)

| # | Title | Status |
|---|-------|--------|
| **0017** | [Three-panel shell: layout, resizing, shared selection model (F1)](0017-three-panel-shell.md) | Accepted |
| **0018** | [NavRail IA, Ingest Activity View, Provider Selector, Settings, i18n (M4 Phase 2)](0018-navrail-ingest-provider.md) | Accepted |
| **0019** | [Chat: streaming transport, persistence, `<think>`/LaTeX, and the G3 gate (M4 Phase 3)](0019-chat-streaming.md) | Accepted |
| **0020** | [Document Upload + Scheduled Folder Import (M4-EXT)](0020-upload-and-scheduled-import.md) | Accepted |
| **0021** | [Labeled NavRail Standard + Provider Config CRUD Contract (M4-HARD)](0021-labeled-navrail-and-provider-crud.md) | Accepted |

---

## Retrieval, Chat & Content (ADR-0022 through ADR-0027)

| # | Title | Status |
|---|-------|--------|
| **0022** | [F5 4-phase retrieval + `[n]` citation architecture (M5 Phase 1)](0022-retrieval-and-citations.md) | Accepted |
| **0024** | [Deep Research: bounded multi-query SearXNG loop + ingest-seam synthesis (F10)](0024-deep-research.md) | Accepted |
| **0025** | [HITL Review Queue + Multi-format ingest (F9 + F12)](0025-review-queue-and-multiformat.md) | Accepted |
| **0026** | [Cascade deletion of wiki pages (F13)](0026-cascade-delete.md) | Accepted |
| **0027** | [Read-only MCP server introspection endpoint + Settings panel (F1-MCP-UI)](0027-mcp-info-ui.md) | Accepted |

> **Note:** ADR-0023 was intentionally skipped during sprint v0.5 — the number was reserved but never assigned. No document exists for 0023; this is a historical gap in the numbering sequence, not a missing index entry.

---

## Configuration & Integrations (ADR-0028 through ADR-0043)

| # | Title | Status |
|---|-------|--------|
| **0028** | [Browser API base is relative; proxy target is a server-only env var](0028-relative-api-base-and-server-only-proxy-target.md) | Accepted |
| **0029** | [Remote MCP over HTTP: mount `mcp.http_app()` at `/mcp/server`, bearer-token auth, read-only by default](0029-remote-mcp-over-http.md) | Accepted |
| **0030** | [Embeddings on/off toggle with lexical degrade (global env flag, Postgres keyword fallback)](0030-embeddings-toggle-lexical-degrade.md) | Accepted |
| **0031** | [OpenAI-compatible embeddings adapter (explicit `EMBEDDING_FORMAT`, optional `EMBEDDING_API_KEY`)](0031-openai-compatible-embeddings-adapter.md) | Accepted |
| **0032** | [Remote MCP runtime toggle: persisted `remote_mcp_enabled` flag, always-mount + 404-gate, no remount](0032-remote-mcp-runtime-toggle.md) | Accepted |
| **0033** | [UI-settable MCP access token + "allow without token" (loopback/private only); hashed storage in `vault_state`, public source always requires the token](0033-ui-settable-mcp-token-allow-without-token.md) | Accepted |
| **0038** | [F11 Web Clipper: secure clip ingress model](0038-web-clipper-ingress-security-model.md) | Accepted |
| **0040** | [Web clipper runtime configuration (GET/PUT /clip/config; DB wins over env)](0040-web-clipper-runtime-configuration.md) | Accepted |
| **0041** | [SearXNG web-search runtime configuration (GET/PUT /web-search/config; DB wins over env)](0041-web-search-runtime-configuration.md) | Accepted |
| **0042** | [CLI provider subscription auth: OAuth token / ambient login, no API key (F17, I6/I7)](0042-cli-provider-subscription-auth.md) | Accepted |
| **0043** | [UI-settable CLI subscription OAuth token (DB-stored, injected into the spawned `claude` CLI, API key scrubbed from the child env) (F17, I6/I7, §12)](0043-ui-settable-cli-subscription-token.md) | Accepted |

---

## Advanced Features & Workflows (ADR-0034 through ADR-0051)

| # | Title | Status |
|---|-------|--------|
| **0034** | [Review Queue: proposal model with lazy on-demand page creation (F9 redesign)](0034-review-queue-proposal-model.md) | Accepted |
| **0035** | [Page content read/edit endpoints (`GET`/`PUT /pages/{id}/content`)](0035-page-content-read-edit-endpoints.md) | Accepted |
| **0036** | [Wikilink-enrichment post-pass (substitution-apply, bounded, provider-agnostic)](0036-wikilink-enrichment-post-pass.md) | Accepted |
| **0037** | [K2 Lint-fix loop (bounded, human-gated wiki health check)](0037-lint-fix-loop.md) | Accepted |
| **0044** | [Review Queue: contextual depth, stable idempotency & bulk actions (F9 depth pass)](0044-review-queue-contextual-depth-and-idempotency.md) | Accepted |
| **0045** | [ForceAtlas2 graph layout via fa2_modified (F4, I2)](0045-forceatlas2-graph-layout.md) | Accepted |
| **0046** | [Live ingest activity queue with cancel / pause / retry (F9-adjacent, watcher, I1/I7)](0046-ingest-activity-queue-cancellation.md) | Accepted |
| **0050** | [Retrieval scope restricted to wiki/ pages only](0050-retrieval-wiki-only-scope.md) | Accepted |
| **0051** | [Pluggable PDF extractor seam: Marker over HTTP with pypdf fallback](0051-pluggable-pdf-extractor-seam.md) | Accepted |
| **0058** | [Lint parity extension: broken-wikilink category, batch actions, review bridge, orphan delete (extends ADR-0037; B1)](ADR-0058-lint-parity-extension.md) | Accepted |
| **0059** | [Chat composer parity: attach-image (capability-aware vision), web-search toggle (amends ADR-0050 — additive `[W]` namespace), frozen retrieval-mode presets, AnyTXT do-not-mirror (B2; I3/I6/I7/I9)](ADR-0059-chat-composer-parity.md) | Accepted |
| **0060** | [Graph visual parity: render-only edge culling, hub labels, node density down-scale (F4, I2/I3; GL4 deferred, GL5/GL6 declined)](ADR-0060-graph-visual-parity.md) | Accepted |
| **0061** | [MCP tool expansion (graph-neighborhood / list-reviews / read-source read tools; resolve-review / trigger-rescan write tools gated like write_page) + review bulk-resolve/PATCH REST + trigger-disciplined agent skill (extends ADR-0010/0029/0033/0044; B5/D2; I1/I5/I6/I7/I9)](ADR-0061-mcp-expansion-and-skill.md) | Accepted |
| **0062** | [Cloudflare Access edge authentication + client service tokens (browser OTP cookie; iOS/clipper/frontend `CF-Access-Client-Id/Secret`; `/mcp/server` bypass path); interim before `user→vault` tenancy (v1.3.9; audit C1/C2)](ADR-0062-cloudflare-access-edge-auth.md) | Accepted |

---

## Distribution & Security (ADR-0039, ADR-0047 through ADR-0057)

| # | Title | Status |
|---|-------|--------|
| **0039** | [Tauri v2 desktop shell: Vite SPA wrapped in Tauri, native window, cross-platform CI (F15, v0.6)](0039-tauri-v2-desktop-shell.md) | Accepted |
| **0047** | [Desktop runtime server URL + Connect gate (Tauri first-launch backend binding)](0047-desktop-runtime-server-url-and-connect-gate.md) | Accepted |
| **0048** | [Dark mode, command palette, UI polish, and desktop pack (v0.6 frontend + Tauri)](0048-dark-mode-command-palette-ui-polish-desktop-pack.md) | Accepted |
| **0049** | [Desktop auto-update over GitHub Releases (unified `v*` tag, minisign-verified)](0049-desktop-auto-update-github-releases.md) | Accepted |
| **0052** | [Shared Bearer token auth (`SYNAPSE_AUTH_TOKEN`): env-only credential, FastAPI middleware, CORS-safe 401 (R10-1, v1.0)](0052-auth-token-model.md) | Accepted |
| **0053** | [Runtime UI config-override layer (`app_config` key/value store; env baseline → DB override; GET/PUT/DELETE `/config/app`) (R11-2, v1.1)](ADR-0053-ui-config-overrides.md) | Accepted |
| **0054** | [Domain taxonomy (controlled vocabulary + ingest auto-tag) and dashboard stats API (`/stats/overview`, `/stats/sections`, `/ops/backfill-domains`) (F18, R12-1/R12-2, v1.2)](ADR-0054-domain-taxonomy-and-dashboard-stats.md) | Accepted |
| **0055** | [Settings IA v2: two-level nav, focused pages, domain-co-located runtime config, S14–S18 loop-bound keys (v1.2)](ADR-0055-settings-ia-v2.md) | Accepted |
| **0056** | [Bounded watcher ingest concurrency (`INGEST_MAX_CONCURRENCY`, default 3) — prevents bulk-drop flood of DB pool / embedding host / RAM (I7, v1.2)](ADR-0056-ingest-concurrency-cap.md) | Accepted |
| **0057** | [Responsive strategy for iPhone/iPad: 3 viewport tiers (767/1023), `useViewport()` hook, PanelDrawer + `uiStore`, iOS safe-area/`100dvh` (R13-11, v1.3)](ADR-0057-responsive-mobile-tablet.md) | Accepted |
| **0063** | [Ingest-quality parity: long-source chunked analysis + checkpointing, LLM body-merge on re-ingest, wrong-language page drop (orchestrated route; provider-abstracted, bounded) (F3/I6/I7, R1, v1.3.13)](ADR-0063-ingest-quality-parity.md) | Accepted |
| **0064** | [Missing-page fan-out (F9 review suggestions)](ADR-0064-missing-page-fanout.md) | Accepted |
| **0065** | [Marker large-PDF conversion via page-range chunking — split in the service, shared models, dedicated `MARKER_MAX_UPLOAD_BYTES`, raised timeout (F12/I7/I1, v1.4.1)](ADR-0065-marker-large-pdf-chunking.md) | Accepted |
| **0066** | [LLM Wiki 1:1 parity program (v1.5) — amends I9 (multi-provider web search opt-in) + Marker/MinerU posture; 6-phase scope](ADR-0066-llmwiki-1to1-parity-program.md) | Accepted |

---

## Navigation

- **Home:** [docs/index.md](../index.md)
- **User Guide:** [docs/USER.md](../USER.md)
- **Deploy Guide:** [docs/DEPLOY.md](../DEPLOY.md)
- **Architecture Diagrams:** [docs/architecture/](../architecture/index.md)
