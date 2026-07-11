# Changelog

All notable changes to Synapse are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Full, per-release notes live under [`docs/release-notes/`](docs/release-notes/) and on
the [GitHub Releases](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases) page.

## [Unreleased]

## [1.5.0] — 2026-07-11 — "LLM Wiki 1:1 parity"

Brings Synapse's generated output and UX to 1:1 parity with the LLM Wiki gold vault (same corpus,
same Haiku 4.5). See `docs/adr/ADR-0067` and `docs/reference/AUDIT-SYNAPSE-VS-LLMWIKI-1TO1-2026-07-10.md`.

### Added
- **Corpus-level synthesis/comparison generator** (`ops/synthesize.py`, `POST /ops/synthesize`) — a
  bounded graph-clustered pass authors cross-cutting synthesis and side-by-side comparison pages
  after import; the ingest-time prohibition on generating them stays intact [F4, ADR-0067 D3].
- **Contradiction → open-question** — an applied contradiction lint finding authors a genuine `query`
  page (Question / Hypothesis / Open Points / Impact / References) with real sources [F9, K2, K4].
- **Vault-maintenance ops** (dry-run by default): `migrate_lint_query_stubs`, `reconcile_folders`
  (folder = type), `dedup_entities` (alias merge via Review), `backfill_related` (adds `related:` and
  converts `[[Title]]` → `[[slug|Title]]` in place) [F13, K6].
- **`related:` frontmatter** (by slug) on generated pages, seeding the 4-signal graph [F4, ADR-0067 D2].
- **Home additions** (all additive — nothing removed): wiki-thesis hero, quick actions, inline review
  preview, open-questions, and a data-quality nudge; and **Sfoglia** now filters the Wiki tree to a
  domain/group, with the overview always kept visible [F18].

### Changed
- **Frontmatter mirrors LLM Wiki** — `type`-first key order, `related:` by slug; `sources`/`lang` kept
  in Postgres (graph source-overlap ×4 and cascade-delete intact) but no longer written to the `.md`
  [F3, ADR-0067 D2].
- **Overview** gains a keyword tag-cloud, a bolded thesis lead, and an `## Open Questions` block that
  lists the live query pages [F3, ADR-0067 D6].
- **Entity canonicalization at ingest** — `AWS` / `Amazon Web Services (AWS)` merge to one page (exact
  normalized key; fuzzy variants routed to Review) [F3, ADR-0067 D5].
- **Chat "save to wiki"** files analytical answers to `synthesis/` (not `query`), graph-connects them,
  and citations reference page paths [F5, F6].
- **Frontend code-split** — heavy views (graph, editor, chat) lazy-load; initial JS bundle
  ~2010 kB → ~332 kB (−83%); the graph revisit reuses the cached store instead of refetching [I2, I3].

### Fixed
- **Wiki Lint no longer manufactures `type:query` stub pages** for missing wikilinks — they route to
  entity/concept (or the Review queue), reserving `queries/` for genuine open questions
  [K2, ADR-0067 D1].
- **Index** — `## Queries` heading (was `## Querys`), the `## Uncategorised` ghost section removed,
  and duplicate titles collapsed [K3].
- **CLI-delegated ingest** now runs the wikilink-enrichment post-pass, so delegated pages are no
  longer graph-sparse [F4].

## [1.4.1] — 2026-07-10 — "Large PDFs & graph counts"

### Added
- **Large-PDF Marker conversion via page-range chunking** — the Marker microservice now splits a
  PDF larger than `--pages-per-chunk` pages (default 25) into page-range sub-PDFs, converts them
  one at a time with a single shared model set, and concatenates the markdown. This bounds peak
  VRAM to *models + one chunk*, so a ~190 MB / several-hundred-page ServiceNow export converts on a
  12 GB GPU without OOM. Small PDFs keep the identical whole-file path; any split error falls back
  to whole-file. Response gains an additive `chunks` field [F12, ADR-0065].
- **Dedicated `MARKER_MAX_UPLOAD_BYTES` (default 300 MB)** for `POST /ingest/convert-marker`,
  separate from the 25 MB generic upload cap so large PDFs are accepted only where they can be
  chunked. Marker service `--max-upload-mb` default raised 50 → 300 [F12, ADR-0065].

### Changed
- **`MARKER_TIMEOUT_SECONDS` default 120 → 1800 s** — a chunked conversion runs all chunks inside
  one HTTP request, so the timeout must cover the whole job (a ceiling, not a fixed wait) [I7].

### Fixed
- **Graph "hidden" chip no longer shows a phantom count.** `total_nodes` (the denominator behind
  the pages/hidden pills) counted raw-source tracking rows and `query` pages that the graph engine
  deliberately excludes as nodes, so a source-heavy vault showed e.g. "233 hidden" that no UI
  filter could clear. `total_nodes` now applies the engine's exact node-eligibility rule
  (exclude `raw/*` + hidden page types, NULL-safe), so with no filters active the hidden count is
  0 — matching nashsu/llm_wiki [F4].

