# ADR-0048 — Dark mode, command palette, UI polish, and desktop pack (v0.6 frontend + Tauri)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v0.6 (M6 — shippable)
- **Features:** F1 (3-panel shell / NavRail / chat empty state) · F16 (settings
  persistence, i18n IT/EN, GFM rendering, `dataVersion`) · F15 (cross-platform:
  Tauri v2 desktop — multi-server, zoom, native notifications) · F7/F8 (chat & LaTeX
  rendering surfaces audited for dark theme)
- **Builds on:** ADR-0015 (§CVD-SAFE hex-palette exception for sigma) · ADR-0039
  (Tauri v2 shell; no `invoke`/`window.__TAURI__` in React) · ADR-0045 (FA2 server-side
  layout — client renders precomputed coords) · ADR-0047 (desktop runtime server URL,
  `base.ts`, Connect gate, Header server chip)
- **Reference:** R12 (CodeMirror 6 + `@codemirror/theme-one-dark`) · R10 (sigma.js) ·
  R13 (Tauri v2) · CLAUDE.md §3 (invariants I2/I3/I4/I6), §12 (no hardcoded config)
- **Invariants owned:** I2 (no client-side layout — graph stays render-only) ·
  I3 (no per-token work — palette/notifications add none) · I4 (CodeMirror 6, no WYSIWYG;
  virtualization — palette results capped so none needed) · I6 (pluggable inference —
  untouched). No invariant is traded for convenience.
- **Author:** solution-architect
- **Implementers:** frontend-engineer (`styles/theme.css`, `styles/markdown.css`,
  `store/settingsStore.ts`, `components/settings/SettingsPanel.tsx`,
  `components/common/CommandPalette.tsx`, `components/graph/*`, CodeMirror setup,
  `components/chat/*`, NavRail, `api/base.ts`, Header, `en.json`/`it.json`) ·
  devops-engineer (`src-tauri/Cargo.toml`, `src-tauri/capabilities/default.json`,
  `package.json` `@tauri-apps/plugin-notification`) · tech-writer (D5 screenshots
  refreshed light+dark, D6a USER notes for shortcuts/zoom)

---

## 1. Context

The owner approved four packages for the v0.6 frontend + desktop shell: **T1 dark mode**,
**T2 command palette + shortcuts**, **T3 polish**, **T4 desktop pack**. All four are
presentation/UX and desktop-transport concerns; none touch the backend, the vault, or the
inference layer.

Verified facts (re-confirmed against the tree):

- `frontend/src/styles/theme.css` is the single color source: `:root` block, `--syn-*`
  variables, `color-scheme: light`. Components consume the vars via inline styles / class
  primitives; there are **no hardcoded hex values in components**. Flipping the vars
  re-themes the app — *except* two documented hazards this ADR must call out (§2.1).
- `frontend/src/components/graphPalette.ts` documents that **sigma cannot resolve CSS
  custom properties at canvas draw time**, so the hex community palette is the documented
  exception to token-only usage (ADR-0015 §CVD-SAFE).
- `settingsStore.ts` already persists `language`/`context` in `localStorage`
  (`synapse.lang`, `synapse.settings`) and `serverUrl` (ADR-0047).
- CodeMirror 6 with `@codemirror/theme-one-dark` is already in dependencies.
- Chat streaming is NDJSON; **I3** (no per-token work) and **I4** (CodeMirror + virtualized
  lists) apply.
- Desktop plumbing exists: `api/base.ts`
  (`apiBase`/`getServerUrl`/`setServerUrl`/`clearServerUrl`/`getLastServerUrl`/`isTauri`),
  a Header server chip, and the ConnectScreen gate (ADR-0047).

Two theme-audit hazards in `theme.css` that a naive "flip the vars" dark mode would miss:

