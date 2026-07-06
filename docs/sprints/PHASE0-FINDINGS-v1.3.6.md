# Phase 0 — Live Diagnostic Findings (v1.3.6)

> Senior-tester Chrome session driven against the live app (frontend on :5300, backend
> :8000 + Postgres + Qdrant, real `default` vault: 986 pages, dataVersion v1005, 109 review
> items, 32 communities). Evidence = network capture + DOM inspection + response bodies.
> Date: 2026-07-06.

## P0-1 — WS-B Review "In attesa" vs "Risolti" — CONFIRMED (reframed)

**User report:** the "In attesa" (pending) and "Risolti" (resolved) tabs show the same result.

**What is NOT the bug (ruled out with evidence):**
- Backend is correct. Direct calls returned *different* data per status:
  - `status=pending` → total **109**, all items `status:"pending"`.
  - `status=resolved` → total **2**, items `status:"auto_resolved"`.
  - `status=dismissed` → total **0**.
  - `pendingEqResolved: false` (first-id sets differ).
- Frontend store/tab-switching is correct. Clicking tabs re-renders the right counts:
  `In attesa → 11 rendered (virtualized, of 109)`, `Risolti → 2`, `In attesa → 11`, `Ignorati → 0`.

**The actual defect (frontend, presentation/logic):**
Resolved (and dismissed) items render with the **identical card UI used for pending proposals**:
- same header prompt *"Proposte dell'AI — crea una pagina, avvia una ricerca approfondita o salta."*
- same primary actions **Crea / Salta / Ignora / Ricerca Profonda**
- **no resolution badge/status** on the card (no "auto-risolto", no "creato il…", no link to the created page).

Consequences:
1. The two tabs are visually indistinguishable → the user perceives "same result" (aggravated by near-identical titles from the same source cluster, e.g. *"Cloud Licensing Cost Extraction Business Case"* [pending] vs *"…Validation"* [resolved]).
2. Offering **Crea / Ricerca Profonda** on an already-resolved item is a logic bug (re-creating an already-created/auto-resolved page).

**Fix direction (WS-B, F9):** give resolved/dismissed items a distinct card state — resolution badge + timestamp + link to created page; replace primary actions with a read-only/"reopen" affordance. Verify no StrictMode refetch race in prod build.

## P0-2 — WS-D(7) Wiki note scroll/overlap — CONFIRMED (nested scroll, affects ALL pages)

User clarification: *"tutte le pagine, se scorri, il testo finisce sopra la sezione con titolo/sorgenti"* — on every page, scrolling makes the body text collide with the metadata header (title / SORGENTI / CORRELATE). This is NOT raw frontmatter.

**Ruled out:** frontmatter is correctly stripped (`stripLeadingFrontmatter`, `renderMarkdown.ts:190`; robust regex handling CRLF, leading whitespace, duplicate legacy blocks, `...` fence). No raw YAML renders in the body.

**Confirmed root cause (live DOM inspection, NoteView):**
- Metadata header (title, type chip, `aggiornato:`, tag chips, SORGENTI, CORRELATE) is a `position:static` flex sibling, **height ≈ 379px = 46%** of the 824px note area.
- `.note-view__body` is a **separate inner scroll pane** (`overflow:auto`, ≈445px, `top:427`).
- The ancestor wrapping both (`anc1`) **also** has `overflow:auto` → **two nested scroll containers**.
- Result: depending on pointer position you scroll either the inner body or the whole card; the oversized static header + double-scroll makes body text visually collide with the SORGENTI/CORRELATE block, with no divider. Reproduces on every page.

**Fix direction (WS-D7, K6/I5):** collapse to a SINGLE scroll container for the note — either let the header scroll with the content, or make it a proper `position:sticky; top:0` header (solid background + z-index) above one scroll pane; remove the redundant inner/outer `overflow:auto` nesting. Consider making the tall metadata header collapsible (it currently eats ~half the viewport). Confirmed — no longer conditional.

## P0-3 — WS-D(8) schema.md / purpose.md missing — CONFIRMED (design limitation)

- Backend `GET /pages` is DB-driven; `schema.md`/`purpose.md` are written at bootstrap but never ingested as Page records, so they never appear in the tree. Confirmed by exploration.
- Fix: a "Vault / Meta" tree node reading the two fixed-path files from disk (small endpoint, no Postgres write). No repro needed.

## P0-4 — WS-A real-time freshness — CONFIRMED

- `GET /ingest/queue` **is** polled live (observed repeated calls via ActivityBar, ~1.5s/5s).
- `stats/overview`, `stats/sections`, `stats/groups`, `status`, `graph` are fetched **once on mount**; no polling. Home KPI/section cards and Graph therefore go stale until manual refresh.
- Fix: lightweight `dataVersion` polling → invalidate on bump (I2/I3, no WebSocket, no client layout).

## P0-5 — WS-C ingest progress — CONFIRMED (data already present)

- `/ingest/queue` snapshot exposes keys: `paused, pending, processing, failed, completed_since_idle, total, tasks, batch`.
- `batch` is `null` at idle; populated during bulk "index all" (ADR-0046). `tasks[]` carry per-run phase/progress/eta.
- Fix: surface overall % + ETA in the Home "Lavori attivi" widget, handling both single-file (`tasks[]`) and batch (`batch`) modes. No backend change.

## P0-6 — WS-G automations — SMOKE ONLY (needs controlled functional run)

- `GET /ops/schedules` → 200; all 4 ops (`lint, backfill, schema_review, reclassify`) are `schedule:"off"`, `last_run_at:null` (never run on this vault).
- Endpoint healthy, but "works after v1.3.5" is unproven until each op is run and returns a clean `last_status`.
- Plan: `lint` run-now as free smoke; `backfill/schema_review/reclassify` in a controlled QA run (provider cost, I7). Owned by qa-test-engineer.

## Environment note
- Instrumented dev instance added as `frontend-preview` (port 5300) in `.claude/launch.json`; the existing `frontend` (5199) from another session was left untouched.