### Notes
- Uploads through a reverse proxy / Cloudflare Tunnel may hit a lower request-body cap (~100 MB on
  CF) regardless of `MARKER_MAX_UPLOAD_BYTES` — import very large PDFs over the LAN / Tailscale.

## [1.4.0] — 2026-07-10 — "UI parity & secrets"

### Added
- **Provider vendor catalog** — one row per vendor for 15 known providers (Anthropic, Claude
  Code CLI, Codex CLI, OpenAI, Gemini, Azure, DeepSeek, Atlas, Groq, xAI, NVIDIA NIM, Kimi ×3,
  Ollama), each with a toggle, model presets, context-window and reasoning controls, and
  connection/function tests — matching the LLM Wiki "LLM Models" UX [F17].
- **Encrypted API key storage** — keys entered in the UI are encrypted at rest
  (Fernet/AES-128-CBC+HMAC, master key from `SYNAPSE_SECRET_KEY`); responses expose only
  `api_key_configured` + `api_key_masked`, never plaintext [F17].
- **CLI auth co-located in its provider** — the Claude Code CLI subscription OAuth token
  config now lives inside the Claude Code CLI vendor row; the Codex CLI row shows an inline
  auth note (`codex login` / `OPENAI_API_KEY`) instead of a separate section [F17].
- **Provider connectivity & function tests** — `POST /provider/test/{connection,function}`,
  bounded and never echoing the key [F17].
- **Async import UX** — the Marker convert panel shows a progress bar (N of M, %) + ETA +
  per-file status, a persisted conversion history with an "Open in Sources" button, and a
  fixed drag-and-drop zone [F12].
- **macOS menu-bar (system tray) icon** — a Synapse status-bar icon with "Apri Synapse" /
  "Esci" and click-to-show, present while the app runs or is minimized [F15].
- **In-app Changelog** — a Settings → Changelog section rendering this file as expandable
  per-version cards (10 most recent) [F16].
- Visual divergence audit vs LLM Wiki v0.6.0 (`docs/reference/V14-DIVERGENCE-AUDIT.md`) [F16].

### Changed
- **Marker convert is now asynchronous** — `POST /ingest/convert-marker` returns
  `202 {batch_id, queued, total}` immediately and runs a serial background batch (status via
  `GET /ingest/convert-marker/status`), eliminating Cloudflare 524 timeouts on large PDFs [F12].
- Provider settings rebuilt from the add-a-provider-config form to the vendor-catalog UX;
  per-provider `reasoning_effort`, falling back to the env key when no stored key is set [F17].

### Fixed
- ConvertPanel drag-and-drop — the drop event now fires (`onDragEnter` preventDefault) [F12].
- Vendor catalog stuck on "loading" — removed an effect-dependency abort loop in the fetch [F17].

### Security
- **All sensitive DB secrets encrypted at rest** — `cli_oauth_token` (the `sk-ant-oat`
  subscription token) was stored in plaintext and is now Fernet-encrypted (migration 0027);
  provider API keys encrypted (0026); `clip_access_token` confirmed already PBKDF2-hashed; no
  plaintext secret remains. Removed the now-obsolete "stored in plaintext" caveat; test
  fixtures no longer hardcode secret-shaped literals [F17].

## [1.3.16] — 2026-07-09

### Fixed
- **Marker font-dir permission**: the Marker image now pre-creates and `chown`s
  `<site-packages>/static/fonts` to the non-root `marker` user (UID 1000) at build time.
  `marker.util.download_font()` writes there on first run but cannot write under
  `site-packages` as a non-root user, producing a `PermissionError [Errno 13]` that
  surfaced after the cu128 GPU fix in 1.3.15 let conversion actually start [F12].

## [1.3.15] — 2026-07-09

### Fixed
- **Marker GPU support on RTX 3060**: pin `torch==2.7.1` + `torchvision==0.22.1` from
  the PyTorch `cu128` index before `marker-pdf` in the Dockerfile. The default PyPI torch
  targets a CUDA version newer than the TrueNAS host driver supports (cap: CUDA 12.8),
  causing `TORCH_DEVICE=cuda` to fail at runtime with "NVIDIA driver on your system is
  too old" (HTTP 500 from `/convert`) [F12].

## [1.3.14] — 2026-07-09

### Added
- **Review queue per-type icons**: each card now leads with a coloured Lucide icon
  (missing-page = purple, suggestion = green, duplicate = teal) instead of a text pill;
  aligned to llm_wiki 0.6.0 [F9].
- **Review queue Approve action**: one-click "Approve" resolves a confirmation item
  without creating a page; contradiction items retain "Create" so a resolution page can
  be authored [F9].
- **Review queue dismiss-X**: dismiss moved to a top-right **X** icon (llm_wiki parity);
  Skip remains a text button [F9].
- **Deep Research side panel on the Review page**: persistent right panel (topic input +
  run list) reusing `researchStore`; the rail "Deep Research" section is kept as a
  superset [F9].
- **Graph toolbar labels**: Filter / Reset / Type / Community / Insights buttons now show
  text labels alongside icons [F4].
