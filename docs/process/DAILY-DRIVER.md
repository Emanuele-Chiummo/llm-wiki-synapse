# Synapse — Daily Driver (autonomous improvement loop)

> **What this is:** the durable playbook a scheduled, no-human-input daily session reads to
> pick and ship ONE bounded block of work — advancing nashsu/llm_wiki parity **and** bug-fixes —
> committing it to a **weekly integration branch**. It is the single source of truth for the loop.
>
> **Who runs it:** a scheduled session (see §6) fires at ~07:03 local daily, follows this file
> top-to-bottom for one block, commits+pushes (no PR), then stops. No human input required.
>
> **Cadence & owner gate (owner decision 2026-07-11):** daily work accrues; **release is WEEKLY.**
> A Friday ~18:00 run consolidates the week into ONE release PR (bump + CHANGELOG + `release-cut`).
> The job NEVER merges — the owner reviews & merges that single weekly PR. DoD "human checkpoint"
> (CLAUDE.md §8) preserved, without a daily PR flood.

---

## 1. Golden rules (parsimony first — I7)

1. **ONE block per day.** Never start a second block. If today's block finishes early, stop.
2. **Reason in blocks.** If the queued item is too big for one bounded session, SPLIT it: do only
   the first sub-block today, append the remaining sub-blocks to the queue (§3), and stop.
3. **Bounded cost.** Target ≤ ~150k output tokens / run. Log `total_cost_usd`. If a step blows the
   budget, checkpoint what's done, open a draft PR, and stop — do not push through.
4. **Query, don't re-read.** Use `graphify query "..."` / `graphify explain "Symbol"` /
   `graphify affected "Symbol"` to locate code instead of reading many files. That is the whole
   point of the memory graph.
5. **Cheapest capable model.** Follow the routing table (§4). Opus is the exception, not the default.
6. **Never touch invariants silently.** Any change that risks I1–I9 (CLAUDE.md §3) → escalate to
   `solution-architect` before coding.

---

## 2. The daily procedure (the session executes these in order)

**Phase 0 — Boot & memory** (Haiku-class, effort low)
- `export PATH="$HOME/.local/bin:$PATH"`; ensure `graphify` present (else `uv tool install graphifyy`).
- `graphify update .` — refresh code memory (AST-only, zero tokens). Blocking; wait for it.
- Read this file's queue (§3) and the run log (§7). Update `docs/process/status/run-status.json`
  to `phase: "boot"` (see §5).

**Phase 1 — Pick the block** (orchestrator, effort medium)
- Interleave rule: **even day-of-month → bug/hardening**, **odd day-of-month → llm_wiki parity**.
  If the chosen lane is empty, fall back to the other lane. Pick the TOP unchecked item in §3.
- Tag the block `mechanical | standard | architecture` (drives model choice, §4).
- If the block is `architecture`-tagged or touches `InferenceProvider`/F17/an invariant → the
  orchestrator escalates itself to Opus/high and opens with a `solution-architect` design pass.

**Phase 2 — Implement** (delegated per §4)
- Delegate to the right subagent from `.claude/agents/` with an explicit `model` + `effort`.
- Split into sub-blocks if large; do only the first today.
- Heartbeat: update `run-status.json` `agents[]` at each delegation start/finish (§5).

**Phase 3 — Test 360°** (qa-test-engineer, effort medium)
- Always: `make lint` · `make typecheck` · `make test` (pytest).
- Frontend touched: `cd frontend && npm run -s typecheck && npm run -s lint && npm run -s test`.
- Docs/schema touched: `make er` / `make openapi` must show ZERO git diff (I8).
- **UI blocks — preview:** run Playwright E2E via `docker-compose.ci.yml`; capture a screenshot into
  `docs/screens/`. This is the "test from preview" step. Record pass/fail per gate in `run-status.json`.
- If a gate is red: fix within budget, or revert the block and open a DRAFT PR explaining the blocker.

