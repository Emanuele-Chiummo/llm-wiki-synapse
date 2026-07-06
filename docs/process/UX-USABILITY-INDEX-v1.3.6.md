# Synapse — UI Usability & Intuitiveness Index (baseline @ v1.3.6)

> Heuristic evaluation from a live senior-tester session on the running app (real vault:
> 986 pages, 32 communities, 109 review items). Scores are 1–5 (5 = excellent). This is a
> planning baseline to prioritise UX work across v1.3.6 → v1.3.7+. Date: 2026-07-06.
>
> Each heuristic notes what v1.3.6 already improved and what remains.

## Overall index: **3.3 / 5** — "capable but expert-oriented"

The app is feature-dense and internally consistent for a power user who knows the model
(vault / ingest / review / graph). The friction is concentrated in **discoverability**,
**naming**, and **settings information architecture** — i.e. the gap between "works" and
"a non-expert can find and understand it". v1.3.6 closed the biggest **feedback** and
**consistency** gaps; v1.3.7 (settings restructure + domain wizard) targets the rest.

| # | Heuristic | Score | Trend after v1.3.6 |
|---|-----------|:-----:|--------------------|
| H1 | Visibility of system status (freshness, progress) | 4.0 | ▲ from 2.5 (WS-A real-time, WS-C ingest %/ETA) |
| H2 | Match to real world / naming clarity | 2.5 | — |
| H3 | Consistency & standards | 4.0 | ▲ from 3.0 (WS-B distinct review states) |
| H4 | Error prevention | 3.5 | ▲ from 2.5 (WS-B: no invalid actions on resolved items) |
| H5 | Recognition over recall / discoverability | 2.5 | ~ (WS-D8 surfaced meta files, but buried) |
| H6 | Flexibility & efficiency | 3.5 | — |
| H7 | Aesthetic & minimalist design / reading comfort | 3.5 | ▲ from 2.5 (WS-D7 note header no longer eats 46%) |
| H8 | Settings information architecture | 2.5 | — (WS-F deferred to v1.3.7) |
| H9 | Help & onboarding | 3.0 | — |

## Per-heuristic findings (evidence-based)

### H1 — System status · 4.0 ▲
- **Fixed in v1.3.6:** Home/Graph now auto-refresh on `dataVersion` bump (was stale until manual reload); the "Lavori attivi" widget now shows overall % + ETA instead of a bare "3 in corso".
- **Remaining:** the ingest progress widget only appears while a batch is active — no persistent "last run finished" affordance. Consider a brief completion toast.

### H2 — Naming clarity · 2.5 ⚠ (top-priority, cheap)
- **"Cerca" vs "Ricerca"** are two different nav items (wiki search vs deep research) with near-synonymous Italian labels — a classic confusion. Rename one (e.g. "Ricerca" → "Ricerca approfondita" / "Deep Research").
- "Sorgenti" (sources) vs "Import" vs "Converti" — three ingestion-adjacent entries; their distinction isn't obvious pre-click.
- **Recommendation:** a naming/terminology pass across the nav rail; group ingestion actions.

### H3 — Consistency · 4.0 ▲
- **Fixed in v1.3.6:** review resolved/dismissed items now render a distinct dimmed card with an `AUTO-RISOLTO` badge instead of looking identical to pending proposals.
- **Remaining:** the review section subtitle ("Proposte dell'AI — crea una pagina…") still shows on the Risolti/Ignorati tabs, where creation is not the action. Make the subtitle tab-aware.

### H4 — Error prevention · 3.5 ▲
- **Fixed in v1.3.6:** resolved items no longer expose Crea / Ricerca Profonda (which would re-create an already-created page).
- **Remaining:** destructive actions (cascade delete) — confirm the confirmation copy states shared-entity preservation clearly.

### H5 — Discoverability · 2.5 ⚠ (top-priority)
- The new **Vault / Meta** section (schema.md, purpose.md) sits at the very bottom of a virtualised tree — invisible until you scroll past 400+ concept nodes. Consider pinning it to the top, or a dedicated affordance.
- **Domain backfill** is a hidden manual step: setting the domain vocabulary doesn't tell the user they must trigger a backfill to tag existing pages (feeds v1.3.7 WS-E: end-of-ingest auto-backfill + visible "Regenerate").
- **Group/domain creation** is a comma-separated text field buried in settings (feeds v1.3.7 WS-E wizard).
- Clicking a domain/group on the Home dashboard navigates to Wiki with a localStorage filter, but there is no visible filter chip explaining why the list changed (feeds v1.3.7 WS-E: land in Search with a visible filter).

### H6 — Flexibility & efficiency · 3.5
- Command palette exists (good for power users). Resizable panels. Keyboard shortcuts present.
- **Remaining:** the meta files open in a read-only drawer (v1.3.6) — fine; a future "open in center pane" option would help longer reads.

### H7 — Reading comfort · 3.5 ▲
- **Fixed in v1.3.6:** the note metadata header (title/tags/sources/related) no longer occupies ~46% of the pane and no longer double-scrolls; it's a compact sticky header with a collapsible detail tier. Reading area is now generous.
- **Fixed in v1.3.6:** meta-file drawer now uses the shared prose stylesheet (tables/lists/code render correctly).

### H8 — Settings IA · 2.5 ⚠ (v1.3.7 WS-F)
- 17 pages across 6 groups. To wire the CLI provider you select it under "Provider LLM" but generate its token under "API e MCP" — one task, two tabs.
- No configured/missing state indicators; dense for a non-expert.
- **Deferred to v1.3.7 (WS-F, full restructure):** per-provider end-to-end config in one place, plain-language descriptions, base/advanced modes, configured/missing badges.

### H9 — Onboarding · 3.0
- A first-run wizard exists (FirstRunWizard) and a re-openable "Configurazione guidata". Good baseline.
- **Remaining:** no contextual empty-state guidance in secondary sections; no inline explanation of the vault/raw/wiki model for newcomers.

## Prioritised backlog for future releases

| Priority | Item | Heuristic | Effort | Target |
|:--------:|------|-----------|:------:|--------|
| P1 | Rename "Cerca"/"Ricerca"; group ingestion nav items | H2 | S | v1.3.7 |
| P1 | Pin Vault/Meta section to top of tree | H5 | S | v1.3.7 |
| P1 | Settings restructure (per-provider, plain language, base/advanced) | H8 | L | v1.3.7 (WS-F) |
| P1 | Domain/group wizard + visible search filter + auto-backfill | H5 | L | v1.3.7 (WS-E) |
| P2 | Tab-aware review subtitle | H3 | S | v1.3.7 |
| P2 | Ingest completion toast / persistent last-run status | H1 | S | v1.3.7 |
| P3 | Contextual empty-states + vault-model explainer | H9 | M | v1.4 |
| P3 | "Open meta file in center pane" option | H6 | S | v1.4 |

## Method & caveats
- Heuristic (expert-inspection) evaluation, not a user study. Scores are directional.
- Evaluated on desktop (1440×900) and the default narrow layout; a dedicated mobile pass is recommended separately.
- v1.3.6 fixes (WS-A/B/C/D7/D8) were verified live; their score bumps reflect the shipped state.
