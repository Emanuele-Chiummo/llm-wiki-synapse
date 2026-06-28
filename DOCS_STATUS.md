# DOCS_STATUS — Sprint v0.4 / M4-GUX Phase 0 Documentation Gate

> Tech-writer sign-off for M4-GUX Phase 0 docs gate.
> Generated: 2026-06-28
> Author: tech-writer (claude-sonnet-4-6)
> Supersedes: DOCS_STATUS.md (v0.3 / M3 gate — 2026-06-28)
> Sprint branch: sprint/v0.3 (transitioning to v0.4)
> Phase scope: GraphUX work — ADR-0016 (structural edges, per-edge kind), Feature A (node
>   pinning: pages.pinned + PATCH /pages/{id}/position), sigma.js viewer UX updates
> I8 gate: CLAUDE.md §3 invariant I8 (docs-as-DoD; ER matches live schema; OpenAPI matches
>   live FastAPI)

---

## 1. Per-artifact status table

| ID | Artifact | Required M4-GUX P0? | Status | Drift found | Action taken | Notes |
|----|----------|---------------------|--------|-------------|--------------|-------|
| D2 | `docs/er/schema.mmd` | YES | UP-TO-DATE | DRIFT FOUND — FIXED | Regenerated via `make er` | See §3 for detail. |
| D4 | `docs/api/openapi.json` | YES | UP-TO-DATE | DRIFT FOUND — FIXED | Regenerated via `make openapi` | See §4 for detail. |
| D7 | `docs/adr/README.md` (ADR-0016 index row) | YES | UP-TO-DATE | ZERO DRIFT (row already present) | Header timestamp updated | See §5 for detail. |
| D5 | `docs/screens/graph-obsidian.png` | REFERENCE ONLY | PENDING QA | N/A | Not captured by tech-writer | QA agent captures separately; see §2. |
| D1 | `docs/architecture/component.mmd` | NO (v0.4 update deferred) | CARRY-FORWARD | — | — | M3 version remains valid for Phase 0. v0.4 component diagram update scheduled when 3-panel UI lands. |
| D3 | `docs/sequences/` | NO (Phase 0 scope) | CARRY-FORWARD | — | — | graph-recompute.mmd from M3 remains valid. ADR-0016 edge-filter change is an engine-internal detail; sequence is unchanged. |
| D6a | `docs/USER.md` | NO (v0.4) | N/A | — | — | Not in Phase 0 scope. |
| D6b | `docs/DEPLOY.md` | NO (v0.4) | N/A | — | — | Not in Phase 0 scope. |

---

## 2. D5 screenshot reference — QA agent responsibility

The `docs/screens/graph-obsidian.png` screenshot is captured by the QA/test-engineer agent
running Playwright against the live stack. Tech-writer does NOT capture D5. This is the
established precedent (v0.3 DOCS_STATUS §2: "D5 capture DEFERRED-TO-LIVE").

Expected capture: `docs/screens/graph-obsidian.png` — graph viewer after ADR-0016 structural
edge filter, showing Obsidian-style topology (no hairball, nodes sized by structural degree,
edges styled by kind).

Status at time of this gate: `docs/screens/` directory exists and is empty (0 PNGs committed).
Gate verdict accounts for this: D5 is referenced but does not block Phase 0. It will be
confirmed committed in the sprint v0.4 D5 check (alongside the full 3-panel UI screenshots).

---

## 3. D2 ER diagram — drift found and fixed

### Drift description (pre-fix)

The committed `docs/er/schema.mmd` was generated at v0.3 / M3 and was missing two columns
added in migrations 0004 and 0005:

| Column | Table | Migration | Status before fix |
|--------|-------|-----------|-------------------|
| `edges.kind` | EDGES | 0004 (2026-06-28) | ABSENT from ER |
| `pages.pinned` | PAGES | 0005 (2026-06-28) | ABSENT from ER |

Additionally, the header comment read `<!-- Generated: v0.3 sprint 3 | 2026-06-28 -->`,
not reflecting the M4-GUX transition.

### Fix applied

Ran `/Users/emanuelechiummo/Desktop/LLM Wiki Project/.venv/bin/python backend/scripts/generate_er.py`
which introspects live SQLAlchemy models (`backend/app/models.py`) and regenerates
`docs/er/schema.mmd` from the authoritative source. Output confirmed by generator sanity check:
"all 6 tables present (PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS, EDGES)".

Header comment in generated file updated to:
`<!-- Generated: v0.3→v0.4 transition | 2026-06-28 — ADR-0016: edges.kind; Feature A: pages.pinned -->`

`backend/scripts/generate_er.py` line 69 updated to emit this header on future runs.

### Post-fix verification

