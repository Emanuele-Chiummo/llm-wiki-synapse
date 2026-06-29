# ADR-0021 — Labeled NavRail Standard + Provider Config CRUD Contract (M4-HARD)

- Status: Accepted
- Date: 2026-06-29
- Sprint: v0.4 (M4 "Usable and fluid"), M4-HARD hardening increment
- Decider: solution-architect
- Supersedes (in part): ADR-0018 §1 (the 48px icon-only rail; the disabled-Chat / "Phase 3"
  rail item; the separated M5 placeholder group). ADR-0018's state model (§2), provider
  read/derive model (§4), settings model (§5) and i18n rules (§6) remain in force.
- Invariants: **I3** (NavRail and SettingsPanel sub-nav subscribe via scalar/shallow selectors
  only; no whole-store subscription; section switch does not invalidate graph selectors),
  **I4** (no WYSIWYG/ProseMirror/CodeMirror added; the two new lists — Settings left-nav (9 fixed
  items) and provider list — are BOUNDED, not virtualization candidates), **I6** (provider ADD
  form collects `provider_type` / `model_id` / `base_url` as *user input*; NO model ID or
  provider is hardcoded; routing stays server-resolved from `provider_config`), **I7** (the
  conversation-history-length selector is a context-budget control — see Consequences for the
  unwired-consumer flag)
- Related: ADR-0018 (the rail/section/provider/settings baseline this amends), ADR-0008
  (provider_config schema; `model_id` is a required column), ADR-0017 (3-panel shell),
  ADR-0019 (chat streaming — the `chat` section this rail now activates), CLAUDE.md §3
  (I3/I4/I6/I7), §4b (F1, F16, F17), §5 (F17 provider detail),
  docs/sprints/v0.4-hard-scope.md (PM scope-lock; Point A / Point B rulings; §3 ACs)

---

## Context

ADR-0018 shipped a ~48px icon-only left rail with hover tooltips, a `disabled` Chat item tagged
"Phase 3", and a separated group of M5 placeholder items (Search / Lint / Review / Deep Search)
rendered at near-invisible `#30363d`. Human browser testing (EC-M4-HCP) found two usability
defects:

1. The icons were *non parlanti* — not self-explanatory; hover tooltips are a discoverability aid,
   not a substitute for a visible label.
2. The greyed-out, label-less, non-functional M5 items created uncertainty about whether the app
   was broken or the features were intentionally absent.

The PM ruled (docs/sprints/v0.4-hard-scope.md §2): **Point A** — persistent text labels beside/
below each icon, rail widened to ~72px (Form 1; reject hover-only Form 2 and stateful expandable
Form 3); **Point B** — REMOVE the four M5 items from the M4 rail entirely (do not label a disabled
placeholder), retaining their i18n keys and their `Section` union members for M5.

Separately, the Settings surface was rebuilt from ADR-0018's single-column form into a 9-section
left-nav layout, and the LLM Models section went from read-only to read-write: the user can now
**ADD** a provider (`POST /provider/config`) and **DELETE** one (`DELETE /provider/config/{id}`).
This is the first UI surface that mutates `provider_config` beyond the Header selector's
"set-active" POST. It therefore needs an explicit architect contract on (a) the no-hardcoded-ID
guarantee under user-typed input, and (b) the last-provider deletion risk.

This ADR ratifies the labeled-rail standard, the M5-removal pattern, and the provider-CRUD
contract; and records the architectural concerns the gate must track.

---

## Decision

### 1. Labeled NavRail is the standard (supersedes ADR-0018 §1 rail geometry)

The rail is **72px wide**; each item is a `<button>` rendering an icon SVG plus a persistent
`<span>` caption below it at **10px** (PM cap: ≤12px), centered, `white-space: nowrap` with
ellipsis truncation. The active-state highlight (`#1f2937` fill + `#21262d` outline) encloses
both icon and label. Labels are existing i18n keys (`nav.chat`, `nav.wiki`, `nav.sources`,
`nav.graph`, `nav.settings`) — no new keys.

