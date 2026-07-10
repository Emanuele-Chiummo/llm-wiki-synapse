# v1.4 — Visual Divergence Audit (Synapse vs LLM Wiki v0.6.0)

> Live, page-by-page comparison performed 2026-07-10 with both desktop apps open
> (`ai.synapse.app` vs `com.llmwiki.app`) — not from stale docs. Legend:
> 🟰 at parity · ➕ Synapse extra (kept, per "dove c'è di più va bene") · ➖ was missing → added in v1.4 · 🔀 diverged → aligned in v1.4.

## Verdict

**Synapse reaches the "UI 1:1 with LLM Wiki" goal.** The shared shell (light/card theme, per-type
colors, Lucide icons, 3-panel + nav rail) already matched from the v1.3.x parity work; v1.4 closed the
remaining real gaps (provider vendor catalog, CLI auth placement, in-app changelog, tray icon, async
import) and confirmed that several assumed gaps were **already done** (chat multimodal/web/retrieval,
README/repo-meta). Remaining differences are Synapse's **intentional extras**, kept on purpose.

## Global
- 🟰 THEME: LLM Wiki default light; Synapse dev/preview light too (the app can be dark via Aspetto — a preference, not a divergence).
- 🟰 LEFT RAIL: same core items (Chat · Wiki · Sources · Search · Graph · Lint · Review · DeepResearch · Settings). ➕ Synapse adds Home (dashboard), Import + Convert (marker).

## Per view
| View | Status | Notes |
|---|---|---|
| **Home / Panoramica** | ➕ Synapse-only | System dashboard (health chips + stat cards). LLM Wiki has none. Keep (F18). |
| **Chat** | 🟰 at parity | Composer already has Attach image (multimodal), Web toggle (SearXNG), retrieval segmented Veloce/Standard/Profondo/Locale-prima. Only LLM-Wiki-only bits: AnyTXT (Windows tool, N/A) + Skills (minor). ➕ Synapse adds wiki-specific suggested prompts. |
| **Settings** | 🔀→🟰 aligned | Synapse uses GROUPED nav (kept — better than LLM Wiki's flat 15). **LLM Models** rebuilt v1.4 to the one-row-per-vendor catalog (toggle, API key, model chips, context, reasoning, provider tests) — keys encrypted at rest. API+MCP present. CLI + Codex auth co-located inside their vendor rows. ➕ extras: Costi, Scenari, Web clipper, Sicurezza, CLI-subscription auth. ➖ added: in-app **Changelog** (expandable per-version cards, top 10). |
| **Graph** | 🟰 at parity | v1.3.13/14 closed it: labeled toolbar, community drill-down, index/log legend, insights-expanded, full Filters panel; 1:1 generation + Tailwind-400 node colors. |
| **Review** | 🟰 at parity | v1.3.14: per-type icons, Approve (confirm), ✕ dismiss, Deep Research side panel. |
| **Sources** | 🟰 | Virtualized tree, import/refresh, per-row ingest + two-stage delete, preview dispatcher, ingested badge + derived pages. |
| **Search** | ➕ / 🟰 | Dedicated semantic search with TYPE filters (Concetto/Entità/Sorgente/Sintesi/Confronto/Query) + relevance sort. Clean. |
| **Convert (Marker)** | ➕ Synapse-only | Marker PDF panel — v1.4 made it async (progress %, per-file status, history + Open, drag-drop fix). LLM Wiki has no marker panel. |
| **Lint / Deep Research** | 🟰 | Aligned in v1.3.x parity (lint categories/fixes; deep-research loop + synthesis). |
| **Desktop shell** | ➖ added | v1.4 adds a macOS **menu-bar (tray) icon** (Open/Quit + click-to-show), present when open or minimized. |

## Residual (optional polish, low value)
- Chat "Skills" toggle (llm_wiki-specific) — not ported; decide if wanted.
- Fine visual micro-tuning (spacing/typography) per view — the core look already matches; no blocking items found in the live pass.

*Compiled during the v1.4 program — see the CHANGELOG `[Unreleased]` section and PR #14.*
