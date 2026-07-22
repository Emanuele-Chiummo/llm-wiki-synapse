# Changelog

All notable changes to Synapse are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Full, per-release notes live under [`docs/release-notes/`](docs/release-notes/) and on
the [GitHub Releases](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases) page.

## [2.1.6] — 2026-07-21 — "custom connector"

Feature release adding OAuth 2.1 + PKCE support for the remote MCP server. No breaking
changes.

### Added

- **MCP OAuth 2.1 + PKCE authorization server, for claude.ai's web "Custom connector"**
  (ADR-0090): live-observed gap — adding Synapse's remote MCP server as a claude.ai
  "Custom connector" silently failed, because that UI only speaks OAuth 2.1
  authorization_code + PKCE (unlike Claude Desktop's JSON config, which already supports
  a static bearer header). Added a minimal, single-operator-oriented authorization server:
  `GET/POST /authorize` (consent form — approved with the SAME static MCP token that
  already gates `/mcp/server`), `POST /token` (authorization_code + rotate-on-use
  refresh_token grants), `POST /register` (RFC 7591 Dynamic Client Registration, public
  clients only — PKCE is the confidentiality mechanism, no client_secret), and RFC 8414 /
  9728 discovery documents. JIT client registration handles clients (observed live with
  claude.ai) that self-assign a client_id and skip registration. An OAuth-issued access
  token is accepted by the existing MCP bearer gate exactly like the static token — no
  separate scope model, no delegation chain (an OAuth token can never approve another
  OAuth grant). Shares the same `remote_mcp_enabled` floor as `/mcp/server` itself.
  New tables `mcp_oauth_clients` / `mcp_oauth_tokens` (migration 0038).
- Updated `docs/DEPLOY.md` §5.6/§5.6b and `docs/USER.md` with setup steps for both
  Claude Desktop (bearer token) and claude.ai web (OAuth) connector paths, including the
  Cloudflare Access bypass scope needed for the new endpoints.

## [2.1.5] — 2026-07-20 — "no more argv"

Patch release fixing a live-observed ingest failure on large vaults. No schema migrations.

### Fixed

- **CLI provider ingest could fail with "Argument list too long" on large vaults**: live
  evidence — a ~150-file ingest run processed roughly 30 files cleanly, then every subsequent
  file failed the same way. Root cause: `claude-agent-sdk` passes a plain-string
  `ClaudeAgentOptions.system_prompt` directly as a `claude` CLI command-line argument, not via
  stdin. Synapse's block-loop system prompt (the default ingest path) folds in
  `schema.md`/`purpose.md`/`wiki/index.md`, and `index.md` is unbounded by design — it grows
  by one entry per page ever ingested — so as the vault (and thus the prompt) grows, the
  argument list eventually exceeds the kernel's `ARG_MAX`, and the spawned `claude` process
  fails to start (`E2BIG`) before a single message is exchanged. This explains why early files
  in a large ingest run succeed and later ones deterministically fail once the vault crosses
  the size threshold. Fixed by routing `system_prompt` through the SDK's `SystemPromptFile`
  option (a scoped temp file) at all three `CliAgentProvider` call sites (chat, delegated
  ingest, block-loop completion) instead of passing it as a raw argument.
- **`wiki/index.md`'s contribution to the block-loop prompt was uncapped**: as a complementary
  hardening (independent of the CLI-transport fix above, so it also bounds prompt size/cost on
  the API and Local provider backends), `index.md` is now capped in the analysis/generation
  system prompt the same way the review-stage prompt already caps its equivalent section.

## [2.1.4] — 2026-07-20 — "room to think"

Patch release fixing a live-observed CLI-provider non-convergence mode. No schema migrations.

### Fixed

- **CLI provider could emit zero visible text on a reasoning-heavy generation turn**: live
  evidence — a run on the CLI provider stopped non-convergent after all 3 iterations, with
  "generation produced no FILE blocks (0 parsed)" as the terminal validation error every time
  (~43k/60k tokens spent). `CliAgentProvider.complete()` runs a `max_turns=1`,
  `allowed_tools=[]` single-shot text generation but never bounded
  `ClaudeAgentOptions.max_thinking_tokens` — the installed SDK does expose this field (a
  stale code comment had claimed otherwise). A reasoning-heavy turn (a long/complex source, or
  a retry whose prompt has grown with prior validation errors) could spend the ENTIRE turn's
  budget on internal thinking and emit zero visible text; the SDK reports no error for this,
  so it was indistinguishable from a genuine clean no-op, and because each retry's prompt only
  grows (augmented with the prior "0 FILE blocks" error), the failure never self-corrected
  across iterations. Fixed by bounding `max_thinking_tokens` so headroom for the actual
  FILE-block answer always remains, regardless of `max_turns`.

## [2.1.3] — 2026-07-20 — "overview at boot"

Patch release fixing a freshly-booted default vault's Overview section being permanently
stuck at 0 in the NavTree. No schema migrations.

### Fixed

- **Boot vault never indexed overview.md/index.md/log.md**: `index_bootstrap_meta_files()`
  (added in an earlier release, NC-3) was only ever wired into `POST /projects`, so a
  newly-created project vault got its 3 meta files indexed as Page rows immediately. The
  **boot vault** (`settings.vault_root`, bootstrapped by `bootstrap_vault()` in the app's
  lifespan startup — what a single-vault install actually uses) never got the same call:
  overview.md/index.md/log.md existed on disk but had zero Page rows until an ingest
  queue-drain happened to touch overview.md (2.1.2, ADR-0089). A freshly-booted default vault
  therefore showed a permanent "OVERVIEW: 0" in the NavTree with no way to make it appear
  short of running an ingest. Fixed by calling the same indexer for the boot vault right after
  `_seed_vault_state()` in lifespan startup — idempotent and I1-compliant (targeted 3-file
  index, no vault scan).
- **E2E test-locator regression surfaced by the fix above (not a product bug)**: since
  overview.md is now indexed at boot and — by design — always sorts first in the tree (a
  singleton entry-point, deliberately excluded from the knowledge graph), 6 E2E locators
  across `csp.spec.ts`/`shell-m4-phase1.spec.ts`/`v09-happy-paths.spec.ts` that picked "the
  first page row" assuming it would be a normal graph-backed content page needed to exclude
  it explicitly.

## [2.1.2] — 2026-07-20 — "real root cause + live loose ends"

Patch release fixing the actual root cause behind non-convergent ingest runs that kept
recurring after 2.1.1, plus six smaller fixes/improvements surfaced from live usage. No
schema migrations.

### Fixed

- **Non-convergent ingest runs, actual root cause**: 2.1.1 fixed the `folderContext` hint's
  wording, but that was not the whole story — the default source-summary FILE path was
  computed via `source_filename.rsplit(".", 1)`, which strips only the file extension, not
  the directory. Since `source_filename` (the D3 "source identity") legitimately keeps the
  raw subfolder path for `sources[]` traceability, a source under
  `raw/sources/Procurement/04_Deliverables/.../Deck.md` still got a default summary path of
  `wiki/sources/Procurement/04_Deliverables/.../Deck.md` instead of the required flat
  `wiki/sources/Deck.md` (K6) — failing `validate_page_routing` identically on every retry.
  Fixed by deriving the default path via `Path(source_filename).stem`, matching the sibling
  helper `writer._source_identity_stem()`. Also added an explicit "FILE path MUST match
  frontmatter type" reminder to the generation prompt to reduce a separately observed
  compliance miss (a `type: comparison` page written under `wiki/concepts/` instead of
  `wiki/comparisons/`).
- **overview.md updates were invisible until a manual reload**: `overview.md` is regenerated
  once per ingest-queue drain (matching the original llm_wiki `onQueueDrained` behavior, per
  ADR-0078) but never bumped `data_version`, so the SSE `/events` channel never told live
  clients it had changed. Fixed (ADR-0089) — the bump fires only on a successful overwrite.
- **"Other" nav-tree bucket showed unlabeled ghost entries**: alongside the expected
  `log.md`/`index.md`, stray Page rows with both a null title and a null/unrecognized type
  (partial-ingest leftovers) were also falling into "Other". The tree now drops them.

### Added

- **Retry button for non-convergent ingest runs**: `POST /ingest/runs/{id}/retry` already
  re-injected prior validation errors into a fresh attempt, but nothing in the UI called it,
  and it only recognized true pipeline failures — `converged_false` completions were
  invisible to it. `IngestRunDetail` now has a "Retry run" action for both cases.
- **NavTree live refresh**: the wiki tree now subscribes to the existing `data_version` SSE
  signal (previously only refetched on mount or after an explicit page-create/meta-save), so
  newly ingested pages and overview.md updates appear during a bulk import without a reload.
- **Synthesis/comparison suggestions in the regular review sweep**: `propose_corpus_shape_review()`
  was only reachable via an explicit `POST /ops/synthesize` call. Added it as sweep Pass 3
  (deterministic, no extra provider call) so borderline-confidence synthesis/comparison
  clusters reach the F9 review queue during normal operation, not only on an explicit
  synthesize run. Gated by `review_corpus_shape_enabled` (default on).
- **macOS: closing the window now hides it** instead of quitting the whole app — matching
  standard macOS convention, using the tray Show/Quit menu that already existed. Cmd+Q and
  the tray's Quit still fully exit. Windows/Linux close behavior is unchanged.
- **Changelog bundling safety net**: `copy-changelog.mjs` no longer silently ships a desktop
  build without `CHANGELOG.md` — it now falls back to a repo-root search and fails loudly
  during a release prebuild if the file genuinely can't be found. CI now also verifies
  `frontend/dist/CHANGELOG.md` exists and is non-empty before a release ships.

## [2.1.1] — 2026-07-19 — "non-convergence fixes"

Patch release fixing two related, live-observed ingest non-convergence bugs. No schema
migrations, no other changes since 2.1.0.

### Fixed