The M4 rail renders exactly **5 interactive items**: Chat · Wiki · Sources · Graph (TOP_ITEMS) +
Settings (BOTTOM_ITEMS, pinned). The default section on first load is **`chat`** (ADR-0019
activated it; the old "Phase 3" disabled stub is gone). The ingest running-count badge stays on
the Sources item via `useIngestRunningCount()` (separate hook — no graph-store coupling).

This is now the canonical rail pattern. When M5 ships Search / Lint / Review / Deep Search logic,
those items are re-added to the rail **with labels and full functionality** — never as labeled or
unlabeled disabled chrome. "Features appear when they work" is the ratified rule.

### 2. M5 items are removed from the rail; the `Section` type retains them (no churn)

`M5_ITEMS` is an empty array (kept as a populate-point comment for M5). The separator between the
active group and the (former) placeholder group is removed. Crucially:

- The `Section` union in `graphStore.ts` **retains** `"search" | "lint" | "review" | "deep-search"`
  as valid members (PM AC-HARD-M5P-5). This avoids a TypeScript mass-refactor at M5.
- The `SectionRouter` **retains** render branches for those four values, each rendering an M5
  `ComingSoonPlaceholder`. These branches are now **unreachable in M4** because no rail button can
  set those sections and the default is `chat` — but they are kept type-sound and live so M5 can
  re-add the rail button and the route is already wired. This is deliberate dead-but-correct state,
  not a bug.

### 3. The `chat`-default / no-persistence path is type-sound (analysis, not a change)

`activeSection` is **not persisted** to `localStorage` anywhere. It initializes to `"chat"` on
every load (graphStore `INITIAL_STATE.activeSection`). Therefore the AC-HARD-ORD-3 concern
("if a restored section is a removed M5 item, fall back to chat") **cannot arise in M4**: there is
no restore path, so there is no dead-state risk. The keyboard-nav handler in NavRail iterates only
`[...TOP_ITEMS, ...M5_ITEMS, ...BOTTOM_ITEMS]` (M5 empty), so arrow keys can never land on a
removed section. Even if a future change introduces section persistence, `SectionRouter` already
renders every `Section` member (no `null` for the M5 four — they hit the placeholder), so the
union remains exhaustively handled. **Verdict: type-sound, no dead-state.** (If section
persistence is added later, it MUST sanitize a restored value against the rendered rail items and
fall back to `chat` — recorded as a forward constraint, not an M4 defect.)

### 4. Provider Config CRUD contract (ADD / DELETE) — no hardcoded IDs; server-resolved routing

The LLM Models section is read-write against the **already-existing** backend endpoints
(verified in `backend/app/main.py`): `GET /provider/config`, `POST /provider/config` (201),
`DELETE /provider/config/{config_id}` (204 / 404). The DELETE endpoint the frontend calls
**exists** — no missing-endpoint defect.

**I6 guarantee under user input.** The ADD form collects `provider_type` (a fixed 3-way select:
local | api | cli — these are *capability discriminants*, not model identities), a free-text
`model_id`, an optional `base_url`, and a scope (global | vault). **No `model_id` literal is
hardcoded anywhere** — the value is typed by the user and stored verbatim in a `provider_config`
row. The list is rendered entirely from `GET /provider/config`. Routing remains **server-resolved**
(the orchestrator resolves the active provider from `provider_config` precedence at run time; the
UI never decides a backend). I6 holds: the three `provider_type` enum values are the F17
abstraction's own taxonomy (CLAUDE.md §5), not a hardcoded backend.

**Active-row derivation** stays the ADR-0018 §4 model: `addProvider` / `deleteProvider` both
re-fetch the list and call `deriveActiveItem(list, vaultId)` (vault-scoped DESC `created_at`,
non-fallback first, else global). Mutations keep `activeItem` consistent without a reload.

