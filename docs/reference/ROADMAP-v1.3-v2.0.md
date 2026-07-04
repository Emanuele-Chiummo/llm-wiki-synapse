# Synapse — Product Roadmap Proposal v1.3 → v2.0.0

> Produced 2026-07-04 from a full-stack audit of the **v1.2.6 release tag**:
> backend (~36.5K LOC Python, 16 tables, 23 migrations, 92 test files), frontend
> (~27.6K LOC TS/TSX, 82 vitest files + 8 Playwright specs), extension, Tauri,
> CI/CD, all docs (56 ADRs, 12 sprint cycles, TRACEABILITY/DOCS_STATUS/BACKLOG),
> plus a dedicated backend+frontend bug hunt (§0-bis).
> Companion to `ROADMAP-v0.7-v1.0.md` (marked COMPLETE). Owner review pending —
> this is a proposal. Timeline: 8 weeks / 4 sprints, evenings cadence with agents.

---

## 0. Where v1.2.6 actually stands (audit findings)

### The v1.2.4 → v1.2.6 delta (already shipped, adjusts this audit)

Nine commits sit on the release tags beyond `main`'s tip (see T2 below):
**v1.2.4** split the Settings monolith (3,987 → 433 lines; two-level IA,
`settings/sections/*.tsx` + shared `ui.tsx`, ADR-0055) and made the S14–S18
loop bounds (deep-research/lint `max_iter`/`token_budget`) UI-editable;
it also added a **production frontend image** (multi-stage Vite→nginx with API
reverse-proxy, GHCR publish job) and a **TrueNAS SCALE custom-app catalog**
(`trains/stable/synapse` — one-click Postgres+Qdrant+backend+frontend).
**v1.2.5** fixed chat over plain-HTTP LAN (secure-context-safe UUID).
**v1.2.6** bounded watcher→ingest concurrency to survive bulk file drops
without flooding/OOM (ADR-0056).

### Feature coverage — the product is functionally complete

Every feature in CLAUDE.md §4 is shipped: **K1–K8 all GREEN**, **F1–F18 all
GREEN** with three narrow residuals:

| Residual | Feature | Detail |
|---|---|---|
| Clipper project picker | F11 | `extension/popup.js` posts `{url, title, markdown}` only — no target picker. Spec'd in §4b, never built. Becomes the **vault picker** in v2 (R15-3). |
| Cancel in-flight ingest | K2/F17 | G-P2-3, the only open parity item vs llm_wiki matrix. Queue infra exists (ADR-0046); the `DELETE /ingest/{run_id}` endpoint does not. |
| Multi-vault UI | R10-2 | Explicitly deferred at v1.0. `vault_id` is plumbed through every table but a single vault is hardcoded in practice. **This is the v2 headline.** |

All 9 invariants hold and are *mechanically enforced* (ESLint bans on client
layout libs, `no-client-layout` dist scan, `chat-parse-once`, i18n key-parity
test, capability-only provider routing, bounded loops with cost ledger).
No TODO/FIXME anywhere in `app/` or `src/`. This is an unusually clean base.

### Structural debt (what a 2.0 must not carry forward)