| Table | Column | Present | Type | Comment accurate |
|-------|--------|---------|------|-----------------|
| PAGES | `pinned` | YES | boolean | "True when user manually positioned this node via PATCH /pages/{id}/position; preserved across FR recomputes (Feature A)." |
| EDGES | `kind` | YES | string | "Structural discriminator: link (wikilink) or source (provenance). ADR-0016 §4. NULL = link for pre-0004 rows." |

All 6 tables present. pages.x/y retained. Relationships (EDGES FK → PAGES) consistent with
models.py. **Zero drift vs models.py after fix.**

---

## 4. D4 OpenAPI — drift found and fixed

### Drift description (pre-fix)

The committed `docs/api/openapi.json` was generated at v0.3 / M3 and was missing the
M4-GUX additions:

| Missing element | Type | ADR/Feature reference |
|-----------------|------|----------------------|
| `PATCH /pages/{page_id}/position` path | New endpoint | Feature A — node pin/drag |
| `PatchPositionRequest` schema | New schema | Feature A |
| `PatchPositionResponse` schema (id, x, y, pinned) | New schema | Feature A |
| `GraphEdgeResponse.kind` field | New field | ADR-0016 §4 |
| `GraphEdgeResponse` description update | Doc update | ADR-0016 §4 |
| `GraphNodeResponse.size` description | Doc update | ADR-0016 §2 (sqrt formula) |
| `GraphNodeResponse.degree` description | Doc update | ADR-0016 §2/§4 (structural degree) |
| `GraphResponse` example `edges[0].kind` | Example update | ADR-0016 §4 |

### Fix applied

Ran `/Users/emanuelechiummo/Desktop/LLM Wiki Project/.venv/bin/python backend/scripts/generate_openapi.py`
which imports `backend/app/main.py` (FastAPI app) and regenerates `docs/api/openapi.json`.
Output confirmed by generator sanity check:
"all 5 required endpoints present (including GET /graph)".

Post-generation comparison against live API (`curl http://localhost:8000/openapi.json`) showed
exact schema match: identical paths, identical component schemas, identical `kind` field
definition in `GraphEdgeResponse`.

### Post-fix verification

| Check | Result |
|-------|--------|
| `PATCH /pages/{page_id}/position` path present | YES |
| `PatchPositionRequest` schema: required x, y | YES |
| `PatchPositionResponse` schema: id, x, y, pinned (all required) | YES |
| `GraphEdgeResponse.kind` field present | YES — type: string, default: "link", description references ADR-0016 §4 |
| `GraphEdgeResponse` description references ADR-0016 §4 | YES |
| `GraphNodeResponse.size` description: "BASE + GROWTH·sqrt(structural_degree)" | YES |
| `GraphNodeResponse.degree` description: "Structural degree…drives size (ADR-0016 §2/§4)" | YES |
| `GraphResponse` example edges include `"kind": "link"` | YES |
| Committed file == live API (`/openapi.json`): path set identical | YES — 8 paths, zero diff |
| Committed file == live API: schema set identical | YES — 15 schemas, zero diff |
| `info.version` | "0.3.0" (not yet bumped to 0.4.0; backend-engineer owns version bump) |

**Zero drift vs live FastAPI app after fix.**

---

## 5. D7 ADR index — ADR-0016 verification

File: `docs/adr/README.md`

### Pre-fix state

ADR-0016 row was already present in the index (authored by solution-architect). The header
line read `Last updated: 2026-06-28 · Sprint v0.3`, which did not reflect the M4-GUX transition.

### Fix applied

Updated header to: `Last updated: 2026-06-28 · Sprint v0.3→v0.4 (M4-GUX Phase 0)`

Updated narrative paragraph to include ADR-0016 description.

### ADR-0016 index row verification

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0016 | YES |
| Title | "Obsidian-style graph: structural edges, real-connection sizing, type-as-modulator (F4)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-28 | YES |
| Sprint | v0.3→v0.4 | YES |
| Link | `0016-obsidian-graph-rendering.md` | YES — file exists at `docs/adr/0016-obsidian-graph-rendering.md` |
| Summary | Structural-only edges, ADR-0012 superseded §3, sqrt sizing, per-edge kind | YES — accurate |

### ADR-0016 content verification (spot-check)

| Section | Present | Content accurate |
|---------|---------|-----------------|
| Context | YES | Describes hairball defect; same-type clique math; user goal |
| Decision §1 | YES | Structural edges = direct link OR shared source; AA/same-type = modulators |
| Decision §2 | YES | size = BASE + GROWTH·sqrt(structural_degree); BASE=1.0, GROWTH=1.0 |
| Decision §3 | YES | FR layout fed structural edge set with modulated weights |
| Decision §4 | YES | Per-edge `kind` ("link"|"source"); `degree` = structural_degree |
| Decision §5 | YES | Exact change list for backend-engineer (engine.py + main.py) |
| Decision §6 | YES | ADR-0012 reconciliation: §3 superseded, §1/§2 weight formula retained |
| Consequences | YES | Lists +/- outcomes including D5 screenshot regeneration note |