**Last-provider deletion.** Per PM AC-HARD-PROV-6 the UI does **not** enforce a minimum of one
provider; it shows a non-blocking warning and permits the delete. The system tolerates zero
providers safely: with an empty `provider_config`, the next ingest/chat fails *server-side* with a
"no resolvable provider" error surfaced as an error toast — it does not crash the client and does
not corrupt state. **Architect verdict on the guard: the non-blocking warning is ADEQUATE for
M4-HARD**, on two conditions (see §Conditions): the warning must actually fire on the last-row
delete, and the empty-provider server failure must be a clean, surfaced error (not a 500 or a
silent hang). A hard minimum-of-one enforcement is explicitly NOT wanted — it would let a user get
stuck unable to delete a misconfigured sole provider to replace it.

### 5. Panel collapse uses the library, not a measurement loop (I3/I4)

Left/right panels are `collapsible` with `collapsedSize="3%"` via `react-resizable-panels`
`usePanelRef()` + imperative `collapse()` / `expand()`. No custom DOM measurement or rAF layout
loop is introduced (I3); the library owns layout. The collapsed-content guard (`!leftCollapsed &&`)
unmounts the panel's heavy children when collapsed — a render reduction, not a heavy task.

### 6. Invariant confirmation (this increment)

- **I3:** NavRail subscribes to `selectActiveSection` (scalar) + `selectSetActiveSection` only,
  plus the isolated `useIngestRunningCount()`. SettingsPanel's sub-nav is **local `useState`**
  (not store-backed) — zero store churn on sub-nav switch. The LLM Models section uses
  `useShallow(selectProviderList)` for the array and scalar selectors for loading/error/actions.
  settingsStore / providerStore are separate from graphStore, so their changes never re-render the
  graph. No whole-store subscription anywhere in the changed files. No per-token/per-frame work
  added (these are static forms/lists). **PASS.**
- **I4:** No CodeMirror / ProseMirror / Milkdown / contentEditable added. The two new lists are
  **bounded**: the Settings left-nav is a fixed 9-item array (`NAV_ITEMS`); the provider list is
  bounded by the number of `provider_config` rows a single user hand-creates (realistically < 20).
  Neither is a virtualization candidate. **PASS — with the bounded-growth note in §Consequences.**
- **I6:** No hardcoded model/provider IDs; ADD form is user input; list from API; routing
  server-resolved. **PASS.**
- **I7:** `conversationHistoryLength` is a context-budget control. Persisted correctly. **Its
  consumer is NOT wired** (see §Conditions / Consequences) — flagged, non-blocking for THIS
  increment's architectural soundness but a QA-gate AC gap.
- **I2 / I5 / I1:** untouched by this frontend increment.

---

## Conditions (attached to APPROVE-WITH-CONDITIONS)

1. **C1 — wire `conversationHistoryLength` into the chat assembler (AC-HARD-CONV-2, I7).**
   `ChatSection.tsx` reads `selectContextWindow` but NOT `selectConversationHistoryLength`; the
   two history arrays it builds (the new-message send at ~L60 and the regenerate send at ~L87) are
   **not sliced** by the selected length. The setting is persisted but inert. Engineer must read
   `conversationHistoryLength` and trim the sent `messages` array to at most that many of the most
   recent messages (system prompt excluded). This is the explicit I7 budget mechanism the PM and
   functional-analyst attached to F1-HARD-CONV-HISTORY. **Owner: frontend-engineer.** Blocks the
   QA gate (AC-HARD-CONV-2), not this architect sign-off.

2. **C2 — client-side require `model_id` before POST (I6 contract integrity).**
   Backend `ProviderConfigCreate.model_id` is `Field(...)` — **required, non-null** (ADR-0008;
   "no hardcoded defaults"). The frontend `CreateProviderConfigBody.model_id` is optional and
   `handleAdd` sends `model_id: formModelId.trim() || null`. Submitting the ADD form with an empty
   model field sends `null` → backend **422**. Today that surfaces as an error toast (no crash, no
   bad state), so it is not a correctness hole — but the ADD button should be disabled (or the
   field validated) while `model_id` is empty, so the user is not handed a server 422 for a
   foreseeable empty field. **Owner: frontend-engineer.** Minor; UX-hardening, consistent with the
   "no hardcoded default model" rule (we must NOT paper over it by injecting a default ID).