| # | Debt | Where | Impact |
|---|---|---|---|
| T1 | `main.py` is a **9,311-line router monolith** — CLAUDE.md's target layout (routers per domain) was never realized | `backend/app/main.py` | Every feature touches one file; merge friction, review blindness; blocks multi-vault scoping cleanly |
| T2 | **Release tags ahead of `main`** — v1.2.4/5/6 were tagged from commits never merged back; `main` sits at v1.2.3+1 | git history | Contributors and CI on `main` build stale code; the OSS front door lies about the product |
| T3 | `review.py` (2,909) and `orchestrator.py` (2,780) are borderline | `backend/app/ops/`, `app/ingest/` | Acceptable, but don't let them grow through v2 |
| T4 | `ops_scheduler` state is **in-memory** — schedules reset on container restart | `backend/app/ops_scheduler.py` | Missed weekly jobs after every update (Watchtower restarts hourly-polled containers) |
| T5 | Auth is a **single shared Bearer token**, disabled by default; no rate limiting; Postgres port 5432 exposed with default `synapse/synapse` creds in compose | `app/auth.py`, `docker-compose.yml` | Fine for LAN/Tailscale; not fine for a 2.0 that people other than the author deploy |
| T6 | **No E2E in CI** — the 8 Playwright specs run only manually against a live stack; integration job is commented out | `.github/workflows/ci.yml` | Regressions in wiring (proxy, auth, streaming) reach tags |
| T7 | Images (backend, frontend since v1.2.4, marker) are **linux/amd64 only**; version bump is a 4-file manual ritual (tauri.conf + Cargo.toml + package.json + pyproject) | `desktop-release.yml`, DEPLOY §7.7 | No ARM homelab/RPi/Apple-container users; bump errors break the updater |
| T8 | Whisper service exists but is **not in compose**; Marker GPU block commented out | `tools/whisper-service/` | F12 AV path requires undocumented manual setup |
| T9 | Docs drift: ADR-0039 unindexed, ADR-0023 number skipped, stale `docs/er/schema 2.mmd`, D5 screenshots PENDING-LIVE since v1.0, BACKLOG says v1.2 "blocked" while the code shipped, `frontend/package.json` description still says "v0.5" | `docs/` | I8 (docs-as-DoD) is silently eroding |
| T10 | **Code-level bug & improvement backlog** — a dedicated v1.2.6 review found 2 P1 + 18 P2 issues (event-loop stall, cross-conversation chat contamination, SSRF, stream leaks, non-atomic index writes, hook-order violation, i18n leaks…). BUG-2 from the old sprint docs is already fixed | backend + frontend | Enumerated in §0-bis; drives R13-5..R13-9 |

### Why the next major is 2.0.0 (semver honesty)

Multi-vault changes the data model (non-null `vault_id` everywhere, vault-scoped
Qdrant), the API surface (vault scoping on every content endpoint), and the auth
model (per-vault/named tokens). That is a breaking change for API consumers
(MCP clients, the clipper, scripts) → **major version**. Everything else in this
roadmap exists to make that change safe.

### Karpathy-pattern alignment (the north star)

The origin pattern is *one purpose-driven wiki*: `purpose.md` defines the thesis,
`schema.md` the rules, the human curates, the LLM maintains (K8). Synapse v1
perfected that for **one** vault. The honest v2 reading of the pattern is not
"more AI features" — it is **many purposes, one instance**: a research vault, a
homelab vault, a reading vault, each with its own purpose/schema/provider/graph,
without deploying N containers. Multi-vault is the pattern's natural plural.
Everything AI-side (F17 routing, bounded loops, review queue, schema
co-evolution) already exceeds llm_wiki parity — v2 deliberately adds **no new
AI loop**.

---

## 0-bis. Bug & improvement hunt on v1.2.6 (code-level review)

A dedicated backend + frontend review of the released `v1.2.6` tree (every
finding traced through the full code path). None of these are release-blockers
for 1.2.6 — most need concurrency or multi-vault to trigger — but they are the
concrete worklist that feeds the v1.3 bug batch (R13-5) and shape a few v1.4
decisions. Priorities: **P1 = fix in v1.3**, **P2 = fix opportunistically or
when the enabling feature lands**.

### Backend

