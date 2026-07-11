# ADR-0067 — Multi-vault: Project Launcher & runtime active-vault switch (v1.5 P2)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Sprint:** v1.5 — "LLM Wiki 1:1 parity", phase P2
- **Extends:** ADR-0066 (parity program). Retires the v1.0 "single-vault single-owner" posture
  (`SYNAPSE-VS-LLMWIKI-PARITY.md`) — but incrementally, not by a rewrite.
- **Reference:** live map §14 (`docs/reference/LLMWIKI-LIVE-UI-MAP-2026-07-10.md`) — LLM Wiki's
  ⇄ bottom-rail **Project Launcher** (New Project / Open Project / Recent Projects); each project
  = a vault folder; the HTTP API exposes `/api/v1/projects`.

---

## 1. Context

Synapse must mirror LLM Wiki's multi-vault UX: a launcher to create/open/switch **projects**,
each project being a vault folder. Today Synapse serves **one** vault fixed at startup:
`settings.vault_id="default"` + `settings.vault_path="vault"` → `vault_root`. The watcher watches
one root; the graph cache and Qdrant are keyed to the running vault.

**Crucial enabler:** the data layer is **already vault-scoped** — `pages`, `vault_state`,
`ingest_runs`, `lint_runs`, etc. carry a `vault_id` column, and `vault_state` is one row per
`vault_id`. So multiple vaults can coexist in the same Postgres/Qdrant, distinguished by
`vault_id`; what's missing is the notion of an **active** vault and the machinery to switch it.

## 2. Decision

Adopt **Model A — single active vault, runtime-switchable**, matching LLM Wiki (one project open
at a time). NOT a per-request multi-tenant model (Model B, rejected §4).

### 2a. Project registry
A persisted list of projects lives OUTSIDE any single vault, at
`~/.synapse/projects.json` (override: `SYNAPSE_STATE_DIR`). Each entry:
`{ id, name, path (abs vault root), created_at, last_opened_at }`. `id` is a stable slug/uuid used
as the `vault_id` for that project's rows. The file also records `active_id`.
Read/written by a small `app/projects.py` service (bounded, no DB — filesystem state, like the
MCP/clip token config precedent).

### 2b. Endpoints (`/projects`)
- `GET /projects` → list + which is active (mirrors LLM Wiki `/api/v1/projects`).
- `POST /projects/open` `{path}` → register an existing vault folder (must contain `wiki/` or be
  scaffoldable); does NOT switch.
- `POST /projects` `{name, path}` → **create**: scaffold `raw/`, `wiki/`, `purpose.md`, `schema.md`
  (reuse the existing bootstrap in `app/vault.py`), register, return entry.
- `POST /projects/{id}/activate` → **switch active vault** (the hard part, §2c).
- `DELETE /projects/{id}` → forget from the registry (NEVER deletes files on disk).

### 2c. Runtime active-vault switch
`activate` performs, in order, bounded and logged:
1. Update the runtime config: `settings.vault_id = id`, `settings.vault_path = entry.path`
   (a runtime override layer — the env value remains the boot default).
2. Restart the watcher on the new `vault_root` (stop current observer, start on new root).
3. Invalidate the graph cache (force recompute for the new `vault_id`).
4. Seed `vault_state` for the new `vault_id` if absent (idempotent, ADR-0005).
5. Bump a global `active_vault_epoch` the frontend reads to hard-reload its stores.
Qdrant: continue to filter by `vault_id` (or per-vault collection — decided in the P2 impl slice,
kept behind the embeddings seam). No cross-vault data mixing: every query already filters `vault_id`.

### 2d. Frontend
- ⇄ **Project Launcher** entry at the very bottom of the NavRail → full-screen launcher: title +
  **New Project** (folder picker → name) · **Open Project** (folder picker) · **Recent Projects**
  (name + path, click → activate). On activate, reload the app against the new active vault.
- Folder picking: Tauri dialog in desktop; in web, a path input (self-hosted server-side path).

## 3. Consequences
- True multi-vault with **no schema change** — rides the existing `vault_id` columns.
- One active vault at a time (LLM-Wiki-faithful); switching is a bounded runtime operation, not a
  process restart.
- The registry is a new **filesystem** state file — must be backed up alongside the vault(s).
- Boot still uses env `VAULT_ID`/`VAULT_PATH` as the default active project (back-compat: existing
  single-vault deploys keep working; the registry is seeded from the boot vault on first run).

## 4. Alternatives considered
- **Model B — per-request multi-tenant** (`vault_id` on every request, no active concept).
  Rejected: far larger surface (every route, MCP, watcher-per-vault, auth), and LLM Wiki itself is
  single-active — no user benefit for the parity goal.
- **Process-per-vault / restart to switch.** Rejected: slow, breaks the desktop "switch project"
  UX; the watcher + cache can be re-pointed in-process.

## 5. Rollout (P2 slices)
1. `app/projects.py` registry + `GET /projects` (read-only; seed from boot vault). ← first slice
2. `POST /projects` (create+scaffold) + `POST /projects/open`.
3. `POST /projects/{id}/activate` — runtime switch (watcher restart + cache invalidation + epoch).
4. Frontend launcher + ⇄ rail entry + active-vault reload.
Each slice: tests + gates; the switch slice also gets an integration test.
