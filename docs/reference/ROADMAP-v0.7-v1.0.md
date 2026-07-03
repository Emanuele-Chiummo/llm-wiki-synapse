# Synapse — Product Roadmap Proposal v0.7 → v1.0

> Produced 2026-07-03 from a full-stack audit: frontend UI inventory (11 sections,
> 15 UX gaps), backend API/capability inventory (27K LOC, 48 ADRs, 14 tables), and a
> gap analysis against `SYNAPSE-VS-LLMWIKI-PARITY.md`, CLAUDE.md §4 (K1–K8/F1–F17)
> and deferred-work notes across ADRs. Owner review pending — this is a proposal.

## 0. Where v0.6 actually stands (audit corrections)

- The parity doc's Phase-0 blockers are **already closed** (stale doc):
  Save-to-Wiki (`POST /chat/save-to-wiki`, commit edb35c6), Louvain communities
  (GET /graph `community_id` + palette/legend), provider-gate empty state (aab417c).
  → action item R7-13 refreshes the parity doc.
- Review "Create" is **implemented** (`_run_generation` → `run_orchestrated_loop`,
  review.py:400); remaining `NotImplementedError` mentions are defensive legacy.
- Shipped in v0.6 beyond plan: Tauri desktop shell + Connect gate (ADR-0047),
  brand identity, dark mode + command palette + polish + desktop pack (ADR-0048,
  in flight), ingest queue with cancel/pause/retry (ADR-0046), ServiceNow
  Marker connector (acquisition+split, `tools/marker-converter/`).

**Structural facts that shape the roadmap:** single-vault in practice (vault_id
plumbed everywhere, no auth layer); cost tracked per run/message but never
aggregated; no export/backup endpoint; image/AV ingest are placeholders;
desktop builds unsigned (no auto-update possible until signing).

---

## v0.7 — «Core completeness & daily UX» (2 settimane) ✅ SHIPPED 2026-07-03

Theme: close every seam a daily user hits; make the wiki feel finished.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R7-1 | **Scenario templates** — 5 vault presets (Research/Reading/PersonalGrowth/Business/General) pre-populating purpose.md + schema.md | FE+BE | M | F1, G-P1-4 | ✅ |
| R7-2 | **New page from UI** — "+ Nuova pagina" in the wiki tree (title/type/dir dialog → existing write path) | FE | S | UX gap #9 | ✅ |
| R7-3 | **Rename conversations** + conversation search/filter in sidebar | FE+BE | S | UX gaps #1, #15 | ✅ |
| R7-4 | **Unsaved-changes indicator** in editor + navigation guard | FE | S | UX gap #2 | ✅ |
| R7-5 | **Review search_queries** — JSONB column populated at proposal time, handed to deep-research on that action | BE+AI | M | F9, G-P1-6 | ✅ |
| R7-6 | **Recursive folder import + folderContext hint** injected into analysis prompt | BE+AI | M | F3, G-P1-9 | ✅ |
| R7-7 | **ServiceNow scheduler** — wire the Marker connector into import-schedule (periodic convert→drop→ingest); auto-download from docs.servicenow.com behind a config | BE | M/L | connector README step 3 | ✅ |
| R7-8 | **Retrieval scope decision** — citations from wiki/ only (exclude raw/ from /search assembly) — decide + implement | BE | S | G-P1-10 | ✅ |
| R7-9 | **ThinkBlock streaming preview** — rolling last-lines fade during stream | FE | S | F7, G-P1-11 | ✅ |
| R7-10 | Verifications batch: multi-provider reasoning field routing (DeepSeek/Qwen), deep-research synthesis landing in wiki/queries/, language directive in API/Local providers | AI | S | G-P1-8/12/13 | ✅ |
| R7-11 | **Bulk ops on sources** — multi-select ingest/delete; upload progress per file | FE | M | UX gaps #4, #14 | ✅ |
| R7-12 | **Cancel-all confirmation** dialog in activity bar | FE | S | UX gap #12 | ✅ |
| R7-13 | Refresh SYNAPSE-VS-LLMWIKI-PARITY.md (close stale P0/P1 rows) | Docs | S | audit | ✅ |

## v0.8 — «Content power» (2 settimane) ✅ SHIPPED 2026-07-03

Theme: ingest anything, at quality — leverage what already exists in-house.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R8-1 | **Marker as first-class PDF extractor** — promote tools/marker-converter engine to an optional high-quality PDF path with pypdf fallback (replaces the MinerU idea: Marker is already proven in-repo) | BE | M | G-P2-5 reframed | ✅ (ADR-0051) |
| R8-2 | **Vision captions for images** — provider.chat() caption on png/jpg, cached by SHA256, indexed | AI+BE | L | F12, G-P2-1 | ✅ |
| R8-3 | **Audio/video transcription** — local whisper (MPS/RTX) behind provider abstraction; opt-in | AI+BE | L | extract.py M6 note | ✅ |
| R8-4 | **Vault export/backup** — zip of vault/ + JSON dump (pages/links/edges/runs); restore doc | BE | M | audit gap | ✅ (DEPLOY.md §14) |
| R8-5 | **Search filters & sort** — type/date facets on /search + UI | FE+BE | M | UX gap #6 | ✅ |
| R8-6 | **Citation click-through everywhere** — wire onCitationClick in all MarkdownView contexts | FE | S | UX gap #10 | ✅ |
| R8-7 | Chrome clipper release — verify extension packaging, publish flow (store or unpacked doc) | FE/DevOps | M | F11 verify | ✅ (in releases) |