**Phase 4 — Persist to the weekly integration branch** (Haiku-class, effort low)
> CADENCE (owner decision 2026-07-11): **daily work accrues, release is WEEKLY.** Daily runs
> commit + push but DO NOT open a PR. One consolidated release PR is cut every **Friday ~18:00**
> (see §4.5). This means the owner reviews/merges **one PR per week**, not one per day.
- Weekly integration branch: **`claude/weekly-<YYYY-Www>`** (ISO week, e.g. `claude/weekly-2026-W28`).
  On the week's FIRST run, create it off latest `origin/main`; on later runs of the same week,
  `git fetch` and continue on it (rebase onto origin if it moved).
- Commit(s): `feat|fix(module): description [Fxx|Kxx]` (CLAUDE.md §11). Reference a feature ID.
- Update `CHANGELOG.md [Unreleased]` (accumulates all week's entries; the Friday run turns
  `[Unreleased]` into the versioned section).
- `git push` the weekly branch. **Do NOT open a PR** (the Friday release run does that).

**Phase 5 — Record & publish dashboard** (Haiku-class, effort low)
- Tick the shipped item in §3; append a row to the run log (§7).
- Finalize `run-status.json` (`phase: "done"`, totals) and append to `history.jsonl`.
- Regenerate `docs/dashboard/index.html` from the two status files and **re-publish the Artifact**
  to the stored URL (§5). Commit the status/dashboard/queue changes on the weekly branch and push.
- Report a 5-line summary and STOP.

**§4.5 — Weekly release run (Fridays ~18:00)**
A separate scheduled run consolidates the week into ONE release, following the repo's release flow:
1. `git fetch origin`; ensure the weekly branch `claude/weekly-<YYYY-Www>` is rebased on `origin/main`.
2. Run the full 360° gate on the whole week's accumulation (`make lint` · `make typecheck` ·
   `make test`; frontend if touched; `make er`/`make openapi` zero-diff if schema/routes moved).
3. Pick the version bump (patch/minor per what shipped) and run `make bump VERSION=x.y.z` (updates
   the 4 version files) — this is the single "release commit". Finalize `CHANGELOG.md` (move
   `[Unreleased]` → `[x.y.z] — <date>`).
4. Open ONE **PR** `main ← claude/weekly-<YYYY-Www>` (mirror `.github/PULL_REQUEST_TEMPLATE.md`),
   summarizing every block shipped that week. NEVER merge — owner reviews & merges, then the
   `release-cut.yml` / `desktop-release.yml` workflows cut the tag + images from `main`.
5. After the owner merges, the NEXT week starts a fresh `claude/weekly-<next-week>` off `origin/main`
   (a merged PR is finished — never restack on merged history).
- If the week produced nothing shippable, skip the release (log "no release this week") — do not
  open an empty PR.

---

## 3. Work queue (interleaved; top = next)

> Grounded in `PROGRAM-v1.5-LLMWIKI-PARITY.md`, `docs/reference/AUDIT-SYNAPSE-VS-LLMWIKI-1TO1-2026-07-10.md`,
> `BACKLOG.md`, and the `CHANGELOG [1.5.3]` follow-up. The orchestrator refines this as it learns;
> keep items **bounded** (split anything L into S/M sub-blocks before starting).

### Lane P — llm_wiki parity (odd days)
- [x] **P3-b (1/3) — Network proxy config keys** (S) — DONE 2026-07-11 (run1). Added
      `network_proxy_{enabled,url,bypass_local}` (S24/S25/S26) to `ALLOWED_CONFIG_KEYS` +
      `validate_value` + pytest, under ADR-0053. `ORDERED_KEYS`/GET/UI untouched (staged).
- [ ] **P3-b (2/3) — Surface proxy settings in GET /config/app + Settings UI** (M). Add the 3 keys
      to `ORDERED_KEYS`, per-key field metadata in `routers/config.py`, a Network-proxy section in
      `SettingsPanel.tsx` + `settingsStore.ts`, update the FE snapshot + `test_config_overrides.py:537`
      last-two assertion. Tag: standard (touches FE snapshot — needs `npm` + vitest).