- **Graph community drill-down**: community legend rows are now clickable buttons wired to
  `CommunityPanel` — the drill-down was non-functional in 1.3.13 [F4].
- **Graph index/log legend**: `index.md` (#fbbf24 amber) and `log.md` (#a78bfa violet) get
  dedicated legend rows and colours, separated from the catch-all "other" bucket [F4].
- **Graph Filters panel (full)**: hide index/overview/log nodes, hide isolated nodes,
  min/max link-count sliders, node-size and spacing sliders (I2-safe: spacing scales
  pre-computed server coordinates, no client re-layout), node-type checkboxes with
  counts, "shown/total" summary; Reset clears all [F4].
- **Graph Insights panel opens expanded** by default [F4].
- **Descriptive LLM-generated overview title** (llm_wiki parity): the overview `title`
  now reflects the vault's domain/thesis and current period (`YYYY-MM`) instead of the
  static "Overview" label; degrade-safe fallback to `settings.overview_title` for vaults
  with no H1 in the generated body [F3].

### Fixed
- **Marker `/convert` 422 on every real multipart upload**: `from __future__ import
  annotations` in the Marker microservice stringified FastAPI endpoint annotations; FastAPI's
  `get_type_hints()` could not resolve the function-local `UploadFile` name and
  misclassified `file` as a query parameter, rejecting every multipart request before it
  reached Marker. Removing the future import restores eager annotation evaluation [F12].
- **Graph Filters panel temporal-dead-zone crash**: Zustand store selectors are now
  hoisted above the refs that capture them, fixing a startup crash in `GraphViewer`
  caught in live preview (not surfaced by tsc / eslint / vitest, which do not
  full-render `GraphViewer`) [F4].

### CI
- i18n keys added (EN/IT): graph filter panel, Review Approve action, Deep Research
  panel, and purpose/schema item types.
- Vite dev server honours `PORT` env var; `launch.json` uses `autoPort` to avoid
  cross-session port conflicts.

## [1.3.13] — 2026-07-09

### Added
- **Ingest parity with llm_wiki 0.6.0** (ADR-0063) [F3][F12]:
  - Long-source chunking with checkpoint analysis over `ingest_long_source_char_threshold`.
  - LLM body-merge on re-ingest via `provider.chat` (`ingest_reingest_merge_enabled`).
  - Deterministic wrong-language page drop (`ingest_language_guard_enabled`).
  - Generation now receives the full source document text (budget-trimmed to
    `ingest_generation_source_char_budget`, default 24 000 chars) instead of the lossy
    Analysis JSON — the primary cause of wiki/graph divergence from llm_wiki on
    identical raw + model.
  - `GENERATE_SYSTEM` prompt gains an explicit page-type scaffold (one source-summary +
    entity + concept per file; synthesis/comparison reserved for the review queue).
  - `_ensure_source_summary` always guarantees exactly one origin source page per file
    (dedupe-guarded); synthesized source page titled `Source: <identity>` at
    `wiki/sources/<stem>.md`.
  - `index.md` / `log.md` persisted as `Page` rows (types `index`/`log`) so they appear
    as graph nodes; excluded from the index.md catalogue.
  - Page-type generation scaffold narrows JSON type union to `entity|concept|source`;
    `ANALYZE_SYSTEM` gains a conservatism clause.
  - `files` multipart field name corrected on the convert client (was `files[]`).
- **Graph parity with llm_wiki 0.6.0** [F4]:
  - Edges from resolved wikilinks only; shared-source contributes to weight, not topology
    (ADR-0016 amended).
  - FA2 layout parameters matched to the reference: `outboundAttractionDistribution=False`,
    `jitterTolerance=1+ln(n)` (slowDown equivalent), `barnesHutTheta=0.5`, outlier clamp
    removed (ADR-0045 amended).
  - Node type palette → Tailwind-400: entity #60a5fa · concept #c084fc · source #fb923c ·
    synthesis #f87171 · comparison #2dd4bf · query #4ade80 · overview #facc15 · other slate-400.
  - Edges → neutral slate ramp (`weight→slate-500`); hover highlight → cyan `#38bdf8` (dark)
    / slate-800 (light).
  - Graph page chrome unified with llm_wiki: single top toolbar (Network · stat pills ·
    icon buttons), zoom cluster top-right, compact Node Types legend with per-type counts.
  - Query-type nodes excluded from graph generation.
  - Hover label parity: only the hovered node forces a label; neighbours highlight
    (z-index + deepened colour) without flooding with labels.
  - `hideLabelsOnMove` / `hideEdgesOnMove` enabled for lighter rendering on large graphs.
- **Lint parity with llm_wiki 0.6.0** (ADR-0058 §7) [K2]:
  - New deterministic `no-outlinks` (info severity) and semantic `suggestion` categories.
  - Fuzzy token-overlap suggestions for orphan (source) and no-outlinks (target).
  - Auto-apply paths: append `[[link]]` under `## Related` for no-outlinks/orphan; create
    a `type:query` stub for broken-wikilink without a resolved target.
  - `overview.md` now eligible for orphan/no-outlinks checks (only index/log excluded).
  - Client-side chunking of batch lint actions to respect the 200-id server cap.
  - Missing-xref removed from the semantic prompt (llm_wiki has 4 semantic types, not 5).
- **Review queue multi-page fan-out** (ADR-0064) [F9]: a missing-page item whose title
  encodes a list (comma, CJK comma, "and"/"e" word-boundary guarded) now creates one page
  per candidate on "Create", capped at 5 (I7).
- **iOS neural visual refresh** [F15][F16]: indigo→violet gradient (light/dark-aware) for
  wordmark, primary CTAs, hero stat card, user chat bubble, and send button; per-type SF
  Symbol glyphs in every list row; expressive stat cards with icon chips and tabular
  numerals; pulsing gradient on the "Da rivedere" card when count > 0; `NeuralMotif`
  constellation behind the Wiki hero header; `AuroraBackground` (drifting blurred
  type-colour blobs, honours Reduce Motion) behind the knowledge graph; soft elevation on
  Wiki hero cards.

### Fixed
- **iOS chat streaming**: the iOS NDJSON parser expected `{token, done, error}` keys but
  the backend emits `{"type","delta"}` events; `SynapseClient` stream decoding rewritten
  to match — tokens and citations now render [F6].
- **iOS sub-page header alignment**: `BackHeader`'s `VStack` pinned to full width and
  left-aligned, fixing the offset appearance on Settings, Deep Research, Review, and
  Ingest screens [F1].
- **Lint orphan under-report**: inbound-link count was including links from `index.md`,
  `log.md`, and other vaults, making almost nothing appear orphaned. Inbound set now
  restricted to this vault's `wiki/%` content pages, excluding index/log [K2].
- **Review duplicate-sweep rule inverted**: auto-resolved duplicates when the page
  existed (should resolve only when an affected page is gone, llm_wiki parity). Missing-page
  now resolves when the page exists (by slug); `_normalize_title` strips
  "Missing page:"/"Duplicate page:" prefixes [F9].

## [1.3.12] — 2026-07-08

### Changed
- **Graph: batch coordinate/edge persistence** (`executemany`): `_persist_results` no
  longer performs one round-trip per node/arc — two `executemany` calls replace thousands
  of sequential round-trips on large vaults (I2) [F4].
- **RAG: non-blocking passage read**: source-file reads in Phase-4 assembly dispatched via
  `asyncio.to_thread`, unblocking concurrent chat requests (I3) [F5].
- **Embeddings: persistent HTTP client**: `httpx` connection pool reused across calls
  instead of a new TCP/TLS handshake on every embedding request; pool closed on shutdown [F5].
- **Stats: no double scan**: `/stats/sections` no longer re-scans the `pages` table a
  second time to resolve titles [F18].

### Fixed
- **Deep Research: in-loop provider timeout**: the three in-loop provider calls (query,
  sufficiency, synthesis) now use `asyncio.wait_for`; a stalled provider can no longer
  leave a run stuck in `running` indefinitely (I7) [F10].
- **Ops: vault-scoped orphan detection and cascade slug-match**: orphan detection and
  cascade-delete slug matching now filter by `vault_id` for correctness in multi-vault
  scenarios [F13][K2].
- **Provider: cost-gate warning when price map unset**: if `PROVIDER_PRICE_MAP` is not
  configured on the paid-provider path, a one-time WARNING is now emitted instead of
  silently logging `total_cost_usd=0` (which disabled cost-anomaly detection) (I7) [F17].
- **Chat: stabilized `MessageRow` memo**: `MessageRow` no longer receives the entire
  messages array as a prop; per-turn cost telemetry demoted to `console.info` in dev only,
  so `memo` no longer re-renders on every streaming token (I3) [F6].

### Security
- **Auth-posture warning at startup**: if `SYNAPSE_AUTH_TOKEN` is empty the API is
  effectively open; a `WARNING` in the startup log makes this visible without blocking
  boot.
- **Rate-limit proxy-aware keying**: the rate limiter now uses the same trusted-proxy
  resolver as the source classification (extracted to `app/client_ip.py`); behind
  Cloudflare/Tailscale (with `MCP_TRUSTED_PROXIES`) it counts per real client IP
  instead of collapsing all traffic into a single global bucket.
- **Response hardening headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options:
  SAMEORIGIN`, and `Referrer-Policy` on every response; HSTS only when
  `x-forwarded-proto: https` (does not break plain-HTTP local installs).

## [1.3.11] — 2026-07-07

### Added
- **KPI grid aligned, unified type palette**: `entity/concept/source/…` share the same
  colour on Home mini-bars, wiki badges, and the graph [F4][F18].
- **Sparklines under "Monthly Spend"**: 30-day cost chart from real data [F18].
- **Card elevation and skeleton loader**: subtle shadow on dashboard cards; skeleton screen
  during Home load [F18].
- **Graph selection ring**: clicked node keeps a persistent selection ring (not only on
  hover) [F4].

### Changed
- **Accessibility (WCAG 2.2)**: secondary text contrast raised to ≥ 4.5:1 (AA) in both
  light and dark; chat retrieval-mode selector converted to a proper `radiogroup` with a
  single selection; Review/Lint KPI buttons now have visible focus rings and are keyboard-
  navigable; CLI token field gains a show/hide toggle [F1][F6][F9].
- **"Ricerca" → "Ricerca profonda"** (Deep Research): resolves the collision with "Cerca"
  (search); rail label wraps to two lines; tooltip shows the full name [F10].
- **Quick search in Settings**: filters across all 18 settings pages in real time [F16].
- **User guide rewritten** (`docs/USER.md`): aligned to 1.3.x implementations; new section
  on external access (Tailscale, Cloudflare Tunnel, Cloudflare Access, service tokens) [F16].

### Fixed
- **Graph ghost labels**: on camera entry, community centroid labels are now hidden until
  `project()` positions them; de-overlap applied to nearby labels — the top-left label
  pile-up is gone [F4].
- **Home "Recent activity" blank rows**: pages without a title no longer render as a bare
  icon; fallback italic "*Senza titolo*" shown instead [F18].
- **Page preview dev placeholder**: "demo node (Phase 3)" removed; replaced with a clean
  empty-state prompt [F1].
- **Sources: central filename truncation**: long filenames truncated in the middle
  (`01_Stra…report.md`) so prefix and extension remain visible; date moved to tooltip [F12].
- **Rail label clipping**: "Strumenti" rail label no longer truncates to "TRUMENT" [F1].

See [`docs/release-notes/v1.3.11.md`](docs/release-notes/v1.3.11.md) for the full notes.

## [1.3.10] — 2026-07-07

### Added
- **Desktop app works behind Cloudflare Access**: Tauri desktop calls the backend via the
  native HTTP client (`tauri-plugin-http`) instead of the browser `fetch`, eliminating
  the CORS preflight that Cloudflare Access rejected (403). The web/PWA path is unchanged
  (lazy import) [F15][F17].
- **CLI provider streaming — token-per-token**: the CLI (claude-agent-sdk) provider now
  enables `include_partial_messages` and emits `text_delta` increments, so the chat
  response streams character-by-character via subscription (no API key required). Degrades
  gracefully to full-response delivery if the SDK version does not support partial messages
  [F17].

See [`docs/release-notes/v1.3.10.md`](docs/release-notes/v1.3.10.md) for the full notes.

## [1.3.9] — 2026-07-07

### Added
- **Settings reorganised into 5 intent groups** (Essentials · Content & Sources · AI
  Behaviour · Access & Security · System), each with a descriptive tagline; advanced items
  are badged; default landing page is "AI & provider" (ADR reorganisation) [F16].
- **CLI provider setup unified**: subscription token is now configured on the same page as
  the provider selector with an inline guide; previously it lived in a separate tab [F17].

### Fixed
- **502 on "Create" in the Review queue (and Lint generation) with the CLI provider**: the
  on-demand generation path forced the orchestrated loop (`analyze()`), which is invalid
  for agentic providers. Routing is now capability-aware: `supports_agentic_loop=True` →
  delegated path; otherwise → orchestrated loop (I6) [F9][F17].

### Security
- **Cloudflare Access gate** (ADR-0062): the entire app is now behind Cloudflare Access.
  Browser clients authenticate via the interactive login (cookie/OTP); non-browser clients
  (desktop, iOS, Chrome clipper, MCP) pass the gate with a CF Service Token
  (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) configurable in Settings [F15][F16].
- **Security response headers** (nginx): HSTS, `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`, `Referrer-Policy`,
  `Permissions-Policy` [audit C3].
- **PWA manifest behind Access**: `crossorigin="use-credentials"` on the manifest link
  prevents it from being diverted to the Cloudflare login page (CORS error).

See [`docs/release-notes/v1.3.9.md`](docs/release-notes/v1.3.9.md) for the full notes.

## [1.3.8] — 2026-07-06

### Added
- **Native iOS app (SwiftUI, iOS 17+)** in `ios/` [F15]: connects to the Synapse backend
  via REST. Five tabs — Wiki (vault stats, page list, detail + Markdown body + mini-graph),
  Search (4-phase RAG with type filters), Chat (NDJSON streaming with tappable citations),
  Graph (interactive pan/zoom, hub-label anti-overlap), Altro (review queue, import, deep
  research, settings). XcodeGen project (`ios/project.yml`), auto-signing, guide in
  `ios/README.md`.

See [`docs/release-notes/v1.3.8.md`](docs/release-notes/v1.3.8.md) for the full notes.

## [1.3.7] — 2026-07-06

### Fixed
- **`/vault/meta` routed in production**: the frontend nginx reverse-proxy
  (`nginx.conf.template`) was missing the `vault` prefix in the API-forwarding regex;
  the Vault / Meta tree section (WS-D8, added in 1.3.6) returned HTML instead of JSON
  in the shipped container image and was hidden. Added `vault` to the regex, aligned to
  `API_PREFIXES` in `vite.config.ts` [K1][I5].

## [1.3.6] — 2026-07-06

### Added
- **Real-time freshness** (Home + Graph): lightweight `dataVersion` polling via
  `GET /status` (10 s interval on Home, 5 s on Graph). Data is re-fetched only when
  `data_version` changes; the Zustand shallow-equality guard prevents re-renders on
  unchanged versions. No WebSocket, no new endpoint, no client-side layout recompute
  (I2, I3) [F4][F16][F18].
- **Ingest progress bar + ETA**: the "Active jobs" widget on Home now shows a CSS
  progress bar (`done/total × 100`) and an "ETA ~Xs" label from the existing
  `batch.eta_seconds` field on `GET /ingest/queue`. No schema change (ADR-0046
  contract preserved) [F3][F16].
- **Vault / Meta tree section**: new fixed "Vault / Meta" node at the bottom of the wiki
  tree exposes `schema.md` and `purpose.md` in the preview panel; new endpoint
  `GET /vault/meta` reads them directly from disk (no Postgres write, no Qdrant upsert, I1).
  If a file is absent (fresh install pre-bootstrap) the entry shows "Not yet generated"
  without crashing [K1][K6][I5].

### Changed
- **Review queue resolved/dismissed card states**: action buttons (Create / Skip / Ignore /
  Deep Research) removed from cards already in a resolved or dismissed state; replaced with
  a read-only badge showing the resolution type + timestamp + link to the created page
  where available [F9].
- **Note viewer single scroll**: the nested dual-`overflow: auto` layout is collapsed to a
  single scroll container; the metadata header (title, type, sources, related) scrolls
  with the body and is no longer sticky or collapsible (I5) [K1][K6].
- **Scheduled automations verified** after the v1.3.5 format changes (narrative `log.md`,
  frontmatter timestamps, full `schema.md`); no regressions detected [K2][F3][F16][F18].

### CI
- **Repaired `docs/sequences/lint-fix.mmd`**: participant `Links` renamed to `LinkTbl`
  (Mermaid keyword conflict); `;` in message labels changed to `,` (statement-separator
  conflict). Docs Gate Mermaid validation now passes.
- **Completed Node 20 → Node 24 action bumps missed in 1.3.5**: `actions/upload-artifact`
  v4→v7, `actions/setup-node` v4→v6, `actions/setup-python` v5→v6.

See [`docs/release-notes/v1.3.6.md`](docs/release-notes/v1.3.6.md) for the full notes.

## [1.3.5] — 2026-07-06

### Changed
- **`schema.md` is now the full llm_wiki contract**, not a stub: page-type→directory map,
  naming conventions, complete frontmatter (incl. `lang` + source `authors/year/url/venue`),
  index/log format, cross-referencing and contradiction-handling rules (K1 layer 3).
- **`log.md` is a narrative, day-grouped diary** (nashsu/llm_wiki parity) instead of a
  machine marker log: `## YYYY-MM-DD` headers + `- HH:MM:SSZ · <verb> · <type> · [[Title]] — path`
  bullets. Still append-only and machine-parseable (K4 preserved); one entry per ingest
  (AC-K4-1). Page deletion routes through the same `append_log` writer.

### Added
- **`created` / `updated` frontmatter** on every generated wiki page (`write_wiki_page`):
  `created` preserved across re-generation, `updated` advances each write.

### CI
- Bumped deprecated (Node 20) GitHub Action pins across all workflows: `actions/checkout`
  v4→v5, `docker/build-push-action` v5→v7, `docker/login-action` v3→v4,
  `docker/setup-buildx-action` v3→v4, `docker/setup-qemu-action` v3→v4.

See [`docs/release-notes/v1.3.5.md`](docs/release-notes/v1.3.5.md) for the full notes.

## [1.3.4] — 2026-07-06

### Added
- **Lint parity with llm_wiki** [K2]:
  - Deterministic **broken-wikilink** category from dangling entries in the `links` table
    (zero LLM cost); tolerant suggested-target resolver + "Fix" button that rewrites the
    link in-body.
  - Batch bar (Fix / Ignore / Send-to-Review on selected items), per-row "Open" action,
    bridge to the Review queue.
  - Per-severity headers with true totals (e.g. "Warnings (259)").
  - Semantic (LLM) lint toggle; Delete action for orphan pages.
- **Chat parity with llm_wiki** [F5][F6][F17]:
  - Attach image (capability-aware per provider, I6).
  - Web Search toggle (SearXNG; `[W]` citation namespace separate from `[n]`).
  - Retrieval-mode presets: Fast / Standard / Deep / Local-first (deterministic).
- **Graph header** with pages/links/hidden stats, Search in graph, Filter by type, Reset,
  fullscreen [F4].
- **Per-domain community names** (unique, e.g. "SAM · Reconciliation"): Louvain clusters
  named by dominant domain, coloured by cluster, no duplicates. Obsidian-like arc culling
  (link chip reflects culling). Collapsible legend. Insights collapsed by default.
  Centroid labels clamped to canvas [F4][F18].
- **Sources & Reader** [F12][F16]: folder import (recursive "＋ Folder") + folder delete
  (cascade bounded) + footer file count; reader shows "More" on tag overflow and an
  `updated:` metadata row.
- **Wiki tab in Sources**: browse the `wiki/` folder structure read-only with preview —
  equivalent to llm_wiki's "Files" view [F1][K7].
- **MCP expanded** from 4 to 9 tools: `graph_neighborhood`, `list_reviews`,
  `resolve_review`, `read_source_file`, `trigger_rescan`; REST `review/bulk-resolve` +
  `PATCH` [F9][F17].
- **Installable Synapse Skill** (`tools/synapse-skill/`): interrogate Synapse from Claude
  Code/Codex via an agent skill (read-only, trigger-disciplined) [F17].
- **Deep Research with LLM-optimized topic**: topic + queries optimized by the LLM (reads
  `overview.md` + `purpose.md`) and presented in an editable confirm dialog before the
  research run starts [F10].

### Fixed
- **Automations card true outcome**: the scheduled-automations card now reports the actual
  classification result (`dormant` / `error` / counts) instead of a blanket "ok" [F18][I7].

See [`docs/release-notes/v1.3.4.md`](docs/release-notes/v1.3.4.md) for the full notes.

## [1.3.3] — 2026-07-05

### Fixed
- **Deep Research is PDF-proof**: SearXNG results pointing at a PDF were stored as raw
  bytes and killed the whole run in Postgres. PDFs now go through the ingest extractor
  (Marker when configured, else pypdf), other binaries are skipped with a log, all text
  is NUL-sanitized, and a single unstorable source no longer aborts the run.
- **Chat `[n]` citations open correctly**: click-through now navigates by page UUID with
  a `GET /pages/by-slug/{slug}` fallback for historical messages (was 422).

## [1.3.2] — 2026-07-05

### Fixed
- Setup wizard: backend server URL is now an editable, validated field.
- Deep Research: zero-source runs no longer synthesize and ingest a junk page.
- Search: relevance `%` chip shown only for vector results (no more 2144% from graph
  expansion).
- Review queue: the auto-resolve button is relabeled to disambiguate it from "clear
  resolved".

## [1.3.1] — 2026-07-05

### Changed
- Multi-arch frontend image builds the Vite bundle natively (minutes, not the 4h+ QEMU
  emulation of 1.3.0).
- CI E2E job green for the first time: seeded stack + 122+ Playwright tests on every push
  to `main`; hardware-aware skips for Ollama/GPU-dependent tests.
- New `release-cut` and `release-notes-sync` workflows; release notes versioned in
  `docs/release-notes/`.

## [1.3.0] — 2026-07-05 — "Foundations"

The sprint that pays down structural debt before multi-vault (v1.4 → 2.0). No new AI
features by design. First release cut from `main` under the new tagging policy.

### Changed
- `main.py` decomposed from 9,311 → ~1,400 lines across 13 domain routers; API contract
  frozen and proven (byte-identical OpenAPI).
- Release lineage realigned: v1.2.4–1.2.6 merged into `main`; "tags are cut only from
  main" rule documented in CONTRIBUTING.

### Added
- Responsive mobile/tablet/desktop layouts (ADR-0057): drawers, safe-area insets, `100dvh`,
  and touch-reactive interactions (no ~350ms tap delay, ≥44px targets).

### Fixed
- Graph recompute (igraph/FA2/Louvain) moved to a thread executor — no more server
  freeze on large vaults.
- Chat responses bound to their originating conversation; stream aborts on switch/unmount.
- Atomic `index.md` writes, provider streams closed on timeout, word-boundary wikilinks,
  concurrent-edit `409`, and ~14 other regression-tested fixes (2 P1 + 18 P2).

### Security
- SSRF guard on deep-research fetches (http/https only, private/metadata IP blocking, max
  3 redirects).
- Per-method auth exemptions, Postgres no longer host-exposed, per-IP rate limiting on
  chat/ingest/research.

## [1.2.6] — 2026-07-04

### Fixed
- **Bulk ingest concurrency cap** (ADR-0056): dragging dozens of files into `raw/sources/`
  would launch equally many simultaneous ingest tasks — DB connection pool exhausted, GPU
  overwhelmed, host RAM spiked to crash. A semaphore (`INGEST_MAX_CONCURRENCY`, default 3)
  now processes files in an ordered queue. The *what* is unchanged; only the *when* is
  serialised (I7) [F3].

## [1.2.5] — 2026-07-04

### Added
- **TrueNAS SCALE custom-app catalog** (`trains/stable/synapse`): one-click installation
  of Postgres + Qdrant + backend + frontend from the TrueNAS Apps UI, with logo, guided
  form, and in-place update support [F16].

### Fixed
- **Chat over plain `http://` on LAN**: `crypto.randomUUID` exists only in secure contexts
  (HTTPS/localhost); replaced with a UUID generator that works outside them. Sending a
  message from `http://truenas:5173` previously failed silently [F6].
- Ruff/Black formatting on S14–S18 configuration code; ER diagram header no longer
  includes a generation date (Docs Gate no longer depends on the day) [K2][I8].

## [1.2.4] — 2026-07-04

### Added
- **Settings redesign** (ADR-0055): the 3 987-line monolithic panel is replaced by a
  two-level navigation with 16 focused pages (`settings/sections/*`); same functionality,
  much greater clarity [F16].
- **Loop-limit controls in Settings**: `max_iter` and `token_budget` for deep-research
  and lint (S14–S18) are now adjustable in-app without environment variables (I7) [F10][K2].
- **Production frontend image**: multi-stage Vite → nginx build with integrated API
  reverse-proxy published to GHCR on every release; web client is deployable with a
  single `docker compose pull` [F15][F16].

### Fixed
- **Type reclassification memory**: pages already examined are remembered across runs —
  no more double AI billing on subsequent runs, and completion is real [F18][K8].

See [`docs/release-notes/v1.2.4.md`](docs/release-notes/v1.2.4.md) for the full notes.

## [1.2.3] — 2026-07-03

### Added
- **In-app type reclassification** (4th scheduled operation): review and correct AI-assigned
  page types without leaving the UI; badge counts now exact [F18][F9].
- **Pending-review badge on the Review rail item**: always-visible count of items awaiting
  action [F9][F1].
- **Weekly schema-review operation**: a schedulable operation proposes schema-conformance
  changes via the Review queue [F18][K6][K8].

## [1.2.2] — 2026-07-03

### Added
- **Bounded page-type reclassification** per curated `schema.md`: automatically corrects
  AI-assigned types against the vault schema, bounded by `max_iter` + `token_budget`
  (I7) [F18][K8].

### Fixed
- **Home dashboard full-width**: removed the 1 100 px cap that constrained the layout on
  wide screens [F18].
- **Backfill summary crash**: a section summary serialised as an object (not a string)
  caused a React render error on the Home dashboard [F18].

## [1.2.1] — 2026-07-03

### Added
- **Periodic update checks**: the app checks for new releases every 4 hours and on
  window-focus return — the update banner appears automatically without restarting [F15].

### Fixed
- **Graph edges visible in dark mode**: edge colour ramps now follow the active theme;
  the previous dark background rendered edges invisible [F4].
- **Version banner fires only when the server is behind the app**: the comparison is now
  correct semver (server < app), not a string equality check [F15].

## [1.2.0] — 2026-07-03 — "Home & Insights"

Home dashboard and per-domain section insights (F18), in-app type reclassification, and a
run of Home/classification fixes across the 1.2.x patch line.

## [1.1.0] — 2026-07-03 — "Convert & Configure"

Multi-format conversion pipeline and in-app provider/model configuration; Chrome web
clipper 1.1.0.

## [1.0.0] — 2026-07-03 — "Distribution"

First distributed release: signed desktop bundles and auto-update from GitHub Releases.

## [0.9.0] — 2026-07-03 — "Trust & observability"

Cost/observability surfacing and trust features ahead of 1.0.

## [0.8.1] — 2026-07-03

### Fixed
- Auto-update hotfix.

## [0.8.0] — 2026-07-03 — "Content power"

Content-power features across ingest and editing.

## [0.7.0] — 2026-07-03 — "Core completeness & daily UX"

Core completeness, daily-use UX, and auto-update.

## [0.6.0] — 2026-07-03 — M6 "Shippable"

PWA + Tauri packaging, Chrome clipper, lint loop, MkDocs — milestone M6.

## [0.5.0] — 2026-06-30 — M5 "Feature parity core"

Deep Research, review queue, multi-format ingest, cascade delete — milestone M5.

## [0.4.0] — 2026-06-29 — M4 "Usable & fluid"

3-panel web UI, provider selector (F17 UI), chat streaming — milestone M4.

## [0.3.0] — M3 "Knowledge graph live"

4-signal graph, server-side FA2 layout, sigma.js viewer — milestone M3, no main-thread
freeze.

## [0.2.0] — M2 "Agentic loop + 3 providers"

`InferenceProvider` with all three backends, orchestrated ingest loop, MCP server —
milestone M2.

## [0.1.0] — M1 "Data flows end-to-end"

Walking skeleton: watcher + Postgres + Qdrant + REST — milestone M1.

[Unreleased]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.16...v1.4.0
[1.3.16]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.15...v1.3.16
[1.3.15]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.14...v1.3.15
[1.3.14]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.13...v1.3.14
[1.3.13]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.12...v1.3.13
[1.3.12]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.11...v1.3.12
[1.3.11]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.10...v1.3.11
[1.3.10]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.9...v1.3.10
[1.3.9]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.8...v1.3.9
[1.3.8]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.7...v1.3.8
[1.3.7]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.6...v1.3.7
[1.3.6]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.5...v1.3.6
[1.3.5]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.4...v1.3.5
[1.3.4]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.3...v1.3.4
[1.3.3]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.6...v1.3.0
[1.2.6]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.5...v1.2.6
[1.2.5]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.4...v1.2.5
[1.2.4]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.3...v1.2.4
[1.2.3]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.3...v0.4.0
[0.3.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.2...v0.3
[0.2.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v0.1...v0.2
[0.1.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases/tag/v0.1