ADR-0016 file is consistent with models.py (edges.kind column added in migration 0004),
with openapi.json (GraphEdgeResponse.kind field), and with the ER diagram (edges.kind row).

**Total ADRs in index: 16 (0001–0016). All Accepted. Zero gaps.**

---

## 6. Cross-consistency sweep (M4-GUX Phase 0)

| Check | Result |
|-------|--------|
| `pages.pinned` in ER matches `models.py` `Page.pinned` (Boolean, NOT NULL, server_default false, migration 0005) | PASS |
| `edges.kind` in ER matches `models.py` `Edge.kind` (String, nullable, migration 0004) | PASS |
| `PATCH /pages/{page_id}/position` in openapi.json matches live backend (curl confirms 200 schema) | PASS |
| `GraphEdgeResponse.kind` in openapi.json matches ADR-0016 §4 ("link"\|"source" discriminator) | PASS |
| `GraphNodeResponse.size` description (sqrt curve) matches ADR-0016 §2 formula | PASS |
| `GraphNodeResponse.degree` description (structural degree) matches ADR-0016 §2/§4 | PASS |
| ADR-0016 edge inclusion rule (structural gate) consistent with ADR-0012 reconciliation note in ADR-0016 §6 | PASS |
| ADR-0012 §3 superseded status documented in ADR-0016 §6 and README summary | PASS |
| `docs/adr/0016-obsidian-graph-rendering.md` exists and is non-empty | PASS |
| ER header comment updated to reflect M4-GUX transition | PASS — "v0.3→v0.4 transition | 2026-06-28 — ADR-0016: edges.kind; Feature A: pages.pinned" |
| generate_er.py header string updated to match | PASS |
| D5 screen reference (graph-obsidian.png): QA agent responsibility, not tech-writer | PASS — noted in §2, not blocking gate |
| I2 invariant: no client-side layout in any diagram or doc | PASS — unchanged; ADR-0015 untouched |
| I8: ER matches live SQLAlchemy models after regeneration | PASS — zero drift |
| I8: openapi.json matches live FastAPI app after regeneration | PASS — zero drift |

**No contradictions found across ER / OpenAPI / ADR-0016 / models.py / migrations 0004–0005.**

---

## 7. Files modified by this gate run

| File | Action | Reason |
|------|--------|--------|
| `docs/er/schema.mmd` | Regenerated via `make er` + header updated | DRIFT: missing pages.pinned (migration 0005) and edges.kind (migration 0004) |
| `docs/api/openapi.json` | Regenerated via `make openapi` | DRIFT: missing PATCH /pages/{id}/position, PatchPositionRequest/Response schemas, GraphEdgeResponse.kind |
| `backend/scripts/generate_er.py` | Header string updated (line 69) | Header was "v0.3 sprint 3"; updated to "v0.3→v0.4 transition …" |
| `docs/adr/README.md` | Header timestamp + narrative paragraph updated | Header said "Sprint v0.3"; ADR-0016 row was present; narrative lacked ADR-0016 description |
| `DOCS_STATUS.md` | Full rewrite (this file) | Supersedes M3 gate; Phase 0 verdict |

---

## 8. DOCS GATE VERDICT — M4-GUX Phase 0

| Artifact | Status | Detail |
|----------|--------|--------|
| D2 `docs/er/schema.mmd` | UP-TO-DATE (drift fixed) | pages.pinned + edges.kind now present; header updated; zero drift vs models.py |
| D4 `docs/api/openapi.json` | UP-TO-DATE (drift fixed) | PATCH /pages/{id}/position + PatchPositionRequest/Response + GraphEdgeResponse.kind all present; zero drift vs live API |
| D7 ADR-0016 row in `docs/adr/README.md` | UP-TO-DATE | Row was present; index header updated to M4-GUX; 16 ADRs listed, zero gaps |
| D5 `docs/screens/graph-obsidian.png` | PENDING QA | QA agent captures separately; not blocking Phase 0 gate |

**DOCS GATE: PASS**

All required M4-GUX Phase 0 D-artifacts are UP-TO-DATE after drift correction. D5 is
a QA-agent responsibility (Playwright capture against live stack) and is explicitly tracked
as pending — it does not block this gate.

Drift found and fixed in this run:
- D2: `pages.pinned` and `edges.kind` were absent from the committed ER diagram.
- D4: `PATCH /pages/{page_id}/position` endpoint, `PatchPositionRequest/Response` schemas,
  and `GraphEdgeResponse.kind` field were absent from the committed openapi.json.

Both artifacts now match the live schema (models.py / migrations 0004–0005) and the live
FastAPI app respectively.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4-GUX Phase 0**