- [ ] **P3-b (3/3) — Wire httpx proxy transport** (M). Read the effective proxy config and apply an
      `httpx` proxy/bypass transport to outbound clients (LLM/embeddings/search/update). Consider an
      ADR for the transport seam. Tag: architecture (cross-cutting outbound clients).
- [ ] **Synthesis/comparison auto-trigger on ingest-all completion** (M). `ops/synthesize.py` +
      Home manual trigger exist (v1.5.3); add the auto-trigger at end of ingest-all. Bounded loop
      (I7). Tag: standard → escalate if it touches the orchestrator core.
- [ ] **P3-c — Source Watch wider types** (M/L → split). Real extractors for
      .doc/.odt/.rtf/.odp/.ods/.csv/.html/.mdx + grouped-checkbox UI + excluded-folders + max-size.
      Split per format family. Tag: standard.
- [ ] **P3-d — MinerU cloud PDF toggle** (L → split; ADR-0069 exists). Opt-in, off-by-default,
      upload warning. Tag: architecture (backend integration + invariant I9).
- [ ] **P3-e — Multi-provider web search** (L → split; ADR-0070/0071). Tavily/SerpApi/Firecrawl/
      Brave/Ollama-Web adapters behind a seam, opt-in off-by-default. Tag: architecture.
- [ ] **P4 — Chat composer** (L → split). Skills toggle · AnyTXT toggle · Fast/Standard/Deep/Local
      pills wired to retrieval. Tag: standard.
- [ ] **P5 — Skills view** (M/L). Rail entry: scan/enable/disable/rescan skill folders. Tag: standard.

### Lane B — bug / hardening (even days)
- [ ] **AUDIT gap: Query pages 100% lint placeholders** — verify `lint.py` `_create_broken_link_stub`
      no longer hard-codes `type=QUERY`; fix if still present. Tag: standard.
- [ ] **AUDIT gap: entity dedup only on exact title-slug** (`orchestrator.py`) → duplicate pages.
      Retrofit review-queue path (see TODO `orchestrator.py:1459`). Tag: architecture (dedup logic).
- [ ] **AUDIT gap: `related:` frontmatter** — confirm `ops/backfill_related.py` populates it; wire
      if dormant. Tag: standard.
- [ ] **AUDIT gap: "Save to Wiki" hard-codes `type=query`** instead of `synthesis/`. Tag: mechanical.
- [ ] **BACKLOG WS-B — review queue status filter** (Phase-0 confirm first; drop if not reproduced).
- [ ] **TODO `wiki/index.py:158/171`** — em-dash gloss needs `Page.summary` column (core wave).
      Tag: architecture (schema change → `make er` + Alembic).

> When both lanes are empty: run a `graphify query` sweep for the next AUDIT gap, or do a bounded
> docs/test-coverage hardening block. Never invent scope beyond the tracked sources.

---

## 4. Model routing & effort (no "always Opus")

| Block tag | Model | Effort | Typical work |
|-----------|-------|--------|--------------|
| `mechanical` | `claude-haiku-4-5` | low | 1-file fix, i18n keys, snapshot-test update, changelog/docs sync, `make er`/`make openapi`, formatting |
| `standard` | `claude-sonnet-5` | medium | FastAPI routes, React components, ops modules, most parity slices, their tests |
| `architecture` | `claude-opus-4-8` | high (xhigh only for gnarly design) | ADRs, `InferenceProvider`/F17, schema/Alembic, cross-cutting, invariant-touching, final architect/QA gate |

Rules:
- The orchestrator itself runs at **medium** by default; it escalates to **Opus/high only** for an
  `architecture`-tagged block or when a gate demands it.