| # | Sev | Defect | Where | Trigger |
|---|-----|--------|-------|---------|
| B1 | **P1** | **FA2/igraph/Louvain layout runs synchronously in the async event loop** — no `run_in_executor`; the whole server stalls for the duration of a recompute | `graph/engine.py:405` via `graph/cache.py:129,198` | 1–2.5k-page vault; every 0.5s tick (or a cold-cache `GET /graph`) freezes all requests/streams/watcher for seconds |
| B2 | P2 | **SSRF** — deep-research fetches SearXNG result URLs with redirects followed and no private-IP/scheme filtering; internal responses land in the prompt *and* are persisted | `ops/deep_research.py:395` | A result (or redirect) pointing at `169.254.169.254`, `127.0.0.1:6333/5432`, etc. |
| B3 | P2 | Ingest run can be stranded `running` + a ghost queue handle never clears if context load raises **before** the finalizing `try` | `ingest/orchestrator.py:458–491`, `_load_vault_context` `:2336` | `purpose.md`/`schema.md` removed or unreadable between `exists()` and `read_text()` |
| B4 | P2 | Chat stream leaks the provider stream (open httpx connection) on timeout/error — only the token-budget branch calls `aclose()` | `chat/stream.py:222–291` | Slow provider hitting the 60s timeout repeatedly → connection-pool/FD exhaustion |
| B5 | P2 | `index.md` rebuild is non-atomic and races under concurrent ingest (last stale writer wins; readers can see a truncated file) | `wiki/index.py:97` | `INGEST_MAX_CONCURRENCY>1`, two ingests interleave |
| B6 | P2 | Same-slug collision race: two concurrent ingests producing the same title both INSERT → `IntegrityError` on the live-unique index, one run spuriously `failed` | `ingest/orchestrator.py:1333–1385` | Two sources naming the same entity dropped together |
| B7 | P2 | Wikilink enrichment matches a mention by bare substring (no word boundary) → can produce `[[Cat\|cat]]egory`, persisted to the vault | `ops/enrich_wikilinks.py:300` | LLM proposes a short mention that is a substring of a longer word |
| B8 | P2 | GraphCache stamps its freshness marker from a *post*-recompute version read → a concurrent bump makes it serve a stale snapshot as a cache HIT (self-heals via follow-up) | `graph/cache.py:129–138` | `data_version` bumped mid-recompute |
| B9 | P2 | `ImportScheduler.run_now` single-flight guard has a check-then-act gap across an `await` → concurrent scans | `import_scheduler.py:330–342` | Two near-simultaneous `run-now` calls |
| B10 | P2 | `PUT /pages/{id}/content` optimistic-lock hash check is not atomic with the write → silent last-writer-wins instead of 409 | `main.py:2969–3011` | Two simultaneous editors of one page |
| B11 | P2 | Auth exempt-list matches path regardless of HTTP method (latent, not currently exploitable) | `auth.py:98` | Future mutating route added at an exempt path |
| B12 | P2 | `cascade_delete` is multi-transaction, not crash-atomic (documented consequence, no reconciliation on restart) | `ops/cascade_delete.py:662–765` | Crash mid-delete |

### Frontend

| # | Sev | Defect | Where | Trigger |
|---|-----|--------|-------|---------|
| F1 | **P1** | **"Keep editing" ping-pongs the unsaved-changes dialog forever** — the guard re-fires on the selection it restores; only "Discard" escapes | `wiki/NoteView.tsx:362–498` | Edit a dirty page, click another tree node, click "Keep editing" |
| F2 | **P1** | **Chat answer lands in the wrong conversation** — switching conversation mid-stream doesn't abort; `finalizeTurn` appends to the now-current list | `chat/ConversationList.tsx:174`, `chatStore.ts:203`, `useChatStream.ts` | Send in A, switch to B while streaming |
| F3 | P2 | `useChatStream` never aborts on unmount despite its docstring — detached reader keeps writing to the global store | `chat/useChatStream.ts:16,31` | Leave the chat section mid-stream |
| F4 | P2 | Double-submit race: aborted stream #1's `clearStream()` wipes stream #2's live UI state | `useChatStream.ts:47–111` | Enter auto-repeat / rapid double-send |
| F5 | P2 | `ConversationList.loadConversations` closes over stale `activeId` (deps `[vaultId]`) → yanks selection to `items[0]` and reloads after every turn | `chat/ConversationList.tsx:119–172` | Any completed chat turn while on another conversation |
| F6 | P2 | Ingest polling keeps polling the **old** vault after a vault switch (no branch for vaultId-change while polling) — a v1.4 landmine | `ingest/IngestView.tsx:61` | Switch vault with an active ingest (needs multi-vault) |
| F7 | P2 | `ThinkBlock` real rules-of-hooks violation — currently defused by both callers guarding non-empty content; one careless future caller = hard crash | `chat/ThinkBlock.tsx:50–65` | `<ThinkBlock content={maybeEmpty}/>` |
| F8 | P2 | Hardcoded English strings in the graph tooltip + screen-reader announcement bypass i18n | `GraphViewer.tsx:280,299,303,319,1393` | Italian user hovering/selecting a node |