3. **C3 — verify the last-provider warning fires AND the empty-provider server path is clean.**
   AC-HARD-PROV-6 requires a warning when deleting the last remaining provider. Confirm the UI
   detects `providerList.length === 1` at delete time and shows the warning copy
   (`settings.llmModels.confirmDelete` or a dedicated last-provider string) before issuing DELETE.
   Confirm that with zero providers a subsequent ingest/chat returns a clean, surfaced error (not a
   500 / silent hang). If either is missing, it is a QA-gate gap, not an architecture change.
   **Owner: frontend-engineer + qa-test-engineer.**

None of C1–C3 require an architecture change or violate an invariant; all are wiring/validation
hardening. The design is sound.

---

## Consequences

**Positive**
- The rail finally "speaks": labeled items remove the EC-M4-HCP discoverability defect with a pure
  layout change (no new state, no new store, no new i18n keys) — the minimal correct fix.
- Removing M5 chrome eliminates the "is it broken?" ambiguity while keeping the `Section` type and
  `SectionRouter` branches intact, so M5 re-adds each feature by un-emptying `M5_ITEMS` — zero
  shell rework, zero type churn.
- Provider CRUD makes F17 fully user-manageable from the UI without config-file edits, while the
  no-hardcoded-ID and server-resolved-routing invariants are preserved (user types the model_id;
  the backend owns routing).
- The 9-section settings sub-nav is local component state — it adds capability with zero global
  re-render cost (clean I3).
- `chat` as the default section (ADR-0019) lands new users on the most common task.

**Negative / trade-offs (stated explicitly)**
- **`conversationHistoryLength` is persisted but unwired (C1).** The most material gap: the setting
  exists end-to-end in storage and UI but does not yet affect the request payload, so its I7
  budget promise is not yet kept. Bounded to a small wiring change; flagged as a blocking QA-gate
  AC, not an architecture flaw.
- **Provider list is unbounded in principle (I4).** `POST` only creates rows (no upsert — ADR-0018
  §4 known issue); a user repeatedly re-adding could grow `provider_config` without limit, and the
  list is rendered un-virtualized. In practice a single user hand-manages a handful of rows, so it
  is NOT a virtualization candidate for M4. **Forward note:** if/when the upsert/PUT fast-follow
  lands (ADR-0018 Consequences), the "set-active appends a row" growth disappears; until then, if
  the list is ever observed to exceed ~50 rows it must be virtualized (TanStack Virtual) to stay
  I4-compliant. Recorded as a watch item, not an M4 blocker.
- **`model_id`-empty → 422 round-trip (C2).** Foreseeable empty-field error reaches the server
  before client validation catches it. We deliberately do NOT fix this by injecting a default
  model ID (that would violate the I6 / ADR-0008 "no hardcoded default" rule); we fix it by
  client-side requiring the field.
- **`SectionRouter` carries 4 unreachable branches.** Dead-but-correct in M4; the cost is a few
  lines of placeholder rendering kept live for M5. Accepted to avoid type churn (PM AC-HARD-M5P-5).
- **`activeSection` is not persisted.** Every reload returns to `chat`. This sidesteps the
  dead-state risk entirely for M4; the forward constraint (sanitize-on-restore → fallback `chat`)
  is recorded for whoever adds section persistence later.

**Follow-ups (tracked, not this ADR's scope)**
- C1/C2/C3 wiring (frontend-engineer) before the QA gate.
- M5: re-populate `M5_ITEMS` with labeled, functional Search / Lint / Review / Deep Search; the
  `SectionRouter` branches swap their placeholder for the real view.
- `PUT /provider/config` upsert fast-follow (kills row growth; ADR-0018).
- D5 nav screenshot refresh + D6 USER.md nav-journey wording (tech-writer gate).
- If section persistence is introduced: sanitize restored value against rendered rail items,
  fallback `chat`.