- Respect any model pinned in a subagent's `.claude/agents/*.md` definition.
- Phase 0/4/5 (boot, release mechanics, recording) are **Haiku/low** — they are plumbing.
- **Log** `{model, effort, cost_usd}` for every delegation into `run-status.json` (feeds the dashboard).

---

## 5. Status contract (feeds the dashboard)

The session keeps `docs/process/status/run-status.json` current at every phase boundary:

```jsonc
{
  "run_id": "2026-07-11",              // date of the run
  "started_at": "2026-07-11T07:03:00Z",
  "phase": "implement",                // boot | pick | implement | test | release | done | failed
  "lane": "parity",                    // parity | bug
  "block": "P3-b — Network proxy settings page",
  "block_tag": "standard",
  "orchestrator": { "model": "claude-sonnet-5", "effort": "medium" },
  "agents": [                          // one row per delegation — THIS is "who is working"
    { "name": "backend-engineer", "model": "claude-sonnet-5", "effort": "medium",
      "task": "add proxy config + httpx transport", "status": "running", "cost_usd": 0.0 }
  ],
  "tests": { "lint": "pending", "typecheck": "pending", "pytest": "pending",
             "vitest": "pending", "e2e": "pending" },   // pass | fail | pending | n/a
  "pr_url": null,
  "total_cost_usd": 0.0,
  "updated_at": "2026-07-11T07:20:00Z"
}
```

On finish, append the final object as one line to `docs/process/status/history.jsonl`, then
regenerate `docs/dashboard/index.html` and re-publish the Artifact.

**Dashboard Artifact URL:** the daily job MUST re-publish to THIS url (pass it as `url=` to the
Artifact tool) so the link stays stable — never mint a new one.
`DASHBOARD_ARTIFACT_URL: https://claude.ai/code/artifact/864280d0-9cba-47a3-b845-21b42e39f1ac`

> Phase-5 publish recipe (keep in sync): (1) regenerate `docs/dashboard/index.html` embedding the
> fresh `run-status.json` + `history.jsonl` into the `<script id="run-data">` block; (2) extract the
> `<style>` + inner `<body>` into a body-only temp file; (3) call Artifact with that temp file **and**
> `url=<DASHBOARD_ARTIFACT_URL>`, favicon `🧠`. This mirrors the setup run.

---

## 6. How it is scheduled (two jobs)

Two schedules drive the loop:
- **Daily work** — `cron "3 7 * * *"` (07:03): run ONE block, commit+push to the weekly branch, NO PR.
- **Weekly release** — `cron "3 18 * * 5"` (Fri 18:03): the §4.5 release run — bump + ONE PR to `main`.

**Preferred vehicle: durable Routine** via `mcp__Claude_Code_Remote__create_trigger`
(`create_new_session_on_fire: true`, `notifications: {push:true}`). Durable server-side, spawns a
fresh isolated session each fire — true no-input autonomy that survives this session ending.
Stop with `delete_trigger`; fire an extra run with `fire_trigger`.

**Current stopgap: session-cron bridge** (`CronCreate`). Until the owner grants the client
permission for `create_trigger`, the daily job runs as a session-cron (job `0830c896`). Caveat:
session-only — fires into THIS session while idle, dies when it ends, auto-expires after 7 days.
Upgrade to the durable Routine as soon as the permission is granted, then delete the bridge.

Why not rely on `CronCreate` long-term: it is session-only (7-day cap, no fresh session). The
Routine is the real autonomy mechanism.

---

## 7. Run log (append-only)

| Date | Lane | Block | Models used | Tests | PR | Cost USD |
|------|------|-------|-------------|-------|----|----------|
| _(bootstrap 2026-07-11 — setup only, no block shipped)_ | — | graphify memory + driver + dashboard scaffolding | opus (setup) | n/a | — | — |
| 2026-07-11 (run1) | parity | P3-b (1/3) — network-proxy config keys [ADR-0053] | orchestrator opus/med · backend-engineer **sonnet**/med | lint✓ type✓ pytest✓ (30) | (branch) | — |