**Verified clean (no action):** sigma lifecycle (kill on unmount + re-mount), NDJSON
multi-byte/split handling, HTTP timeout signal composition, settingsStore
persistence/migration guards, extension error surfacing. BUG-2 (ingest polling
dedup on remount) **is fixed** in 1.2.6 — the remaining ingest issue is the
vault-switch case (F6), not remount.

---

## v1.3 — «Foundations» (settimane 1–2)

Theme: zero visible behavior change; make the house solid before the extension.
This is the sprint that pays T1–T10 down so multi-vault lands on clean ground.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R13-1 | **Router split** — decompose `main.py` into `app/routers/{pages,search,graph,ingest,chat,review,research,ops,config,stats,clip,mcp}.py` via `APIRouter`. Contract-frozen: the CI OpenAPI drift gate must show an **empty diff** (paths, schemas, examples identical) | BE | L | T1 | |
| R13-2 | **Release hygiene** — merge the v1.2.4–v1.2.6 release lineage back into `main`, protect `main`, and adopt the rule *tags are only cut from `main`*; document in CONTRIBUTING | DevOps | S | T2 | |
| R13-3 | **Cancel in-flight ingest** — `DELETE /ingest/{run_id}` on top of ADR-0046 queue (cancellation events exist); Activity bar wiring | BE+FE | S | G-P2-3 | |
| R13-4 | **Persistent scheduler state** — last-run timestamps for ops/import schedulers into `app_config` (survives restart; T4) | BE | S | T4 | |
| R13-5 | **P1 fix — event-loop stall (B1):** offload `GraphEngine.recompute` (igraph build + FA2 + Louvain) to a thread/process executor so recompute never blocks requests/streams/watcher. Regression-guard with the existing `graph-perf.spec.ts` | BE | M | B1 | ✅ done |
| R13-6 | **P1 fix — chat/editor UX correctness (F1, F2):** unmount/conversation-switch abort in `useChatStream` (kills the "answer in wrong conversation" race F2 and the leak F3); break the "Keep editing" dialog ping-pong in `NoteView` (F1) | FE | S/M | F1, F2, F3 | ✅ done |
| R13-7 | **Bug batch (P2s):** stream `aclose()` in `finally` (B4); atomic `index.md` write + coalesced rebuild (B5); finalize-in-`finally` for ingest runs + queue handle (B3); word-boundary mention match (B7); GraphCache marker at recompute start (B8); `run_now` sync single-flight (B9); 409 on concurrent page PUT (B10); double-submit `e.repeat` guard (F4); stale-`activeId` fix in ConversationList (F5); `ThinkBlock` hooks-order fix (F7); graph tooltip i18n (F8); PWA `lang` + package.json description drift | BE+FE | M | §0-bis | |
| R13-8 | **CI hardening** — (a) E2E job: compose up backend+postgres+fake-embeddings in CI, run the existing Playwright suite headless; (b) multi-arch images (linux/amd64+arm64 via buildx); (c) `make bump VERSION=x.y.z` single-command 4-file version bump with check | QA+DevOps | M | T6, T7 | |
| R13-9 | **Deploy security pass** — SSRF guard on deep-research fetch (block private/loopback/link-local/metadata ranges, http/https only, redirect cap — shared util with searxng.py; B2); method-aware auth exempt list (B11); drop the default `5432:5432` publish (compose-internal network), creds via `.env` with generated defaults; minimal rate limit on inference-cost endpoints (`/chat`, `/ingest/trigger`, `/research`); document Tailscale/CF-Tunnel-only posture in DEPLOY | DevOps+BE | M | T5, B2, B11 | |
| R13-10 | **Docs hygiene** — index ADR-0039, delete `schema 2.mmd`, refresh D5 screenshots via the new CI E2E job, sync BACKLOG/parity/TRACEABILITY to v1.2.6 reality, whisper-service compose profile (`av`) | Docs+DevOps | S/M | T8, T9 | |
| R13-11 | **Responsive iPhone/iPad (ADR-0057)** — 3 viewport tiers (mobile ≤767 / tablet 768–1023 / desktop ≥1024, legacy 720px unified); `useViewport()` hook + `uiStore` (absorbs PanelGroup collapse state); `PanelDrawer` for tree+preview on mobile and preview on tablet; iOS safe-area (`viewport-fit=cover`, `env(safe-area-inset-*)`) + `100dvh` shell; graph toolbar reachable on touch (mobile half of UXA-27); i18n EN/IT for new strings | FE | M/L | owner request 2026-07-04 | ✅ done |