- **Non-convergent ingest runs for sources in a raw subfolder**: when a source document's raw
  file lived under a subfolder (e.g. `raw/sources/Cloud Licensing/doc.md`), the `folderContext`
  prompt hint (R7-6) — meant purely as topical context — was worded ambiguously enough
  ("...when classifying the document **and naming/linking pages**") that the model would mirror
  the raw subfolder into the generated source page's file path (`wiki/sources/Cloud
  Licensing/doc.md`) instead of the required flat `wiki/sources/doc.md` (K6). Because
  retry-with-context re-injects the same hint unchanged on every iteration, the model repeated
  the identical mistake across all attempts and the run burned its full iteration/token budget
  non-convergent. Reworded the hint to state plainly that it must never influence a page's file
  path — source pages always use the exact path given elsewhere in the prompt; entity/concept
  pages still route only through the schema table or `wiki/entities/`/`wiki/concepts/`.
- **Non-convergent ingest runs from a spurious `wiki/log.md` FILE block**: the generation prompt
  asked the model to "also" emit a log entry for `wiki/log.md`, even though `append_log()`
  already appends one automatically for every page written (K4) — fully server-managed. The
  block-loop validator already anticipated a model emitting a `log.md` block and skips it by
  exact filename, but a model would sometimes satisfy the redundant instruction with a
  differently-named file (e.g. `wiki/log-entry.txt`), which slipped past that guard and failed
  frontmatter validation identically on every retry. Removed the instruction — the model has no
  legitimate reason to touch `wiki/log.md` at all.

## [2.1.0] — 2026-07-18 — "iOS redesign + close-out"

The flagship item deferred from 2.0.0 — the **complete native iOS redesign** — plus the
backend/frontend cleanup batch that closes out the items 2.0.0's own audit flagged as follow-up.
No schema migrations.

### Added

- **Track iOS 2.1 — complete native redesign (flagship, #102, #105, #106)**: the iOS app is
  rebuilt from the ground up in native SwiftUI, targeting parity with the desktop experience.
  - **Fase A — design foundation** (ADR-0088): a brand-aligned design system
    (`ios/Synapse/DesignSystem/`) ported token-for-token from the desktop theme (accent
    `#2563eb`, per-type jewel tones, light/dark parity, never pure black), a native 5-tab shell
    (Home · Wiki · Chat · Graph · More), and a documented graph-rendering spike (WKWebView sigma
    embed vs native Canvas/SpriteKit — both consume the same server-side FA2 coordinates, so I2
    holds either way).
  - **Fase B — core surfaces**: Home, Wiki reading, Chat (streaming + citations), and Search
    rebuilt as real API-backed screens against the 2.0.0 backend (error envelope, SSE).
  - **Fase C — parity & GA**: Graph, Review queue, Sources/Activity, and Settings brought to
    parity; the legacy pre-redesign theme retired; TestFlight distribution wired up.
