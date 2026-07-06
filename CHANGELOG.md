# Changelog

All notable changes to Synapse are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Full, per-release notes live under [`docs/release-notes/`](docs/release-notes/) and on
the [GitHub Releases](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases) page.

## [Unreleased]

- `fix(ops)`: the automations card now reports the true classification outcome
  (`dormant` / `error` / counts) instead of a blind "ok" (#1).

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

## [1.3.0] — 2026-07-05 — "Foundations" 🏗️

The sprint that pays down structural debt before multi-vault (v1.4 → 2.0). No new AI
features by design. First release cut from `main` under the new tagging policy.

### Changed
- `main.py` decomposed from 9,311 → ~1,400 lines across 13 domain routers; API contract
  frozen and proven (byte-identical OpenAPI).
- Release lineage realigned: v1.2.4–1.2.6 merged into `main`; "tags are cut only from
  main" rule documented in CONTRIBUTING.

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

### Added
- Responsive mobile/tablet/desktop layouts (ADR-0057): drawers, safe-area insets, `100dvh`,
  and touch-reactive interactions (no ~350ms tap delay, ≥44px targets).

## [1.2.0] — 2026-07-03 — "Home & Insights"

Home dashboard and per-domain section insights (F18), in-app type reclassification, and a
run of Home/classification fixes across the 1.2.x patch line.

## [1.1.0] — 2026-07-03 — "Convert & Configure"

Multi-format conversion pipeline and in-app provider/model configuration; Chrome web
clipper 1.1.0.

## [1.0.0] — 2026-07-03 — "Distribution" 🎉

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

[Unreleased]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.3...HEAD
[1.3.3]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/Emanuele-Chiummo/llm-wiki-synapse/compare/v1.2.6...v1.3.0
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