**Exit criteria:** all tests green with an empty OpenAPI diff (R13-1 proof);
both P1s fixed (no event-loop stall on a 2k-page vault under load; no
cross-conversation contamination); the SSRF guard rejects a metadata-IP result;
E2E job green in CI; arm64 image pulls on a Pi/ARM box; no schedule lost across
a container restart; docs gate ALL-UP-TO-DATE with fresh screenshots.

> The remaining P2s that are *multi-vault landmines* (F6 ingest-polling vault
> switch; B6 same-slug collision race — sharper once vaults multiply the write
> concurrency) are fixed inside v1.4 where the vault plumbing is already open,
> not retrofitted here.

## v1.4 — «Multi-vault core» (settimane 3–4)

Theme: the breaking change, backend-first, behind a compatibility default.
One ADR before any code (solution-architect gate).

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R14-1 | **ADR-0058 Multi-vault model** — `vaults` table (id, name, slug, fs path, scenario, created); `vault_id` becomes non-null FK on all 16 tables; **auto-migration**: first `alembic upgrade` adopts the existing vault as `default` (zero data loss, idempotent) | BE | M | R10-2 | |
| R14-2 | **Vector scoping** — Qdrant: single collection + mandatory `vault_id` payload filter (decided in ADR-0058; avoids collection-per-vault ops burden on a 12 GB-VRAM homelab) | BE | S | I1 | |
| R14-3 | **Filesystem & watcher** — `vault/<slug>/{raw,wiki,schema.md,purpose.md,.obsidian}`; one watchdog observer per active vault; `default` keeps the current path for back-compat. Fold in the **same-slug collision race (B6)** — dedup the ingest write path by output slug, not just source path, now that N vaults multiply write concurrency | BE | M | I1, I5, B6 | |
| R14-4 | **API vault scoping** — `X-Synapse-Vault` header (default: `default`) resolved by middleware into request state; every router from R13-1 filters by it. v1 clients keep working unchanged against the default vault — the break is opt-in until 2.0. Fix **ingest-polling vault-switch (F6)** as part of the frontend vault-aware refactor | BE+FE | L | T1→enabler, F6 | |
| R14-5 | **Per-vault provider & costs** — `provider_config` scope=vault verified end-to-end (design already supports it); `/costs/summary` and `/stats/*` grouped per vault | BE | S/M | F17, I7 | |
| R14-6 | **Per-vault export/import** — portable zip (vault fs + JSON dump) → restore into a new vault on another instance | BE | M | v0.8 R8-4 extension | |

**Exit criteria:** two vaults live on one instance — separate graphs, separate
provider configs, separate costs; ingest/chat/search/graph/review all correctly
scoped (cross-vault leak = P0); migration from a real v1.2 database verified;
Obsidian opens each `wiki/` independently (I5 per vault).

## v1.5 — «Multi-vault UX + access» = v2.0.0-beta (settimane 5–6)