1. **`color-mix(... white NN%)`** — the `.syn-section-notice--*` rules and several
   `focus-visible`/border rules mix accent/semantic colors **against a literal `white`**
   (e.g. `color-mix(in srgb, var(--syn-green) 8%, white 92%)`). In dark mode these produce
   pale, low-contrast fills regardless of the `--syn-*` overrides, because `white` is not a
   token. These must be re-expressed against a token (e.g. a new `--syn-mix-base` set to the
   theme's surface) or duplicated in the dark block.
2. **Raw scrollbar/thumb hex** (`#c7ccd4`, `scrollbar-color`) and shadow rgba tuned for a
   light background — visible but wrong on dark; audit alongside the vars.

---

## 2. Decision

Ratify the contract for all four packages. Amendments are limited to: the dark-mode audit
scope (§2.1, the `color-mix`/scrollbar hazard above), the graph dark-mode treatment (§2.1),
and reuse of the existing ADR-0047 persistence semantics for T4 multi-server (§2.4). All
other contract points are accepted verbatim.

### 2.1 (T1) Dark mode — RATIFIED, with an expanded audit scope

- New `:root[data-theme="dark"]` override block in `theme.css` carrying the full `--syn-*`
  dark palette and `color-scheme: dark`. Light stays the base `:root`; dark is an attribute
  override (single stylesheet, no second import, no FOUC beyond the applier's first paint).
- `settingsStore.ts` gains `theme: "light" | "dark" | "system"`, persisted in
  `localStorage["synapse.theme"]`, **default `"system"`**.
- An **applier effect** sets `document.documentElement.dataset.theme` to the **resolved**
  value. `"system"` resolves via `matchMedia("(prefers-color-scheme: dark)")` **with a
  change listener** so the OS toggle re-resolves live. The applier writes the resolved value
  (`"light"`/`"dark"`), never the literal `"system"`, so CSS only ever sees a concrete theme.
- Selector UI (3-way: Light / Dark / System) in
  `components/settings/SettingsPanel.tsx`, i18n-labelled.
- **CodeMirror** swaps to the `oneDark` extension when the resolved theme is dark, back to
  default when light. The swap is a `Compartment.reconfigure` (or equivalent) on resolved-
  theme change — **not** per keystroke and **not** per token; I3/I4 untouched.
- **GraphViewer:** the **node palette stays as-is** — it remains the ADR-0015 §CVD-SAFE
  documented exception and is readable on dark. **Amendment:** the sigma **stage background**
  and **`labelColor`** (and edge/label defaults derived from surface/text) become
  theme-aware, read from the resolved `--syn-*` values in JS (via
  `getComputedStyle(document.documentElement)`), re-applied on resolved-theme change. This is
  a render-property update, **not** a re-layout — I2 holds (coords stay server-computed;
  ADR-0045). `graphPalette.ts` is **not** modified.
- **Audit scope (expanded, mandatory):** `markdown.css` audited for hardcoded light colors,
  **AND** `theme.css`'s `color-mix(..., white NN%)` sites (semantic notices, focus outlines,
  borders) re-expressed against a token so they follow the theme, **AND** the raw scrollbar
  hex (`#c7ccd4`) + light-tuned shadow rgba made theme-aware. Acceptance: no literal `white`
  / light-only hex remains in a rule that renders a surface/fill/text color on both themes
  (the CVD-SAFE graph palette in `graphPalette.ts` is the sole allowed exception).

### 2.2 (T2) Command palette + shortcuts — RATIFIED

- New `components/common/CommandPalette.tsx`; a **global keydown listener**
  (`cmd/ctrl+K` toggle, `Esc` close).
- Sources: app sections + wiki pages. Pages fetched via `fetchAllPages` **cached once per
  open** (not per keystroke), title substring match, **results CAPPED at 20** → no
  virtualization needed. This is the explicit **I4** compliance path: a hard cap keeps the
  list bounded without a virtualizer, which is acceptable *because* it is capped; an
  uncapped list would require TanStack Virtual.
- `Enter` opens the selected page/section via **existing store navigation actions** (no new
  navigation surface).
- `Cmd/Ctrl+N` → new conversation; `Cmd/Ctrl+1..5` → switch section, via existing store
  actions.
- Shortcuts are **ignored while typing** in inputs / textareas / CodeMirror — **except
  `cmd+K`**, which must remain reachable from a focused editor. Detection is by event target
  (`tagName`/`isContentEditable`/CodeMirror DOM), not a global flag.
- i18n namespace `palette.*` in both `en.json` and `it.json` (parity test must pass).

### 2.3 (T3) Polish — RATIFIED

- **NavRail** truncated labels fixed via shorter i18n labels and/or 11px font with a `title`
  tooltip. No fixed-width regression on the other locale (verify IT and EN both fit).
- **Chat empty state** (`components/chat`): brand logo + **3 clickable example-question
  chips** (i18n `chat.examples.*`) that prefill/send via the **existing message-input store
  action** (no new send path).
- **Micro-transitions** (hover/focus, 120–150ms) in `theme.css`. **Constraint:** transitions
  MUST NOT animate the theme switch. Implementation MUST scope transitions to specific
  properties (e.g. `background-color`/`border-color`/`opacity`/`transform` on interactive
  elements) **and/or** apply a `.theme-switching` class on `<html>` during the applier's
  theme change that suppresses transitions for one frame. A blanket
  `transition: all` on `*` is rejected — it would animate every var swap on theme change and
  cause a visible smear.

### 2.4 (T4) Desktop pack — RATIFIED, with one reuse amendment

All T4 UI is Tauri-only, guarded by `isTauri()`; in a browser every branch is inert. I6 is
untouched (backend routes providers via `provider_config`; nothing here hardcodes a
provider).

**(a) Multi-server — RATIFIED, amended to reuse ADR-0047 semantics.**
`base.ts` gains `getKnownServers(): string[]` and `addKnownServer(url: string): void`
(`localStorage["synapse.servers"]`, JSON array, **deduped, max 5**). **Amendment:**
`addKnownServer` is invoked from the **existing successful-connect path** established by
ADR-0047 (`setServerUrl` is called only after a 2xx `GET /status`), so a known server is by
construction a server that connected at least once — do **not** add unvalidated raw input to
the list. The URL stored is the normalized value `setServerUrl` produced (trimmed, trailing
slash stripped, `http(s)` only per ADR-0047 §2.7), so the list is deduped on the normalized
form and cannot contain a hostile/typo'd base.
The Header server chip (ADR-0047) becomes a **dropdown** listing known servers; picking one
calls `setServerUrl(url)` then **`window.location.reload()`**.
**Why full reload (documented):** switching backend changes the base for all 12 clients,
every cached query, the graph, conversations, and `dataVersion`. A full reload is the
**simplest correct** state reset — it guarantees no stale cross-server data leaks into the
new session, with zero bespoke invalidation logic. The cost (a ~1s reload) is paid only on
an explicit, rare user action. Selective invalidation is rejected as clever-and-fragile.

**(b) Zoom — RATIFIED.** `Cmd/Ctrl +`/`-`/`0` adjust
`document.documentElement.style.zoom` between **0.8 and 1.4, step 0.1**, persisted
`localStorage["synapse.zoom"]`, restored on load (Tauri only). CSS `zoom` is used
deliberately: it works in WKWebView/WebView2, whereas a root `font-size` scale would **not**
work because the app styles are in `px`, not `rem`. The zoom keydown handler shares the T2
"ignore while typing" discrimination only where it would collide; `+`/`-`/`0` with a
modifier are safe in inputs and may remain active.

**(c) Ingest-completion notification — RATIFIED.** Via
`@tauri-apps/plugin-notification`: `Cargo.toml` dep + capability `notification:default` in
`src-tauri/capabilities/default.json` + npm `@tauri-apps/plugin-notification`. Fired **where
the frontend already observes an ingest run reaching a terminal state** (reuse the existing
ingest-activity observation from ADR-0046 — do not add a new poll), and **only when
`isTauri()`**. Permission is requested lazily on first fire (`isPermissionGranted` →
`requestPermission`), not on startup.

---

## 3. Consequences

**Positive**
- One stylesheet, one attribute (`data-theme`), one persisted key (`synapse.theme`) drives
  the whole app's theme; `"system"` tracks the OS live. CodeMirror and sigma follow the
  resolved theme without per-token or per-frame cost.
- The command palette gives keyboard-first navigation with a hard 20-result cap that keeps
  it I4-compliant without pulling in a virtualizer.
- Desktop gains multi-server switching, zoom, and native ingest notifications, all guarded by
  `isTauri()` so the web/PWA build is byte-identical.
- Multi-server reuses ADR-0047's validated-then-persist path, so the known-servers list can
  never hold an unreachable or hostile base.

**Trade-offs / limitations (stated explicitly)**
- Dark mode requires re-expressing every `color-mix(..., white)` and raw-hex site in
  `theme.css`/`markdown.css`; the graph node palette stays a hardcoded exception (ADR-0015),
  so a genuinely dark-optimized node palette is *not* delivered here — the existing palette
  is merely verified readable on dark. A future ADR may add a dark node palette if contrast
  testing demands it.
- `window.location.reload()` on server switch throws away all in-memory state by design;
  unsaved chat drafts on the previous server are lost. Accepted: it is the simplest correct
  reset and the action is explicit and rare.
- `document.documentElement.style.zoom` is non-standard (works in the target WebKit/Chromium
  webviews and Chromium browsers, ignored by Firefox). Scoped to `isTauri()`, so the
  non-support case never runs.
- Micro-transitions must be property-scoped or frame-guarded; a mistaken `transition: all`
  would smear the theme switch — this is called out as a reject-on-review condition.

**Invariant check**
- **I2:** graph stays render-only. Dark mode changes only sigma render properties (stage
  background, `labelColor`) read from resolved vars; coords remain server-computed
  (ADR-0045). No client layout. **Holds.**
- **I3:** no per-token work added. Theme swap, palette open, and notifications are
  event-driven (theme change, `cmd+K`, ingest terminal state), never per stream token.
  **Holds.**
- **I4:** editor stays CodeMirror 6 (`oneDark` is a theme extension, not a WYSIWYG). Palette
  results are hard-capped at 20 → bounded list, no virtualizer required; existing virtualized
  lists unchanged. **Holds.**
- **I6:** inference layer untouched; no provider referenced or hardcoded anywhere in this
  work. **Holds.**
- **I5 (Obsidian):** no vault, frontmatter, or wikilink change. **Unaffected.**
- No invariant is traded for convenience. **CONFIRMED: none violated.**

---

## 4. Risks (surfaced to orchestrator)

1. **`color-mix(..., white)` regression:** if the dark-mode audit misses the semantic-notice
   / focus-outline `color-mix` sites, dark mode ships with pale unreadable fills — the audit
   acceptance grep (no literal `white`/light hex in dual-theme rules) is the gate.
2. **`transition: all` smear:** an unscoped micro-transition would animate every `--syn-*`
   swap on theme change; enforce property-scoped transitions or the `.theme-switching` guard
   at review.
3. **CSS `zoom` non-standardness:** works in the Tauri WebKit/WebView2 targets but is not a
   web standard; keep it strictly behind `isTauri()` so the PWA never depends on it.

---

## 5. Contract the implementers follow verbatim

1. `theme.css` gains a `:root[data-theme="dark"]` block (full `--syn-*` dark palette,
   `color-scheme: dark`); every `color-mix(..., white NN%)` and raw light-only hex
   (scrollbar, shadow) is re-expressed against a token or duplicated for dark. Only
   `graphPalette.ts` may keep hardcoded hex (ADR-0015 §CVD-SAFE).
2. `settingsStore.ts` adds `theme: "light"|"dark"|"system"` (default `"system"`,
   `localStorage["synapse.theme"]`); an applier effect writes the **resolved** value to
   `document.documentElement.dataset.theme`, resolving `"system"` via `matchMedia` with a
   `change` listener. SettingsPanel exposes a 3-way selector.
3. CodeMirror reconfigures to `oneDark` on resolved-dark, default on resolved-light — via a
   compartment reconfigure on theme change, never per keystroke/token.
4. GraphViewer reads sigma stage background + `labelColor` from resolved `--syn-*`
   (`getComputedStyle`) and re-applies on theme change; **no** re-layout; `graphPalette.ts`
   unchanged.
5. `components/common/CommandPalette.tsx`: `cmd/ctrl+K` toggle, `Esc` close; pages via
   `fetchAllPages` cached once per open; substring match; **results capped at 20**; `Enter`
   navigates via existing store actions; `Cmd/Ctrl+N` new conversation; `Cmd/Ctrl+1..5`
   switch section; shortcuts ignored while typing except `cmd+K`. `palette.*` in both
   `en.json`/`it.json` (parity test passes).
6. NavRail labels fixed (shorter i18n and/or 11px + `title`), verified in IT and EN. Chat
   empty state: brand logo + 3 `chat.examples.*` chips that send via the existing
   message-input store action. Micro-transitions (120–150ms) are **property-scoped** and/or
   guarded by a `.theme-switching` class; **no `transition: all` on `*`**.
7. `base.ts` gains `getKnownServers()`/`addKnownServer(url)`
   (`localStorage["synapse.servers"]`, JSON, deduped, max 5); `addKnownServer` is called
   **only from the successful-connect path** (ADR-0047 `setServerUrl` after 2xx `/status`)
   with the normalized URL. Header chip becomes a dropdown; selecting a server calls
   `setServerUrl(url)` then `window.location.reload()`.
8. Zoom: `Cmd/Ctrl +`/`-`/`0` set `document.documentElement.style.zoom` in `[0.8, 1.4]`
   step `0.1`, persisted `localStorage["synapse.zoom"]`, restored on load; Tauri-only.
9. Ingest notification: `@tauri-apps/plugin-notification` (Cargo.toml dep +
   `notification:default` capability in `src-tauri/capabilities/default.json` + npm dep);
   fired from the existing ingest terminal-state observation (ADR-0046), permission requested
   lazily on first fire, only when `isTauri()`.

## 6. Do NOT (reject any PR that does these)

1. Do NOT add hardcoded hex/`white`-mixed colors to any dual-theme rule outside
   `graphPalette.ts` — dark mode must follow the tokens (breaks the theme; audit gate).
2. Do NOT run a force/FA2 layout on the client for graph theming — dark mode changes render
   properties only; coords stay server-side (I2, ADR-0045).
3. Do NOT parse markdown/LaTeX or do any work per stream token for the empty state,
   notification, or palette (I3).
4. Do NOT ship an uncapped or virtualization-skipping palette list — the 20-cap is what makes
   it I4-compliant without a virtualizer (I4).
5. Do NOT use `transition: all` (would animate the theme swap); scope transitions or guard
   with `.theme-switching`.
6. Do NOT add unvalidated URLs to `synapse.servers`, and do NOT invent a second persistence
   path — reuse ADR-0047's `setServerUrl`-after-successful-`/status` semantics.
7. Do NOT call Tauri IPC beyond the `plugin-notification` API and the passive `isTauri()`
   check; every T4 branch stays behind `isTauri()` (ADR-0039 §9.1 carve-out).
8. Do NOT hardcode any provider anywhere (I6 untouched).