## v0.9 — «Trust & observability» (2 settimane) ✅ SHIPPED 2026-07-03

Theme: see what the AI does and what it costs; let the wiki improve itself.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R9-1 | **Cost dashboard** — aggregation endpoint (per provider/operation/day) + Settings section with monthly rollup and threshold alert (I7 surfacing) | BE+FE | M | audit gap | ✅ (`GET /costs/summary`, Settings > Costi, `COST_ALERT_THRESHOLD_USD`) |
| R9-2 | **Metrics/health endpoint** — watcher/scheduler/queue liveness, last-error surfacing | BE | S | audit gap | ✅ (`GET /health/detailed`, per-component status, replaces basic `/status` for monitoring) |
| R9-3 | **purpose.md suggestions** — `purpose-suggestion` ReviewItem on scope drift | AI+BE | M | F2, G-P2-2 | ✅ (ReviewItem type, `PURPOSE_SUGGESTION_*` env vars, closed G-P2-2) |
| R9-4 | **schema.md co-evolution** — `schema-suggestion` ReviewItem type (Karpathy K6 principle, beyond llm_wiki) | AI+BE | L | G-P2-4 | ✅ (default off — `SCHEMA_SUGGESTION_ENABLED=false`, `SCHEMA_SUGGESTION_*` env vars, closed G-P2-4) |
| R9-5 | **Graph drill-down** — community details panel (member list), edge weight tooltip with signal breakdown, cohesion score + low-cohesion warning | FE+BE | M | G-P2-7/8, UX gaps #3, #11 | ✅ (`GET /graph/communities/{id}`, `GET /graph/edges/{s}/{t}`, `GRAPH_COHESION_WARN`, closed G-P2-7 + G-P2-8) |
| R9-6 | **Playwright E2E suite** — happy-path per section + D5 screenshot refresh automation | QA | M | I8 | ✅ (`npm run e2e:v09`, D5 screenshots auto-captured) |
| R9-7 | **Conversation auto-titles + list previews** | FE+BE | S | UXB-1 | ✅ (50-char auto-title from first message, preview line in conversation list) |
| R9-8 | **11 UX audit fixes + button design-system** | FE | M | UX-AUDIT-2026-07 W0 | ✅ (`components.css`, all W0/UXB-1/UXB-2 items resolved) |
| R9-9 | **SectionErrorBoundary** — isolate section crashes, show Retry instead of blank screen | FE | S | reliability | ✅ |
| R9-10 | **Dynamic version in UI** — version from backend runtime, not build constant | FE+BE | S | polish | ✅ (Settings > About) |

## v1.0 — «Distribution & multi-user» (3–4 settimane)

Theme: from personal tool to shippable product.

| ID | Item | Area | Effort | Source |
|----|------|------|--------|--------|
| R10-1 | **Authentication layer** — token/OIDC login, request-scoped vault routing (unlocks multi-vault/multi-user; foundational, design ADR first) | BE+FE | XL | audit: no auth |
| R10-2 | **Multi-vault UI** — vault switcher, per-vault provider config surfaced | FE+BE | L | vault_id plumbed |
| R10-3 | **Code signing + notarization** (Apple Developer + Windows cert) | DevOps | M€ | ADR-0047 deferred |
| R10-4 | **Desktop auto-update** — tauri-plugin-updater against GitHub releases (requires R10-3) | FE/DevOps | M | ADR-0039 v0.7+ note |
| R10-5 | **Mobile/PWA polish** — real breakpoints, touch graph gestures | FE | M | UX audit |
| R10-6 | **MkDocs Material docs site** — publish D1–D7 | Docs | M | CLAUDE.md v0.6 optional |

## Cross-cutting (every release)

- Keep i18n parity (EN/IT), invariants I1–I9, per-sprint DoD (tests + architect
  review + docs gate + human checkpoint).
- Performance guard-rails: no regression on the 4 llm_wiki bottlenecks.
- Cost logging on every new bounded loop (I7).

## Suggested sequencing rationale

v0.7 front-loads small, high-frequency UX wins (they compound daily) plus the
ServiceNow scheduler you already asked for. v0.8 exploits the Marker investment.
v0.9 makes AI activity legible before opening the product up. v1.0's auth is the
only structural change — done last, with its own ADR, when everything else is calm.