Theme: surface it, secure it, tag a beta.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R15-1 | **Vault switcher** — header dropdown + command-palette action; create-vault flow reusing scenario templates (R7-1) and the first-run wizard; per-vault purpose/schema editing | FE | M | R10-2 | |
| R15-2 | **Home per vault** — dashboard scoped to active vault + an "all vaults" overview row (counts, last activity, cost) | FE | S/M | F18 | |
| R15-3 | **Clipper project picker** — vault dropdown in the extension popup (`GET /vaults` public-ish via CLIP token), closes the last F11 gap | FE | S | F11 | |
| R15-4 | **Named tokens** — replace the single `SYNAPSE_AUTH_TOKEN` with N named tokens (label, optional vault scope, created/revoked in Settings → Security; hashed at rest like ADR-0033). Env token remains honored as a bootstrap/back-compat token. Full OIDC stays post-2.0 unless a concrete need appears | BE+FE | M/L | T5, v1.0 deferral | |
| R15-5 | **MCP + chat vault scoping** — MCP tools accept `vault` param (default = default vault); conversations belong to a vault | BE | S | ADR-0010/0029 | |
| R15-6 | **UX-audit closure batch** — remaining P2 items from `UX-AUDIT-2026-07.md` (UXA-09..13, 19–28 triaged: fix P2s, consciously close-won't-fix the rest) | FE | M | UX audit | |

**Exit criteria:** tag **v2.0.0-beta.1**; a new user can create a second vault,
clip into it, chat in it, and see its graph without touching the first vault;
token revocation works; migration guide drafted.

## v2.0.0 — «Release» (settimane 7–8)

Theme: freeze, document, ship. No new features enter this sprint.

| ID | Item | Area | Effort | Source | Status |
|----|------|------|--------|--------|--------|
| R20-1 | **Migration guide + auto-migrate** — `docs/MIGRATION-v1-v2.md`; first-boot migration battle-tested against copies of the owner's real TrueNAS data; documented rollback (pre-upgrade pg_dump per DEPLOY §12) | BE+Docs | M | R14-1 | |
| R20-2 | **D1–D7 full refresh** — C4 with vaults, ER regen, new sequences (vault-create, scoped ingest), OpenAPI 2.0.0, D5 screenshot sweep via CI, USER/DEPLOY rewritten for multi-vault | Docs | M | I8 | |
| R20-3 | **Performance regression gate** — re-run the 4 llm_wiki bottleneck guards with 2+ vaults and a large vault (graph fps, no main-thread layout, parse-once, virtualized lists); watcher/queue behavior with N observers | QA | S/M | I1–I4 | |
| R20-4 | **Release engineering** — code-signing certs purchased → signed dmg/exe (guide DEPLOY §14 ready); Chrome Web Store submission (deferred since v1.0); GHCR `:2` channel; release notes with upgrade callout | DevOps | M(€) | R10-3 | |
| R20-5 | **RC discipline** — beta → rc.1 with only P0/P1 fixes; EC-M-HCP human checkpoint on live TrueNAS; tag **v2.0.0** | All | — | DoD | |

**Exit criteria:** v2.0.0 tagged; a v1.2.x instance upgrades in place with one
`docker compose pull && up`; signed desktop builds auto-update from 1.x; store
listing submitted; docs site republished.

---

## Cross-cutting (every sprint)

- i18n parity EN/IT enforced by test; invariants I1–I9; per-sprint DoD
  (tests + architect review + docs gate + human checkpoint EC-Mx-HCP).
- Every schema change ships its Alembic migration **and** `make er` in the same PR.
- No new bounded loop without `max_iter` + `token_budget` + cost row (I7).
- `main.py`, `review.py` and `orchestrator.py` line counts are ratcheted
  **down** — CI fails if they grow past their post-R13 size (Settings already
  paid this debt in v1.2.4).

## Sequencing rationale

The refactor (v1.3) comes first because vault scoping touches every endpoint:
doing it inside a 9,311-line file would be the most expensive possible order.
The data-model break (v1.4) lands mid-roadmap with a compatibility default so
the owner's daily instance never stops working. UI and access (v1.5) ride on a
stable core and produce the beta. The last two weeks are deliberately
feature-frozen — v1.0 taught that distribution work (signing, store, migration
docs) always takes longer than it looks. If multi-vault slips, the fallback is
honest: ship v1.3 as-is (it is releasable alone), move one sprint right, and cut
2.0 with signing/store as the only casualties.

## Explicitly out of scope for 2.0 (post-2.0 candidates)

- Full OIDC/SSO and true multi-user RBAC (named tokens cover the homelab reality).
- New AI capabilities (agentic maintenance daemon, auto-merge of duplicate pages,
  cross-vault retrieval) — the pattern says human curates; keep it that way.
- Mobile-native apps; Kubernetes/Helm packaging; plugin system.
- Real-time collaborative editing (single-writer model is a feature, not a gap).