- **Retry-with-context** (ADR-0085 §4, #108): retrying a "did not converge" ingest run now
  injects the prior run's validation-failure diagnostics into the first regeneration attempt,
  instead of starting the retry blind.
- **Coverage ratchet** (#108): `pytest-cov` now enforces a `fail_under = 82` floor in CI (measured
  baseline).
- **Focus-trap** for `CascadeDeleteModal`, `CommandPalette`, and `ResearchTopicDialog` (#109,
  #112) — Tab now cycles within the dialog and focus returns to the trigger on close, matching
  the pattern already used elsewhere.
- **FE-BUNDLE-1** (#112): the Italian locale is now a separate, dynamically-imported chunk
  (`dist/assets/it-*.js`, ~70kB) instead of a static bundle import — English-only sessions no
  longer download it.

### Fixed

- **Web wikilink navigation** (#107): `[[slug|Display Text]]` piped wikilinks — the format the
  ingest LLM actually emits — carry the page *slug*, not its title, in `data-wikilink`. The click
  handler only ever checked a title-keyed index, so these links always failed with "page not
  found" even when the target existed. It now falls back to `GET /pages/by-slug/{slug}` before
  giving up.
- **`OllamaProvider` silent empty responses on reasoning models** (#103): `complete()` didn't
  account for the `thinking` field some reasoning-capable models (e.g. qwen3.5) emit alongside
  `content` — a response could come back empty if the token budget was consumed by reasoning
  output before any content was generated.
- **CI E2E artifact upload was silently broken** (#110): the Playwright run step passed
  `--reporter=list` on the CLI, which *replaces* rather than merges with `playwright.config.ts`'s
  configured `[["list"], ["html", ...]]` reporter array — so the HTML report never ran and
  `frontend/playwright-report/` (the only path previously uploaded) was empty on every prior
  flake investigation. Fixed by dropping the override and also uploading
  `frontend/test-results/`, where the actual per-test trace files live.
- **CSP dark-theme KaTeX flake, resolved** (#104, #113): after an initial pattern fix, the
  dark-theme test still flaked under CI-only timing variance. Root cause: `playwright.config.ts`
  carries a suite-wide 30s default *per-test* timeout, independent of the 10s default
  *per-assertion* timeout — so widening an individual `expect(...)` call's timeout alone did
  nothing once it exceeded the outer ceiling. Fixed with an explicit `test.setTimeout(60_000)` in
  the test body, raising the ceiling itself.

### Changed

- **`GraphViewer.tsx`** split further, 1588→1075 lines (#111); **`HomeDashboard.tsx`** split for
  the first time, 3119→812 lines (#112) — both extractions are move-only, with the sigma-mount
  and in-place graphology-diff effects (I2/I3) kept intact.
- **Backend layering**: two remaining lazy `app.main` imports in `app/projects.py` replaced with
  `runtime_state` bridge accessors (#108).
- Seven frontend API clients migrated to the shared `ApiError`/`checkResponse` pattern; several
  store/view pairs (ingest, research, import-schedule) moved from timer-driven polling to
  fetch-on-SSE-event with poll as a fallback; run lists and search views gained
  Skeleton/EmptyState treatment (#109).

### Known follow-ups

- **iOS wikilink tap-to-navigate** (draft PR #114, unmerged): SwiftUI's `Text(AttributedString)`
  does not reliably dispatch link taps to `.environment(\.openURL, ...)` in this app's view
  hierarchy — root-caused via live Simulator instrumentation, not an app-logic bug. Fix
  (`LinkableText.swift`, a `UITextView`-backed `UIViewRepresentable` bridge) is implemented but
  not yet confirmed working live: Simulator interaction was blocked for the remainder of the
  session by a recurring Notification Center overlay issue unrelated to the app itself. Needs a
  manual tap-to-navigate check before merge.
- **ADR-0083 parity harness**: not re-run against the 1.9.4 review-create block-loop migration —
  requires `/Applications/LLM Wiki.app` (not installed on this machine) or an approved
  alternative gold fixture. Pending for three releases now.

## [2.0.0] — 2026-07-17 — "one engine"

The v2.0 train's destination release — three intentional, SemVer-major breaking changes that
1.9.x spent five releases preparing the ground for, plus a security hardening pass, a
documentation-truth pass, and an open-source-readiness pass ahead of wider publication. No
schema migrations this release, deliberately: all schema work landed in 1.9.x with backups
already active, so rollback is simply redeploying the 1.9.4 images.

### Breaking changes

- **Legacy JSON ingest pipeline removed** (`app/ingest/loop.py` deleted, `ingest_pipeline_format`
  config key gone). The block-based pipeline — the only path since 1.9.4 gained chunked-analysis
  support for long sources — is now the sole ingest engine; there is no rollback lever left to
  flip. `enrich_wikilinks` (already dead per ADR-0076) removed alongside it. Anyone who had
  `ingest_pipeline_format="json"` set must remove it; the block engine is a strict superset of
  what the JSON path did.
- **Compatibility facades dissolved**: the `app.ingest.orchestrator` module's ~40-symbol
  re-export surface (kept since 1.7.0 so the test suite's monkeypatches and any external importer
  kept resolving through one module) and the `app.main.<name>` aliases (kept since 1.9.2) are
  both gone. Production code and the full test suite (3107 tests, same count as before —
  patches were relocated, not deleted) now import every symbol from its real home:
  `app.ingest.pipeline` / `app.ingest.writer` / `app.ingest.context` for the ingest siblings,
  `app.runtime_state` for the former `app.main.*` aliases. External integrators who imported
  through either facade must update their import paths.
- **Stable JSON error envelope** (ADR-0086): every error response — from a raised `SynapseError`,
  a Pydantic `RequestValidationError`, or a raw `HTTPException` — now returns
  `{"error": {"code", "message", "status", "details"}}` instead of the ad-hoc `{"detail": ...}`
  FastAPI produces by default. `code` is a stable, mechanically-derived snake_case slug
  (`NotFoundError` → `not_found`) that is now a public contract for the frontend, MCP clients,
  and any external API consumer. The frontend's single `checkResponse()` choke point (extracted
  in 1.9.2 for exactly this kind of change) and ~13 API clients were migrated to parse the new
  shape; `ApiError` gained a `code` field so callers can branch on stable codes instead of
  parsing message strings.

### Added

- **W-FE — 11 curated frontend-audit findings**: a semantic `<main>` landmark + skip-to-content
  link; keyboard access for the vault-switch and provider-accordion controls (previously
  click-only); a "New page" command-palette action; a frontend import-layering ESLint rule
  (`api/*` may not import `store/*`) mirroring the backend's import-linter contracts; `prettier
  format:check` wired into CI (repo was already 100% compliant — zero reformat diff).
- **Content-Security-Policy** (ADR-0087, SEC-CSP-1): `script-src 'self'` with no `unsafe-inline`/
  `unsafe-eval` — verified by a new Playwright suite asserting zero CSP violations across Home,
  Chat (including live KaTeX math rendering), Graph, Wiki, and Settings, in both themes.
  `style-src 'unsafe-inline'` is required and documented as a genuine constraint (KaTeX's
  `htmlAndMathml` output mode, React's `style={{}}` props, and `index.html`'s inline reset all
  depend on it) — not a shortcut; `script-src` stays fully locked down since the production
  build emits no inline scripts.

### Fixed

- **Two real bugs from the W-FE audit**: `GraphViewer`'s diff-in-place refresh no longer leaves
  stale nodes on screen when a background `/graph` refetch legitimately returns zero nodes (e.g.
  after a cascade-delete emptied the vault); `statusStore.setDataVersion` now has a monotonic
  guard so an in-flight REST response can no longer overwrite a more recent value SSE already
  pushed. Both shipped with regression tests that were verified to actually fail against the
  pre-fix code.
- **Personal paths and a stale placeholder repo URL** found and redacted during the
  open-source-readiness pass, ahead of wider publication.

### Documentation

- **C4 architecture diagrams (D1)** rewritten from a ~20-release-stale v0.4 baseline to reflect
  the current module boundaries (the dissolved facades, the single block-loop engine, the error
  envelope).
- **USER.md / DEPLOY.md** updated to 2.0.0 with explicit migration notes for all three breaking
  changes.
- **`test_docs.py`** strengthened from a shallow existence check into a real freshness harness:
  12 new assertions that fail if a doc references a deleted module, an old error shape, or a
  stale version header — so the next major release's doc-truth pass starts from a real gate,
  not a fresh audit.
- **ADR-0086** (stable error envelope) and **ADR-0087** (CSP policy) added to the index.

### Known follow-ups (not in this release)

- **iOS 2.0 GA deferred to 2.0.1.** The parallel iOS redesign track's Fase A (design foundation)
  and Fase B (core surfaces) — planned to run alongside 1.9.3/1.9.4 — never started this train;
  there is no foundation yet for a GA flagship. Per the plan's own risk mitigation for exactly
  this scenario, 2.0.0 ships without it rather than block the core breaking changes; the existing
  native iOS client on `main` continues to work unchanged against the 2.0.0 API. iOS work resumes
  as its own dedicated track.
- **`_resolve_fallback_provider_config()` in `app/ingest/pipeline.py` is now dead code** — its
  only call site (the JSON loop's provider-fallback branch) was removed in this release's
  pipeline deletion, but the function definition itself was not caught by that pass since it
  still has other superficial references. Confirmed unreachable; scheduled for cleanup, not a
  behavior risk.
- **Ollama provider doesn't handle "thinking"-capable local models' empty-content responses**
  (found live during the 1.9.4 release-test, not introduced this release) — flagged as a
  separate follow-up task, not fixed here.

## [1.9.4] — 2026-07-17 — "completion pack"

Fifth release of the v2.0 train — the last of the refactor/feature "completion" set before
2.0.0's breaking changes. Long-source chunking finally reaches the default block-loop pipeline
(closing the last blocker to deleting the legacy JSON path), review-create moves onto the same
engine, ingest gets a self-healing 429 queue, per-device scoped API tokens land, MCP/clipper
go multi-vault-aware, pages get a one-line gloss in the index catalogue, and the deprecations
that 2.0.0 will act on start warning now. Five workstreams (some bundled), two migrations.

### Added
- **Chunked-analysis long-source support in the block loop** (flagship, PF-LONGSRC-1):
  `run_block_loop`'s Stage 1 now chunks sources past `ingest_long_source_char_threshold`
  (48K chars, the existing config) via the same bounded chunker `long_source.py` already used
  for the legacy JSON path, then merges per-chunk analyses under `## Source section i/N analysis`
  headers (`merge_analysis_texts()`, a markdown-prose sibling of the existing
  `merge_analyses()`). This fixes the silent truncation of long regulatory PDFs and is the
  probable root cause, confirmed by 1.9.1's non-convergence diagnostics, behind opaque
  "Non convergito" runs on long documents. Sources below the threshold take the exact same
  single-call path as before (byte-identical, verified by test). The on-disk JSON-loop
  checkpoint mechanism is deliberately not reused here — the two paths produce different
  analysis shapes (structured objects vs. free markdown) — an accepted, documented trade-off.
- **Smart ingest queue** (PF-QUEUE-429-1): the queue now auto-pauses on a 429 from any provider
  (`ProviderTransientError` for CLI, `httpx.HTTPStatusError` status 429 for API/Ollama) with a
  decaying backoff ladder (30s → 60s →120s → 300s, hard-capped per I7) instead of failing the
  run outright; a subsequent successful call resets the backoff. A manual `pause()` always takes
  priority and cancels any pending auto-resume. New per-capability concurrency caps
  (`ingest_concurrency_cli`/`_api`/`_local`, default 1/3/1) sit alongside the existing flat
  `INGEST_MAX_CONCURRENCY` limit, so a slow local model no longer starves API-backed runs of
  their own slots.
- **Scoped, revocable API tokens** (PF-AUTH-1): new `api_tokens` table (migration) — label,
  PBKDF2-hashed secret (plaintext returned once, at creation, never again), optional vault
  scope, read-only flag, `last_used_at`. `POST/GET/DELETE /config/api-tokens` + a Settings →
  Security UI card. The bootstrap `SYNAPSE_AUTH_TOKEN` env var is unchanged and checked first —
  this is additive, not a replacement — and unblocks per-device credentials (iOS, clipper) that
  can be revoked individually instead of sharing the one bootstrap secret.
- **Multi-vault MCP and clipper** (PF-MCP-VAULT-1, PF-F11-PICKER-1): every MCP tool gains an
  optional `vault` parameter (falls back to the active vault when omitted or unknown); a new
  write guard rejects cross-vault writes. The Chrome clipper's `ClipRequest` gains `vault_id`
  and its popup UI gets a vault picker populated from `GET /projects`, so clipping to a
  non-active vault no longer silently lands in the wrong place.
- **Gloss catalogue** (PF-INDEX-GLOSS-1): a new nullable `Page.summary` column (migration),
  populated from each page's first paragraph at write time (`extract_first_paragraph_summary`,
  pure — no LLM call) and rendered as an em-dash gloss next to every `index.md` entry
  (`- [[Title]] — gloss…`, capped at 120 chars). A one-time, idempotent `--dry-run`-capable
  backfill script covers pages written before this release.
- **Deprecation warnings for 2.0.0's planned removals**: a startup warning fires when
  `ingest_pipeline_format` is set to the legacy `"json"` value, and importing from the
  orchestrator compatibility facade or the `app.main.*` aliases now raises `DeprecationWarning`
  — both scheduled for deletion in 2.0.0.

### Changed
- **Review-create migrated onto the block loop** (BE-DEBT-1, step 1 of 2 toward deleting the
  legacy JSON generation path): the single-page generation route now calls
  `ingest.block_loop.run_block_loop` instead of `ingest.loop.run_orchestrated_loop`, parsing the
  block loop's FILE-block output into a `WikiPage` (`_wiki_page_from_file_block`) while
  preserving every non-`source` page type and guaranteeing `origin_source` stays in `sources[]`.

### Fixed
- **A real import-linter layering violation, found during this release's own verification, not
  by CI**: the new MCP vault-parameter code (`app.mcp.server`) picked up a transitive import of
  `app.main` through `app.projects`'s existing write-path helpers — a violation of the
  "Routers/MCP must not import main" contract from 1.9.2. Root-caused (import-linter counts
  lazy/deferred imports as graph edges too) and fixed with a clean extraction rather than an
  allowlist: the pure, read-only registry model (`Project`, `ProjectsResponse`, `read_registry`)
  moved into a new `app/project_registry.py` with zero `app.main` dependency; `app/projects.py`
  now re-exports it for backward compatibility. `lint-imports` reports all 4 contracts kept.
- Two rounds of docs-gate drift caught before merge (ER diagram missing the new `api_tokens`
  table, then missing `pages.summary`; OpenAPI missing the new MCP `vault` parameters) —
  regenerated and committed rather than left for CI to fail on (I8).
- A migration-number collision between this release's two independently-developed branches
  (W4's `api_tokens` and W6's `page_summary_gloss` both claimed `0036`) — anticipated in both
  PRs' descriptions and resolved at merge time by renumbering the second-merged migration to
  `0037`.

### Known follow-ups (not in this release)
- The legacy JSON ingest pipeline itself is still present — this release only ports its one
  missing capability (chunking) to the block loop and starts warning on its use. Deletion is
  2.0.0's first breaking change.
- ADR-0083's parity harness (manual, live-stack only) was not re-run against the review-create
  migration in this pass — a human/QA call before 2.0.0, not an automated gate yet.

## [1.9.3] — 2026-07-17 — "live wire"

Fourth release of the v2.0 train, and the flagship of the refactor half: a real-time push
channel replacing blind polling, the graph viewer's WebGL instance no longer resets itself
mid-ingest, one unified UI kit, and two long-standing UX papercuts (full-page reload on vault
switch, a command palette that could only navigate) closed out. Five workstreams, zero
migrations.

### Added
- **`GET /events` SSE push channel** (flagship, FE-RT-2): a change-driven generator streams
  `data_version` and ingest-queue counts to the browser instead of the client polling for them.
  Heartbeat every 15s, hard 30-minute stream cap (I7-bounded), clean shutdown on client
  disconnect. The frontend `eventsStore` uses a manual `fetch()` + `ReadableStream` reader rather
  than native `EventSource` (which can't carry the Bearer/CF-Access auth headers), with
  exponential-backoff reconnect and `Last-Event-ID` resume. This is purely additive: every
  existing REST poller (`statusStore`, `activityStore`, and 6 others) keeps running at its normal
  cadence forever — when the stream is healthy, `statusStore`/`activityStore` merely relax to
  their idle interval instead of the active one. A single failed connection attempt changes
  nothing; only after 3 consecutive failures does the cadence un-relax. Two pollers
  (`data_version`, ingest-queue counts) are wired to the stream this release; the remaining six
  (import-schedule, deep-research, Sources ingest-all, Convert, HomeDashboard synthesize-status,
  the ingest run-list) are left on their existing cadence, unaffected and un-migrated.
- **Command palette v2**: executable actions (new chat, import content, run lint scan, switch
  project, switch theme, regenerate overview) alongside the existing page/section navigation —
  each delegates to the same store/API call its own dedicated UI control already uses.

### Changed
- **`GraphViewer.tsx` (3,627 lines) had its 7 already-formed subcomponents extracted** (move-only,
  FE-ARCH-1) into `components/graph/`: header, legend, centroid overlay, status bar, community
  panel, node tooltip, edge-breakdown tooltip, plus a shared module for palette/halo-drawing
  helpers.
- **The sigma WebGL instance is no longer killed and rebuilt on every background data refresh**
  (FE-RT-1). During a long ingest, the periodic `/graph` refetch used to tear down and recreate
  the entire renderer — resetting the camera and losing hover/drag state every ~10s. It now diffs
  the incoming payload into the already-mounted graphology graph in place (`sigma.refresh()`, no
  `kill()`) and only rebuilds on a real theme/colorMode change or the true first mount. New nodes
  appearing mid-ingest now animate into the existing view instead of triggering a full reset.
- **Startup fan-out and re-render efficiency** (FE-PERF-1/2/3): the Home dashboard's "open
  questions" block now asks the server to filter by page type (`GET /pages?type=query`) instead of
  fetching 100 rows to find 5; four below-the-fold blocks (active jobs, review preview, cost
  sparkline) defer their first fetch to `requestIdleCallback`, cutting the synchronous startup
  burst from 14 calls to 9; `activityStore` skips its state update entirely when a poll snapshot
  is structurally unchanged, so unrelated components stop re-rendering on no-op ticks; chat
  streaming batches token/think deltas into one `requestAnimationFrame`-scheduled update per frame
  instead of one per token (I3 unaffected — markdown/LaTeX are still parsed only once, at stream
  end).
- **One UI kit**: new `components/ui` primitives (Button, Chip, Card, Field, Notice, Skeleton)
  replace both parallel button systems that had accumulated — the legacy `.syn-button` CSS classes
  and the `BTN_PRIMARY`/`BTN_SECONDARY`/`INPUT_STYLE` inline-style consts (13 consumers across
  Settings). A `--syn-font-*`/`--syn-space-*` token scale was added; 26 hand-rolled inline SVG
  icons were replaced with `lucide-react` equivalents; the graph's community/domain palette
  gained dark-theme variants (the near-black domain swatch is gone — never black, per brand);
  skeleton loading + the shared `ErrorState` component were extended to the Sources/Lint/Review
  list views.
- **Switching the active vault no longer reloads the page** (FE-UIUX-3): `resetAllVaultStores()`
  is the single choke point both the project launcher and the new-project wizard now call after
  activation — it resets every vault-scoped store (graph, ingest, activity, chat, lint, review,
  research, import-schedule, provider, status, app) and fires one status refresh; each section
  view already refetches on its own `[vaultId]` effect, so no extra plumbing was needed.

### Fixed
- **A GraphViewer bug found during this release's own verification**: the live-diff effect
  (above) merged the wrong attribute shape into the mounted sigma graph — raw
  `type`/`community`/`domain` fields instead of the color/label/reducer attributes the mount
  effect computes — which stripped every node's color on the very first background refresh after
  mount and broke rendering. Caught by reproducing the CI E2E failure locally against a real
  Postgres + Docker stack (not just the mocked unit tests, which don't exercise real sigma.js)
  before merging.
- **A persistent SSE connection was silently hanging the entire E2E suite**: `page.goto(...,
  { waitUntil: "networkidle" })` can never resolve once a long-lived stream is open (Playwright's
  networkidle waits for zero in-flight requests), which turned nearly every existing spec into a
  30-second timeout the moment the SSE channel above was wired into `AppShell`. Fixed two ways:
  the stream connection is deferred past the initial load burst (`requestIdleCallback`), and the
  9 affected E2E spec files were switched from `networkidle` to `domcontentloaded` — every one of
  them already re-verifies readiness with an explicit `waitForSelector` right after navigation, so
  `networkidle` was never load-bearing for the assertions themselves.

### Known follow-ups (not in this release)
- `GraphViewer.tsx` is 1,565 lines post-extraction — above the ~800–1,000 target. The remainder is
  the core sigma-mount/diff logic plus several small store-sync effects tightly coupled to the
  persistent sigma ref; a further split needs its own pass.
- FE-BUNDLE-1 (lazy-loading the inactive i18n locale) was scoped for this release but not
  completed — deferred, matches the plan's own "partial" scope for this workstream.
- The remaining 6 REST pollers not yet wired to the SSE channel (see Added, above) are candidates
  for a future release once the two flagship consumers prove out in production.
- SSE behavior through the real Cloudflare Tunnel + Access + Tailscale production path (heartbeat
  timing, reconnect on idle-kill) was verified against a local Docker stack for this release, not
  the live tunnel — see the release notes for this version.

## [1.9.2] — 2026-07-17 — "real boundaries"

Third release of the v2.0 train. Theme: finishing the module decomposition the 1.7.0 sprint
started, behind compatibility facades — pure refactors, zero observable behavior change. Seven
workstreams, zero migrations (deliberately: this release moves code, not schema).

### Changed
- **`app/runtime_state.py` extracted from `main.py`.** MCP source classification, PBKDF2 token
  helpers, the four DB-backed config caches (remote-MCP flag, MCP auth, clip config, web-search
  config), and the bearer-auth middleware now live in one module with typed accessors — no more
  `_m.X: Any`. The 13 duplicated `_LazyMain` proxy classes across routers are gone. Temporary
  `app.main.*` aliases remain for this release (dropped in 2.0.0) so existing test patches keep
  working.
- **`routers/config.py` (2,800 lines) split into a package** by domain: provider config,
  provider-vendor probes, embedding, MCP, import-schedule, clip, web-search, CLI-auth, app-config —
  plus `app/schemas/config.py` for the shared DTOs. OpenAPI is byte-identical before/after (verified
  in review, not just claimed).
- **`ops/review.py` (3,960 lines) split into a package**: `queue.py` (CRUD/status),
  `propose.py` (LLM propose+sweep), `create.py` (the generation/stub-create engine),
  `suggestions.py` (purpose/schema-suggestion apply), `prompts.py`. A subtlety found in review: many
  tests patch private seams via the string path `app.ops.review.X`, called internally by sibling
  functions now in different submodules — a static cross-module import would have silently made
  those patches inert. Fixed with deferred (call-time) imports at the specific internal call sites,
  and by routing all DB-session access through `from app import db as _db` so
  `patch("app.db.get_session", ...)` keeps working regardless of which submodule calls it.
- **`ops/lint.py` (2,666 lines) split into a package**: `detectors.py` (the 3 deterministic
  structural checks — no LLM, I1), `fixes.py` (human-gated deterministic appliers, now dispatched
  via a category→handler registry instead of an if/elif chain), `semantic.py` (the LLM-backed
  opt-in pass, reusing 1.9.0's shared `app.ops._llm` plumbing), `persistence.py`.
- **Domain exception taxonomy** (`app/errors.py`): a `SynapseError` hierarchy (NotFound, Conflict,
  Validation, Upstream, and 9 others) with one global FastAPI handler that translates any of them to
  the *exact* `HTTPException` response already produced today — same status code, same `{"detail":
  ...}` shape, same headers (verified with 15 parametrized tests comparing against a plain-
  `HTTPException` control route). The standardized error envelope itself is deferred to 2.0.0; this
  release only lands the infrastructure plus a representative migration slice
  (`create_page_from_review` and its stub-create path).
- **Frontend**: a shared `createPollChain`/`usePollChain` abstraction (single setTimeout chain,
  `AbortController`, refcounted subscribe) replacing 9 independent hand-rolled poll
  implementations across 4 stores and 5 components/hooks; the `/status` poll moved from
  `ActivityBar` (a presentation component) into `statusStore`, so it no longer stops if the bar
  unmounts; `api/errors.ts` extracted from `graphClient.ts` (was used by 12 clients via an odd
  import); `api/types.ts` (~1,100 lines) split into one file per domain behind a re-export barrel;
  a new `appStore` (navigation/vault/selection) split out of `graphStore` (now graph-data-only);
  `ReviewQueueView.tsx` (2,149 → 868 lines) had its inline subcomponents extracted (move-only,
  zero test churn). `GraphViewer.tsx` and `HomeDashboard.tsx` monolith extraction is scoped but
  not done — deferred to keep this release's diff reviewable.
- **Import-linter layering enforcement** (`.importlinter` config in `pyproject.toml`, wired into
  CI as a blocking lint step): four contracts encode the layer boundaries the decomposition above
  just created — models → persistence → services → routers/mcp → main, each forbidding the reverse
  direction. Baseline came back clean after fixing 4 lazy `app.main` imports to use the new
  `runtime_state.graph_cache()` accessor; the one remaining genuine inversion
  (`ingest.pipeline` → `mcp.server`, needed for the CLI-delegated ingest route per ADR-0010 §2) is
  documented and allow-listed rather than forced under time pressure.

### Known follow-ups (not in this release)
- **BE-ARCH-1 (orchestrator facade dissolution) remains parked** (see 1.9.1's known-follow-ups).
  Only a stale-docstring fix landed; the actual extraction needs a dedicated test-decoupling pass
  first — the test suite's coupling to `app.ingest.orchestrator` as its universal monkeypatch
  surface is deeper than a prior estimate found.
- `GraphViewer.tsx` (3,624 lines) and `HomeDashboard.tsx` (3,107 lines) monolith extraction is
  scoped (the same move-only approach used on `ReviewQueueView.tsx`) but not executed this release.
- `app.projects` retains 2 lazy `app.main` imports outside the five layers the import-linter
  contracts currently cover — not a violation of any defined contract, just unaddressed scope.

## [1.9.1] — 2026-07-17 — "fast paths"

Second release of the v2.0 train. Theme: backend query/index performance, ingest-tail write
coalescing, a cross-vault citation-leak fix, automated backups + supply-chain hardening, and
non-convergence diagnostics for a bug reported live during 1.9.0's rollout. Five workstreams,
three serialized Alembic migrations (0033→0034→0035).

### Fixed
- **Lint's broken-wikilink scan re-ran a full resolver-map build and event-loop-blocking fuzzy
  matching per dangling link**, despite a comment claiming otherwise — on a large vault this meant
  up to 1000 full-table scans and ~2M synchronous Levenshtein comparisons per scan, stalling chat
  and `/status` while it ran. Resolver maps now build once per scan; the fuzzy-suggestion loop runs
  in `asyncio.to_thread`.
- **UUID-column casts in retrieval/graph queries defeated the primary-key index** —
  `CAST(id AS TEXT) = :param` forced a sequential scan. Casting the bind *parameter* to `uuid`
  instead restores index use: measured **~650×** faster on `_load_page_meta` (18.7ms → 0.03ms) and
  **~330×** on `_expand_frontier` (22.6ms → 0.07ms) against a 200k-row table.
- **`reresolve_dangling_links` resolved dangling links from *every* vault against the active
  vault's map** — a correctness bug, not just a performance one. Now scoped to the requesting vault,
  backed by a new partial index (migration 0033).
- **`ingest_runs` had no index at all** — the run-list poll and monthly cost scans paid a
  sequential scan + sort on every call. New composite `vault_id, started_at` index (migration 0034).
- **The per-page ingest tail did 2 full table scans and rewrote `index.md` after every single
  generated page** — a 20-page document on a 5,000-page vault meant 40 scans + 20 rewrites + 20
  `data_version` bumps (each invalidating the stats caches the 1.8.1 adaptive poll checks every 3s).
  Resolver maps now build once per document, updated in memory as pages are written; `index.md` and
  `data_version` update once per document instead of once per page.
- **A cross-vault citation leak in retrieval.** Qdrant points carried no `vault_id`, so Phase-1
  vector search queried across all vaults unfiltered, and — more seriously — `_load_page_meta`
  filtered `deleted_at`/`raw/` but never `vault_id`, meaning a page from another vault could
  actually be cited in chat or search on a multi-vault deployment. Points now carry `vault_id` (with
  a payload index); a one-shot backfill script is provided for pre-existing points
  (`scripts/backfill_qdrant_vault_id.py`).
- **"Non convergito" gave no indication of why.** A block-loop run that exhausted `max_iter` without
  converging logged its stop reason and per-iteration validation errors, but never persisted them —
  `error_message` is NULL by design on `converged_false` rows. The run detail view now shows the
  actual stop reason, iteration count, last validation errors, and token budget spent vs. cap
  (new `ingest_runs.diagnostics` column, migration 0035).
- **Marker and Whisper (already fixed in 1.9.0) plus other perf paths**: `GraphCache`'s HIT path
  paid 3 queries (including a JOIN-based COUNT) and a full Pydantic re-serialization on every
  request even when nothing changed — now caches pre-computed totals and the serialized body
  alongside the snapshot marker. `/stats/overview`'s ~11 sequential cache-miss queries consolidated
  and parallelized. `/status` no longer re-resolves the provider config on every poll just to read
  `supports_vision`.
- **`GraphCache`'s debounce had no maximum wait** — a continuous ingest burst could defer the
  background FA2 recompute indefinitely, pushing the layout inline onto `GET /graph` (blocking).
  Added a debounce max-wait plus a stale-while-revalidate path: a MISS with a usable snapshot no
  older than a bound now serves it immediately and kicks one background recompute (same in-flight
  guard, never two concurrent FA2 runs — I2 untouched).

### Added
- **Automated Postgres backups.** A new `backup` operation in the existing `OpsScheduler`
  (off/hourly/daily/weekly, same pattern as lint/backfill/reclassify) runs `pg_dump -Fc` with
  retention pruning; `POST /ops/system-update` now dumps before poking Watchtower, giving every
  auto-update a fresh rollback point. `GET /export/full` extends the existing export to also cover
  conversations, `provider_config` (ciphertext as-is, never decrypted), and `vault_state`.
- **Supply-chain hardening**: `backend/requirements-lock.txt` / `requirements-prod-lock.txt` via
  `uv pip compile`; a new Dockerfile `production` target (prod-only deps, no tests) that the
  default build path is unaffected by; a non-blocking `pip-audit`/`npm audit` CI job;
  `.github/dependabot.yml` (weekly pip/npm/actions); `contents: read` added to `ci.yml`'s
  top-level permissions.
- **A `workflow_dispatch`-only live-smoke lane** (`live-smoke.yml`): runs `pytest -m live` plus the
  ADR-0083 parity harness against a manually-supplied llm_wiki gold snapshot — an on-demand
  pre-release gate, not part of every-push CI.

### Known follow-ups (not in this release)
- **BE-ARCH-1 (orchestrator facade dissolution) is parked.** A 1.9.2 attempt found the test suite's
  coupling to `app.ingest.orchestrator` is far deeper than a prior estimate (169 patch references
  across 18 files, not 23/13) and the `orch.<name>` mirror is *deliberately* load-bearing by 1.7.0
  design. The extraction is not a pure refactor under the current suite; it needs a dedicated
  test-decoupling pass first. Only a stale-docstring fix landed from that attempt.
- Retry-with-context for a non-converged run (feeding the last validation errors back into a retry)
  was scoped and explicitly skipped — no seam exists today to inject prior context into a retry;
  documented in ADR-0085 rather than bolted on.

## [1.9.0] — 2026-07-16 — "clean room"

First release of the v2.0 train (1.9.x → 2.0.0, plan set 2026-07-16 from an 8-dimension multi-agent
audit of 1.8.1). Theme: repo hygiene, test infrastructure, and security hardening — no user-facing
feature work; every change is behavior-preserving or additive/opt-in. Ships in five workstreams.

### Fixed
- **Backend watcher could drop an in-flight ingest task to garbage collection.** `watcher.py`'s
  file-change handler fired `asyncio.create_task` without retaining a strong reference — the same
  bug class fixed for eight other sites in 1.8.1. It now holds one via the shared `_bg_tasks` set.
- **Stats caches (`/stats/overview`, `/stats/sections`, `/stats/groups`) were not vault-aware.**
  Switching the active vault could serve the previous vault's cached numbers until the next
  `data_version` bump happened to land on the new vault. Cache keys now include `vault_id`.
- **A misleading `AC-K6-2/3` warning fired on every raw source file.** The K6 frontmatter check
  (`type`, `title`, `sources` required) only applies to generated `wiki/` pages, but
  `_parse_frontmatter` logged it for `raw/sources/*` files too — which legitimately have no
  frontmatter. The warning is now `wiki/`-path-only; `raw/` paths log at debug level.
- **`overview.md`/`index.md`/`log.md` were invisible in the Nav Tree right after creating a new
  vault.** The onboarding wizard scaffolds the three meta pages to disk, but nothing indexed them
  into Postgres until an unrelated edit touched a file — so `GET /pages` (and the Nav Tree's
  Overview section) came back empty on a brand-new vault. `POST /projects` now explicitly indexes
  the three scaffolded files right after bootstrap (a targeted index of known files, not a rescan —
  I1 intact).
- **A CTA on the Home dashboard was silently broken in dark mode.** Synthesize's "review only"
  button used the class `syn-button--ghost`, which exists in no stylesheet, so it fell back to
  unstyled UA chrome. Fixed to the live kit's `syn-btn--ghost`.
- **Three prominent Home dashboard cards showed Italian text in the English locale.**
  `home.systemStatus.title`, `home.groups.title`, and `home.activeJobs.title` in `en.json` held their
  Italian source strings ("STATO DEL SISTEMA", "GRUPPI AUTOMATICI", "LAVORI ATTIVI") — invisible to
  key-parity checks since both locales had the same keys, just the wrong *value* in one of them.
  Retranslated; a new `i18n-language-leak.test.ts` CI gate catches the class going forward.
- **A dozen `aria-label`/toast strings were hardcoded in English** (nav rail, graph viewer, activity
  bar, panel resize handles, a conversation-creation failure toast) — Italian screen-reader users got
  English announcements despite an otherwise fully-paired i18n layer. All routed through `t()`.
- **Keyboard shortcuts (⌘1–5) and the command palette had drifted from the 1.7 nav rail** — the
  numeric shortcuts pointed at stale sections and the palette was missing Home/Convert/Projects.
  Both realigned to the current rail order.
- **`ConfirmDialog` did not restore keyboard focus on close**, and one native `window.confirm` was
  still in use (Settings → Maintenance) instead of the accessible dialog. Both fixed.
- **Marker and Whisper were reachable from the whole LAN with no authentication**, while the backend
  itself was already loopback-bound — `docker-compose.yml` published `8555`/`8666` on `0.0.0.0`.
  Both now bind to `${SYNAPSE_BIND_HOST:-127.0.0.1}` like the backend; Marker additionally supports
  an optional shared-secret `MARKER_SERVICE_TOKEN` for multi-host setups (off by default, fully
  backward-compatible).

### Added
- **`provider_config.base_url` allowlist validation** (write-time only, existing configs unaffected):
  scheme must be `http`/`https`; host must be `localhost`, `127.0.0.1`, `host.docker.internal`
  (the known Docker/TrueNAS gotcha — explicitly preserved), an RFC1918 private address (LAN
  Ollama/local LLMs), or a known provider host (`api.anthropic.com`, `api.openai.com`,
  `*.openai.azure.com`). Closes the base_url-as-exfiltration-channel gap flagged in the 2026-07
  security audit.
- **Rate limiting on authentication failures.** Repeated 401 responses from one client IP now trip
  the existing rate-limit infrastructure (429 after the configured threshold) — mitigates
  token-guessing against the bearer-token auth model.
- **Optional `QDRANT_API_KEY` support** for Qdrant deployments that require authentication (unset by
  default; no behavior change for the common unauthenticated local/LAN Qdrant).
- **Shared LLM-call helper (`app/ops/_llm.py`).** Seven ops modules (review, lint, enrich-wikilinks,
  synthesize, reclassify-types, deep-research, backfill-domains) each re-implemented the same
  resolve-provider → bounded-chat → parse-JSON-leniently → cost-log pattern, with divergence already
  starting between copies. Consolidated into one typed helper; **446 duplicated lines removed**
  across 8 modules (the 7 above + `ingest/domain_tagger.py`). Purely internal — no behavior change.
- **Shared SQLite test fixture.** 52 hand-written `CREATE TABLE` statements across 14 test files —
  every schema change historically broke ~20+ of them at once — replaced with one
  `Base.metadata.create_all()`-based fixture; schema changes now propagate automatically. Surfaced
  and fixed 8 columns that were `nullable=False` with no `server_default`, whose old hand-rolled DDL
  silently supplied a default that masked the real constraint (test inserts now match Postgres too).
- **CI hygiene**: a junk-file guard rejects the iCloud-sync `"name N.ext"` duplicate pattern in the
  tracked tree (root-caused a ~300-file cleanup this release — 9 of them importable backend modules,
  14 pytest-collectable test copies); an Alembic single-head check prevents the known
  parallel-migration-collision gotcha; `ci.yml`/`desktop-release.yml` gained concurrency groups;
  Playwright now retries once in CI only (never locally, to avoid masking real flakes locally) with
  a `FLAKE_LEDGER.md` ready for use; `pytest-cov`/`vitest --coverage` are wired in as available
  tooling (a no-decrease ratchet is future work).
- **ADR corpus unification**: retired the legacy `ADR-NNNN-*.md` naming (32 files renamed, 76 inbound
  links rewritten); resolved a duplicate-number collision (the multi-vault Project Launcher ADR was
  renumbered 0067→0082; generation-semantics parity keeps 0067). New `scripts/check_adr_index.py`
  (wired into the Docs Gate + `make adr-check`) checks naming, index completeness, dead links,
  duplicate numbers, and Status-line presence.

### Known follow-ups (not in this release)
- The DI-seam refactor needed to stop tests from monkeypatching `app.ingest.orchestrator.*` module
  globals was scoped and deferred to 1.9.2 (23 sites across 13 files mapped) — it's the stated
  precondition for that release's orchestrator-facade dissolution, not test-infrastructure work.
- Coverage measurement is instrumented but not yet gated (no ratchet threshold).

## [1.8.1] — 2026-07-16 — "ingest robustness, real-time UI & accessibility"

Bug-fix release hardening the ingest pipeline against token-dense (regulatory/tabular) documents,
making the dashboard and graph update in real time, and closing a batch of accessibility gaps —
found via a multi-agent audit and each adversarially verified before landing.

### Fixed
- **Token-dense documents crashed embedding and aborted the whole ingest.** `embed_max_chars` bounded
  *characters*, but bge-m3's limit is *tokens*: dense content (Marker-extracted tables, numeric
  registries, legal references) packs >1 token/char, so a payload well under the cap still made
  Ollama return HTTP 500 "input length exceeds the context length" — and that 500 propagated,
  aborting the entire document with **0 pages created**. `EmbeddingClient.embed` now catches that
  specific 500 and retries with the input halved down to a floor (bounded, tokenizer-free), and
  `upsert_vector` degrades an unrecoverable embedding failure to a **vector-less page** (the page
  stays fully indexed; only the Qdrant vector is skipped) instead of aborting the run.
- **"PAGINE CREATE 0" even when pages were written.** The counter keyed off the provider capability
  instead of the resolved route, so every CLI + block-pipeline run reported 0 pages; the failure
  path hard-coded 0; and the delegated count came from the model's self-reported tool-call count.
  All three now report the pages actually persisted.
- **CLI provider failures were opaque and fatal.** An empty/rate-limited completion raised one
  generic error that aborted the document with no retry. The provider now classifies the terminal
  result (rate-limit / overloaded / execution error → transient vs a clean empty), the block loop
  retries transients with bounded exponential backoff (and treats an empty generation as a
  zero-block attempt), and per-call cost/tokens are recorded even when the SDK raises mid-stream (I7).
- **Dashboard KPIs and the knowledge graph did not update in real time.** The server `data_version`
  was only refreshed by a fixed 30 s poll, so after an ingest the numbers and graph stayed stale up
  to 30 s. The `/status` poll cadence is now adaptive (~3 s while the queue is working, 30 s idle),
  and direct edits (page save, cascade-delete, lint-fix apply, save-to-wiki) push the new
  `data_version` immediately — so the UI reflects changes within seconds. The graph's re-fetch is
  throttled to ≥10 s during a long ingest to stay smooth (server-computed coords only; I2 intact).
- **Ingest run list froze / stopped polling** for runs beyond the first page during bulk imports —
  it now re-fetches the full loaded range and counts running rows across the whole list.
- **Background jobs could be garbage-collected mid-run.** Eight `asyncio.create_task` sites
  (deep-research runners, ingest-all drivers, review sweeps, the queue drain) did not retain a
  strong reference, so an unreferenced task could be GC-cancelled — wedging a run at "running"
  forever or leaving a permanent HTTP 409. All now hold a strong reference.
- **Accessibility:** the ⌘K search affordance is now a real button (reachable on touch/mobile, was an
  `aria-hidden` div); keyboard focus rings on the graph/search inputs are restored (WCAG 2.4.7);
  toast auto-dismiss pauses on hover/focus (WCAG 2.2.1); amber warning text meets 4.5:1 contrast;
  the nav rail moves focus to the active item under arrow keys.

### Added
- A subtle **"Updating…"** affordance on the dashboard and graph while a live refresh is in flight.

### Changed
- `EMBED_MAX_CHARS` default lowered 8000 → 4000 (the shrink-retry handles anything denser); the
  stale "degrade to a vector-less page (connectors.importer)" doc was corrected to describe the
  real, now-implemented behavior. `ConvertPanel`'s pollers use a `setTimeout` chain (no overlap, I7).

### Known follow-ups (not in this release)
- Queue-level 429 auto-pause + capability-keyed concurrency cap (the per-call retry-with-backoff is
  the current mitigation); removal of stray macOS `* 2.py` / `schema 4|5.mmd` duplicate files;
  a few graph/stats query micro-optimizations.

## [1.8.0] — 2026-07-16 — "one-click system update"

### Added
- **One-click system update (Settings → Info).** When a newer GitHub Release exists, Settings → Info
  now shows "Update available: vX.Y.Z" and an **Update system** button that pokes Watchtower's HTTP
  API (`POST /ops/system-update`) to pull the latest images and recreate the labelled containers —
  no more manual `docker pull`. Availability comes from `GET /ops/update-status` (running version vs
  the latest GitHub Release, cached ~1h). Requires Watchtower's HTTP API and a shared
  `WATCHTOWER_HTTP_API_TOKEN` (the compose now wires it for backend + marker); when the token is
  unset the button is hidden and the availability line still shows. No download percentage —
  Watchtower's API is fire-and-forget, a deliberate trade-off (B1) that keeps Docker privileges out
  of the backend. See `docs/DEPLOY.md` §9.2b. [R12-3]

## [1.7.4] — 2026-07-16 — "log/index parity fixes"

### Fixed
- **`wiki/log.md` was corrupted by the block-ingest path.** Three issues compounded: the model's
  `log.md` block was not dropped (only `index.md`/`overview.md` were), so it overwrote the
  code-managed log and destroyed its frontmatter; `schema.md` described a second, conflicting
  "Log Format" the model then emitted; and `append_log` fired **once per generated page** (plus
  bogus `## [date] ingest | wiki/log.md` / `| raw/sources/…` entries). Now `log.md` is app-managed
  (its block is dropped), `schema.md` marks it auto-maintained, and the block path appends exactly
  **one `## [YYYY-MM-DD] ingest | <source title>` entry per source** (llm_wiki parity). The
  watcher's raw-wiki-page indexing still logs one line per file (unchanged).
- **`index.md` counted and listed EVERY vault's pages, not the active vault's.** `update_index` ran
  its page query without a `vault_id` filter (e.g. "Total pages: 278" on a 35-page vault) — the same
  cross-vault leak class fixed for the graph resolver. The query is now vault-scoped.

## [1.7.3] — 2026-07-16 — "cleanup: drop personal deployment references from the UI"

### Changed
- **Genericized user-facing text that leaked a homelab-specific detail.** The Ollama row in
  Settings → AI & Providers described the local server as "Local Ollama server **(RTX 3060)**" — a
  personal-config leftover that shouldn't ship in the product. It now reads simply "Local Ollama
  server". Incidental hardware/IP references in code comments were genericized too (the Ollama
  provider docstring and a specific LAN IP in a frontend comment). Deployment docs (compose /
  `DEPLOY.md`) intentionally keep their TrueNAS references — they document the target platform.

## [1.7.2] — 2026-07-16 — "knowledge-graph fixes: file-slug links, hidden aggregates, node click-to-open"

Patch release: three real knowledge-graph defects found while validating a clean re-ingest of a
localized (Italian) vault. No migration, no API change.

### Fixed
- **The knowledge graph stayed near-empty on localized (non-English) vaults.** The wikilink resolver
  indexed pages only by `_slugify(title)`, but the generation prompt mandates bare-slug wikilinks
  (`[[multi-cloud-orchestration]]`) that match the FILENAME a page is filed under — and on e.g. an
  Italian vault the title is localized ("Orchestrazione Multi-Cloud …"), so `_slugify(title)` never
  reproduced the linked slug and almost every link stayed dangling. Measured on a clean 4-source
  Italian ingest: **2 of 114 links resolved (2 edges)**. The resolver now also indexes pages by their
  `file_path` slug; the same ingest yields **58 of 114 resolved (29 edges)**, the rest being genuine
  not-yet-created "missing-page" targets. This is distinct from — and deeper than — the 1.7.1
  vault-scoping fix. [F4]
- **`index` / `log` / `overview` appeared in the knowledge graph as stray isolated dots.** They are
  app-managed aggregate pages (catalogue / history / summary), not knowledge nodes, and Synapse writes
  them outside the link-persistence path so they carry no edges. They are now excluded from the graph
  via `GRAPH_HIDDEN_PAGE_TYPES` (alongside the existing `query` exclusion) — a deliberate step beyond
  llm_wiki, which keeps `index.md` as a catalogue hub. [F4]
- **Clicking a node in the knowledge graph did not open its wiki page.** `downNode` disabled sigma's
  mouse captor to stop the stage panning during a drag, which also suppressed the `upNode` /
  `clickNode` events the open handler relied on — so a click only highlighted the node. Click-to-open
  now runs from the `endDrag` (`!moved`) seam; stage-pan is prevented via `preventSigmaDefault` in
  `moveBody` (the official sigma v3 pattern); and a `suppressStageClick` flag stops the follow-up
  stage click (sigma classifies node clicks as stage clicks) from wiping the just-opened selection. [F4]

## [1.7.1] — 2026-07-15 — "post-1.7.0 fixes: output language, knowledge graph, onboarding UX"

Patch release fixing issues surfaced right after 1.7.0.

### Fixed
- **The knowledge graph collapsed on multi-vault deployments.** Wikilink resolution built its
  slug→page map over ALL vaults' pages (no `vault_id` filter, first-hit-wins), so when vaults share
  page slugs (e.g. the same sources ingested into several vaults) a link resolved cross-vault and
  produced NO graph edge — the target isn't a node in the source vault's graph. Measured on an
  8-vault DB: 50 graph-eligible links became 5 edges. `_build_resolver_maps` and the graph engine's
  link query are now vault-scoped. **Single-vault deployments were unaffected.**
- **overview.md ignored the per-vault output language.** Regeneration resolved language from content
  detection, so an Italian vault built from English sources got an English overview. It now honors
  `vault_state.output_language` (explicit override → per-vault → this run's analysis → detection).
- **The first-run setup wizard reappeared on every reload.** Skipping it (defer) only hid it for the
  current session. Both "completed" and "deferred" now suppress the auto-show across sessions.

### Added
- **Settings → Appearance → "AI Output Language".** Change a vault's generation language after
  creation (previously only settable in the new-project wizard); saves immediately via
  `PUT /vault/meta/output-language`. Distinct from the interface language.
- **D5 Home dashboard screenshot** + its Playwright capture spec.

### Upgrade notes
- No migration. The graph fix takes effect for new ingests; a single-vault graph is already correct.
  To reconnect an existing multi-vault graph, re-ingest the affected vault (links re-resolve to the
  right pages).

## [1.7.0] — 2026-07-14 — "llm_wiki 1:1 core parity, link-regression fix, editorial redesign"

Aligns the three core operations (Ingest · Review · Lint) and the generated page types 1:1 with
`nashsu/llm_wiki` v0.6.3, fixes the wikilink-density regression introduced in 1.6.0, brings the
new-vault onboarding to parity, and refreshes the entire frontend with a new "editorial knowledge
workspace" visual language. Behavior spec: `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md`;
decisions: ADR-0076..0083.

### Added
- **Block-based ingest pipeline** (ADR-0076, `ingest_pipeline_format` — now the **default**
  `"blocks"`, with `"json"` kept as a rollback lever) — a provider-neutral port of llm_wiki's
  two-stage text pipeline: markdown analysis → `---FILE:` / `---REVIEW:` block generation →
  schema-validated block writer. New modules
  `ingest/{prompts,blocks,sanitize,block_loop,block_writer,context,writer,pipeline}.py`. Providers
  gain a raw-text `complete()` transport (I6). In `"blocks"` mode **every** provider — Local, API,
  and the agentic CLI — runs the block loop via `complete()`; llm_wiki drives its CLI as a text
  transport, so the delegated agent loop (which dangled wikilinks) is used only in `"json"` mode.
- **Schema-driven page routing + open page-type set** (ADR-0077) — `wiki/schema.py` parses the
  `schema.md` "Page Types" table into an authoritative `type → dir` map; custom types (thesis,
  methodology, finding, goal, habit, character, …) persist as a raw `page_type` string.
- **New-vault onboarding wizard** (ADR-0081; migration 0032) — a 3-step modal (name + parent dir →
  mandatory AI output language → scenario template) that **auto-activates** the new vault. The 5
  scenario templates carry `extra_dirs` + custom Page Types; per-vault `vault_state.output_language`
  drives the ingest language directive. `GET`/`PUT /vault/meta/output-language`.
- **index.md "Recently Updated"** — a code-owned bounded catalogue section alongside the K3 per-type
  catalogue (ADR-0078); manual `POST /ops/overview/regenerate`.
- **Parity E2E harness** (ADR-0083) — deterministic 3-doc corpus + `scripts/parity_e2e/compare.py`
  (tolerance bands + a *total-links ≥ 1.5.6 baseline* regression sentinel) + runbook.
- **Editorial frontend redesign** — a new design-token language (ink `#0F1729`, cool neutrals, a
  legible categorical page-type palette, softer elevation), a grouped nav rail (Create / Understand /
  Maintain), an editorial Wiki reader with a Connections panel (server FA2 coords, I2), a Review
  decision-trace card, and a shared button/badge/mono-metadata kit. Light + dark, EN/IT parity.
- **Home dashboard, second pass** — a composition hero (total pages set large over a jewel-tone
  per-type bar + legend) replaces the flat page/data-version tiles; semantic KPI states (lint `0` →
  green "clean", pending review green when clear); the ingest quick-action is a primary button;
  review-preview rows carry a color-coded type chip and a primary Create. The graph legend hides
  zero-count node types.

### Changed
- **Wikilink density fixed at the prompt** — the regression was diagnosed as prompt-only (no link
  code changed in 1.6.0): the 6-type JSON scaffold had buried the single `[[wikilink]]` instruction.
  The ported prompts (`ingest/prompts.py`) restore prominent, repeated wikilink guidance and an
  analysis "Connections to Existing Wiki" section; the delegated **CLI** prompt shares the same rules
  (a contract test prevents drift) so the 1:1 E2E path links as densely as the reference.
- **Wikilink enrich post-pass defaults OFF** (ADR-0076) — llm_wiki produces links inline only
  (`enrich-wikilinks.ts` is dead code); the post-pass would double-count. One opt-in toggle away.
- **Review** (ADR-0079) — the auto-resolve sweep fires on **queue drain** (not per run); **Create
  Page** defaults to a deterministic stub (`mode="stub"`), with full-LLM generation as an explicit
  `mode="generate"`; Dismiss now confirms first; block-loop REVIEW blocks are enqueued.
- **log.md** entries use llm_wiki's `## [YYYY-MM-DD] ingest | Title` format. **overview.md** is
  regenerated once per **queue-drain batch** (ADR-0078 refinement) — a single whole-wiki synthesis
  that reads `purpose.md` + the existing-page digest — rather than per source (which would compete
  with entity/concept extraction) or never (which left it a stub).
- **Generation covers all derived page types** — the "what to generate" prompt now asks for query,
  comparison and synthesis pages from the analysis's open questions / commensurable subjects /
  cross-cutting conclusions, and for **one page per distinct** named entity or concept (specific
  subject over a generic umbrella), matching the reference's granularity.
- **Wikilinks are emitted as bare kebab-case slugs** (`[[cloud-cost-explorer-api]]`, with the
  `[[slug|Display]]` escape hatch) — the form the reference writes, so links resolve reliably.
- **Review LLM seams and the overview regen run through `complete()`** (single-turn), not the
  agentic `chat()` loop that hangs the CLI; their single-call timeouts are raised to 120 s to fit a
  CLI subprocess cold-start (degrade-safe — a slow/hung call keeps the previous state).
- **Lint** (ADR-0080) — assessed at parity (0.74 broken-link threshold, deterministic fixes,
  `proposal_origin="lint"` review routing); the bounded semantic loop and on-demand Send-to-Review
  are deliberate supersets (`lint_max_iter=1` reproduces the reference exactly). No lint code change.
- The orchestrator (3,851 → 1,493 lines) is decomposed behind a re-export façade.

### Fixed
- The 1.6.0 wikilink-density regression (see Changed).
- Onboarding no longer strands a new vault un-activated (the wizard activates + reloads).
- **Dangling wikilinks under the CLI provider** — the delegated agent linked to entities it never
  materialised (169 links, 1 graph edge, 0 entities); routing the CLI through the block loop lifts
  link resolution to ~90–100 % on the 1:1 E2E.
- **Block-loop non-convergence** — the validator counted app-managed `index/log/overview` FILE
  blocks (which the prompt asks the model to emit) as routing errors, forcing retries to `max_iter`;
  those blocks are now skipped in validation and every source converges on the first attempt.
- **overview.md stayed an empty stub** — regeneration used `chat()` (agentic CLI loop) and timed
  out; the drain-time `complete()` path now produces a rich synthesis.
- **Review auto-resolve did nothing under the CLI** — the sweep judge / proposal seams timed out at
  30 s via `chat()`; with `complete()` + a 120 s ceiling the sweep resolves items again.
- **Frontend showed the wrong vault** — the dashboard never synced `vault_id` from `/status`, so a
  non-default active vault mismatched every data list (the "13-badge / 2-item list" symptom); the
  status poll now propagates the active `vault_id`.

### Upgrade notes
- Run Alembic migration `0032` (additive, nullable `vault_state.output_language`; NULL = auto).
- `wikilink_enrich_enabled` now defaults **false**; set it true to restore the post-pass.
- **`ingest_pipeline_format` now defaults to `"blocks"`** (the E2E-verified parity path). Set it to
  `"json"` to roll back to the 1.6.x loop (removal slated for 1.8).
- Review/overview single-call timeouts default to **120 s** (`review_sweep_timeout_seconds`,
  `review_propose_timeout_seconds`, `overview_timeout_seconds`) to fit the CLI provider; lower them
  for API/Ollama-only deployments if desired.

## [1.6.0] — 2026-07-13 — "source-grounded generation lifecycle parity"

Major generation-quality release aligning Synapse's direct ingest, human Review and corpus-level
comparison/synthesis lifecycle with the useful behavior observed in `nashsu/llm_wiki`, while
retaining Synapse's provider neutrality, hard bounds and operator control.

### Added
- **Six-type direct generation** — shared orchestrated and delegated prompts can emit `entity`,
  `concept`, `source`, `query`, `comparison` and `synthesis` when the current source contains the
  required evidence. Query, comparison and synthesis have explicit source-grounding gates [F3].
- **Review provenance and type traceability** — migration 0031 adds `proposal_origin`; API and UI
  expose origin, proposed type and effective created type. Server-side filters compose with the
  existing status/pagination contract, and search-query quality is visible before acceptance [F9].
- **Idempotent corpus identity** — comparison/synthesis clusters use a stable member-path signature,
  indexed as `pages.generation_key` and persisted as `synapse_generation_key` in valid YAML.
  Repeated runs skip before inference; forced runs update the same deterministic file [F18].
- **Safe corpus controls and audit** — independent `max_candidates`, provider-free `review-only`
  mode, active-run polling/diagnostics, and `GET /ops/synthesize/audit` for a non-destructive legacy
  duplicate report [F18].
- **Per-run generation diagnostics** — ingest history records an optional six-type page count so
  provider/run discrepancies can be measured directly.

### Changed
- **Delegated Review is source-grounded** — the CLI route now passes bounded raw source text and
  bounded excerpts from only the pages written in that run; it no longer fabricates an Analysis
  object from titles. CLI turns and token usage honor configured boundaries at SDK messages [F3/F9].
- **Review budgets are independent** — deterministic rules are capped at 8, AI proposals at 12,
  and the merged queue at 20. A richer AI duplicate replaces its rule equivalent instead of being
  starved by missing-link noise [F9].
- **Corpus generation requires a real shared domain** — untagged or mixed-domain pages are counted
  and skipped rather than grouped into a global synthetic bucket. Home offers both Generate and
  Propose-only actions and reports duplicate/untagged diagnostics [F18].
- **Review works down to 320 px** — filter tabs are keyboard-operable; Deep Research becomes a
  responsive drawer instead of compressing the queue; EN/IT labels remain in parity.

### Fixed
- Force corpus runs can no longer create a duplicate when the model changes the generated title.
- Bare missing-link search queries now include local referrer/source context when available.
- Review filtering now happens on the server, so totals and pagination remain truthful.
- Corpus status polling stops at terminal state and on unmount instead of leaving stale activity.

### Upgrade notes
- Run Alembic migration `0031`. It is additive and does not rewrite or delete existing pages.
- Existing review rows are labeled `legacy`; existing corpus pages keep a null generation key.
- Use the audit endpoint before any manual legacy cleanup. v1.6.0 never deletes or merges reported
  duplicates automatically.

## [1.5.6] — 2026-07-13 — "write toggle, Marker auto-split, UI audit follow-ups"

Bundles two features (runtime remote-MCP write toggle, Marker `--auto` chapter split) with
usability + accessibility fixes from a UI audit of the live instance.

### Added
- **Remote MCP write tools are now toggleable from Settings** (ADR-0072). *Settings → API & MCP*
  gains a real switch that enables/disables the HTTP MCP write tools (`write_page`,
  `resolve_review`, `trigger_source_rescan`) at runtime — no more env-var edit + backend
  restart. Persisted in `vault_state.remote_mcp_write_enabled` (migration 0030, DB-wins-else-env
  precedence); exposed via `PUT /mcp/remote-write`; reflected in `GET /mcp/info`. Write tools are
  always registered but each guards on the runtime flag (always-register-guard). Token-floor
  clamp: enabling requires a configured token (or allow-without-token). `MCP_REMOTE_WRITE_ENABLED`
  remains the bootstrap default for fresh vaults [F17].
- **`--auto` mode for the ServiceNow Marker connector** (`tools/marker-converter`): derives
  module/feature codes from the PDF bookmark outline (no curated-map or `--module-title` presets),
  splits **every** module in the book, and defaults to one file per L2 chapter/group. Makes large
  multi-module exports (e.g. the 5000-page ITOM book) drop-and-forget in `--watch-dir` mode.

### Fixed
- **Projects page no longer 404s the API in production** — the nginx reverse-proxy regex
  (`frontend/nginx.conf.template`) listed every API prefix except `projects`, so in prod
  `GET /projects` fell through to `try_files … /index.html` and the SPA received `<!doctype…`
  instead of JSON — surfacing the raw `Unexpected token '<', "<!doctype "… is not valid JSON`.
  Added `projects` to the proxied prefixes; the list now matches `API_PREFIXES` in
  `vite.config.ts` again.
- **Raw technical errors are no longer shown to users** — a new reusable `ErrorState`
  component (friendly title + Retry + collapsible "Technical details" with a copy button)
  replaces bare exception text on the Projects page, the AI & Models settings section, and
  Search. A raw `500 Internal Server Error` / JSON-parse error now renders as a civil,
  retryable state with the raw detail tucked behind a disclosure.

### Changed
- **Localization gaps closed** — user-facing strings that bypassed i18n (`Loading graph…`,
  `Connections`, `Quick Start`, `Loading`, and backend status values such as `pending` /
  `cancelled by user`) are now routed through the i18n system with IT + EN translations, via
  a new `status.*` namespace and existing namespaces. `en.json`/`it.json` stay in structural
  parity (key-parity test green).
- **Search has reassuring loading/empty states** — the bare "Loading…" is replaced by a
  result skeleton; a "taking longer than expected" message with Cancel/Retry appears after
  ~4s; failures use `ErrorState`; and empty results show a helpful no-results state instead of
  a stuck view.
- **Legibility quick-wins** — sub-12px shared text classes (`.syn-chip`,
  `.syn-empty-state__eyebrow`, `.syn-meta-row`) raised to a 12px floor; muted-text tokens
  re-checked against WCAG AA (already passing after a prior pass — ratios recorded).

## [1.5.5] — 2026-07-13 — "remote MCP endpoint reachable again"

Patch: the remote MCP HTTP surface (`/mcp/server`) never actually served requests —
Claude Desktop / claude.ai / `mcp-remote` all got a 404. Two overlapping defects, both fixed.

### Fixed
- **`/mcp/server` was never mounted** — the R13-1 router split (`2bbe195`) dropped the
  `app.mount(MCP_MOUNT_PATH, _BearerAuthMiddleware(...))` block, so every remote MCP request
  hit a FastAPI routing 404 while `GET /mcp/info` still reported `http_enabled: true` (a
  misleading green light). The OpenAPI drift gate could not catch it because a `Mount()`
  sub-app is not an OpenAPI path. Restored the mount [F17].
- **Endpoint served at the wrong path** — `http_app()` defaults to `path="/mcp"`, so even once
  mounted the Streamable-HTTP endpoint sat at `/mcp/server/mcp`; a client POSTing to the
  documented `/mcp/server` would still 404. Now mounted with `http_app(path="/")` so the
  endpoint answers at the mount root (`/mcp/server`), matching the docs, the Settings UI
  snippet, and `/mcp/info.mount_path`. Clients may use `/mcp/server` (307 → canonical) or
  `/mcp/server/` directly [F17].

### Added
- **End-to-end MCP mount regression test** (`TestMcpServerMountedEndToEnd`) — POSTs an MCP
  `initialize` through the real ASGI stack with the FastMCP session manager started, asserting
  the handshake reaches the app (200), no-slash → 307 → canonical, wrong bearer → 401, and
  remote-disabled → 404. Closes the coverage gap that let the regression ship green [F17].

## [1.5.4] — 2026-07-12 — "llm_wiki 1:1 parity — ingest boundaries, fuzzy lint, review dedup"

Patch: closes five function-by-function divergences found auditing Synapse against
nashsu/llm_wiki (ingest, review, lint). All prompt/logic changes are provider-neutral (I6)
and covered by unit + integration tests.

### Added
- **Subject-boundary rules in the ingest prompts** — the analyze, generate, and re-ingest
  merge prompts now instruct the model to keep every claim, limit, evaluation, benchmark, and
  recommendation attached to the exact subject it describes, and never transfer them between
  subjects that merely share keywords (context window size, benchmark name, dataset,
  architecture). Direct port of nashsu/llm_wiki (ingest.ts:1949 / 2070-2072 / 2792-2793);
  prevents claim-bleed between entities. Reaches all three backends — the shared
  `GENERATION_SCAFFOLD` is injected into the delegated CLI agent's system prompt too [F3].
- **Delegated (CLI) route source-summary guarantee** — the delegated ingest route now runs the
  same deterministic "ensure exactly one source page" fallback the orchestrated route applies,
  as an additive post-run step (writes the fallback source page only when the agent omitted one,
  never mutates the agent's own writes). Mirrors llm_wiki's `hasSourceSummary` fallback
  (ingest.ts:1209-1244) [F3].
- **Mandatory output-language directive on the delegated route** — the CLI agent now receives an
  explicit "write page bodies and frontmatter `lang` in the vault language" instruction (from
  `overview_language`, the llm_wiki `targetLang` equivalent), so delegated pages no longer
  silently drift to English [F3].

### Fixed
- **Broken-wikilink suggestions now re-point instead of spawning stub pages** — the lint
  broken-link fix reused the exact→case→slug resolver that had already marked the link dangling,
  so a typo'd `[[Transformerz]]` never produced a suggestion and the apply path created a stub.
  Added a typo-tolerant fuzzy fallback (Levenshtein over the basename + same-basename/substring
  shortcuts, threshold 0.74) — a verbatim port of llm_wiki `suggestBrokenTarget`. Suggestion-only:
  it never creates a graph edge, so a wrong guess cannot pollute the graph [K2].
- **Review queue no longer bloats on re-ingest** — `confirm` items carried no dedup key and
  re-inserted on every re-ingest, piling up duplicate pending rows. They now dedup on
  (type + normalized title) like every other review type (llm_wiki `reviewIdFor` parity); the
  enqueue UPSERT still respects a human's terminal decision, so a resolved confirmation is never
  re-opened. Title-less confirmations stay always-insert (no false collapse). Supersedes the
  former "confirm never deduped" rule [F9].

### Changed
- **Review auto-resolve sweep exits early** — the Pass-2 LLM sweep issued all its batches even
  when a batch resolved nothing; it now stops after the first empty batch (llm_wiki
  sweep-reviews.ts:307-310 parity), cutting provider calls on a queue the conservative judge is
  keeping anyway (I7) [F9].

## [1.5.3] — 2026-07-11 — "Synthesize/comparison UI trigger"

Patch: exposes the corpus-level synthesis/comparison generator (`POST /ops/synthesize`,
ADR-0067 D3) in the Home dashboard — previously API-only with no UI trigger.

### Added
- **"Generate now" nudge for synthesis/comparison pages** — a new Home dashboard banner
  triggers the bounded corpus-level synthesis/comparison generator on demand. It runs the
  same deterministic 4-signal-graph cluster seeder as the backend op: high-confidence
  clusters are auto-written as synthesis (thesis + integration) or comparison (table) pages,
  borderline clusters are proposed to the F9 review queue. Hidden when the corpus has fewer
  than 3 entity/concept pages (the seeder's own minimum) or while a run is already in
  flight. A "Sintesi/confronti in corso" row surfaces in "LAVORI ATTIVI" while the run is
  running, matching the existing backfill/reclassify pattern [F18][ADR-0067 D3].

### Notes
- Still a manual, on-demand trigger — synthesis/comparison generation does NOT run
  automatically at the end of a bulk ingest. That auto-trigger (hooking `POST
  /ops/synthesize` into `ingest-all` completion) remains a follow-up, tracked separately.

## [1.5.2] — 2026-07-11 — "Provider config + UX fixes (live-verified)"

Patch: provider-config bugs that broke selecting/using the CLI provider (all **verified live against
real Postgres/asyncpg**, not just mocked tests), plus a few UX regressions.

### Fixed
- **Graph node click now opens the page** — clicking a node in the graph only showed an info
  tooltip; it never opened the corresponding wiki page. Clicking now selects the node and switches
  to the pages section (Obsidian-style), opening the page in NoteView [F4].
- **Overview / page "updated" line showed a raw microsecond ISO** (`…09:44:24.021477Z`) — now
  trimmed to a clean second-precision ISO (`…09:44:24Z`), matching the llm_wiki overview footer.
  The `log.md` content already used clean day/second timestamps; this covers its page view too [F16].
- **File drag-drop into Convert didn't work in the native Tauri (macOS) app** — Tauri v2 intercepts
  OS drag-drop by default, so the webview's HTML5 drop never fired. Set `dragDropEnabled: false` on
  the window so the drop zone receives files normally (PWA/browser were unaffected) [F15].
- **Convert now deletes the source PDF after producing the `.md`** — a Marker conversion left both
  the bulky PDF and its `.extracted.md` in `raw/sources/`; the PDF is now removed on success and
  `sources[]` points at the retained `.md` (best-effort delete never fails the conversion) [F12].
- **Review items came out in English, fewer, and terser on non-English vaults** — the review
  propose prompt was never language-aware (unlike page generation), the delegated/CLI route
  hardcoded `language="en"`, and rule-based rationales were English literals; **and** the anti-spam
  gate summed page *title* lengths (never reaching the char threshold), so the detailed LLM propose
  step was skipped whenever a run produced few pages and few dangling links. Now: the propose prompt
  carries a mandatory output-language directive (`analysis.language → overview_language`), rationales
  localise (IT/EN), the CLI route uses the vault language, and the gate uses real on-disk body sizes
  so the detailed proposals run and stay in the vault language [F9, F3].
- **`index.md`/`log.md` showed up as bogus automatic groups ("Synapse Index"/"Synapse Log")** — v1.5
  made them graph nodes (D4 parity), and being all-linking hubs they were the highest-degree member
  of their Louvain community, so they labelled the group (both in the graph and `/stats/groups`).
  Meta types (`index`/`log`/`overview`) are now excluded from community **labels + top-page
  previews** (they remain graph nodes and members). The files themselves are unchanged — still
  `index.md`/`log.md`; the displayed name was their frontmatter `title` [F18, F4].
- **`PUT /provider/config/{id}` → 500 `MissingGreenlet`** — the handler serialized the row after
  the UPDATE flush, but `updated_at` is server-side `onupdate=now()` and is expired at that point;
  reading it in the sync serializer triggered an async lazy-load outside a greenlet → 500 (seen when
  picking a model in Settings). Now `await session.refresh(row)` runs before serialization. Added a
  regression test for the previously **untested** PUT endpoint (asserts 200 + refresh awaited) [F17].
- **Ingest resolved the wrong provider → "No Anthropic API key" despite CLI configured** — the
  backend resolver (`_query_one`) selected a matching `provider_config` row with `LIMIT 1` and **no
  `ORDER BY`**, returning an arbitrary row. With two global rows (an older Anthropic `api` row and a
  newer `cli` row) it picked the stale `api` row, while the UI (`deriveActiveItem`, newest-wins)
  showed CLI active — so ingest demanded an Anthropic key. The resolver now orders by `created_at`
  DESC, so backend and UI agree that the newest configured provider is active. Regression test added
  (newest global row wins) [F17, I6].
- **Duplicate provider rows piling up + the header dropdown listing them all** — `POST /provider/config`
  always INSERTed, and since "active = newest row" every activation (header dropdown `setActive`,
  catalog toggle) created a new row, so identical providers accumulated (e.g. 3× "CLI / opus"). POST
  is now an **upsert**: it reuses a matching non-fallback row `(scope, vault_id, operation,
  provider_type, model_id, base_url)`, updating it and bumping `created_at` (so selecting a provider
  still activates it) instead of inserting a duplicate. The header **ProviderSelector** now
  **de-duplicates** its display (one row per identity, newest = active). Verified live vs Postgres:
  posting the same provider 3× yields **one** row, and re-posting flips it to active. Regression
  tests added [F17].

### Changed
- **More reviews out-of-the-box, for closer llm_wiki volume parity** — `REVIEW_PROPOSE_MIN_PAGES`
  default lowered `4 → 1` (the curated LLM review step now runs on ordinary single-page ingests
  instead of being gated out) and `REVIEW_PROPOSE_MAX_ITEMS` raised `8 → 12`. Both stay bounded and
  cost-capped by the resolved provider row's `token_budget`; tune via env for fewer/more [F9].

## [1.5.1] — 2026-07-11 — "CLI provider activation fix"

Patch: activating the **Claude Code CLI** provider (and any catalog vendor) from Settings failed
because the vendor-catalog tag couldn't be persisted. Also fixes an unreadable error toast.

### Fixed
- **CLI/vendor provider activation 422** — the Settings vendor catalog tags each `provider_config`
  row with its vendor id in the `operation` column (to disambiguate vendors that share
  `provider_type`+`base_url`, e.g. `claude-cli`/`codex-cli`, `anthropic`/`azure-openai`), but the
  `POST /provider/config` validator only accepted `{ingest, chat, lint}` and rejected vendor ids
  with **422**. The row was never created, so the toggle silently failed and model chips / Test
  buttons stayed inert. The validator now also accepts vendor-catalog ids [F17].
- **`422 [object Object]` toast** — FastAPI returns a 422 `detail` as an array of `{loc, msg, type}`
  objects; the client interpolated it directly. It now renders as readable `field: message` text.
- **Pre-activation provider Test** — "Test connessione"/"Test funzione" on a not-yet-activated vendor
  now include the vendor's default model in the inline probe, so they no longer 422 [F17].
- **0-preset vendors (`codex-cli`, `atlas-cloud`)** — activating a vendor with no preset models used
  to POST a null `model_id` (422). The toggle now reveals the Custom-model input instead, and
  choosing/typing a model creates the row (activation), so every catalog vendor is configurable [F17].
- **macOS menu-bar (tray) icon** — now the **white** Brand mark (`synapse-mark-white`, `tray-white.png`)
  on a transparent background, rendered as-is (not a template) so it shows white on the menu bar
  instead of the near-invisible dark ink [F15, Brand v1.0].

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

[1.8.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.7.4...v1.8.0
[1.7.4]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.7.3...v1.7.4
[1.7.3]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.7.2...v1.7.3
[1.7.2]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.7.1...v1.7.2
[1.7.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.6.1...v1.7.0
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
