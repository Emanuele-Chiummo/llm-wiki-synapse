# ADR-0047 — Desktop runtime server URL + Connect gate (Tauri first-launch backend binding)

- **Status:** Accepted
- **Date:** 2026-07-02
- **Sprint:** v0.6 (M6 — shippable; F15 cross-platform desktop packaging)
- **Features:** F15 (cross-platform: Tauri v2 desktop bundles, macOS + Windows) ·
  F1 (3-panel shell / AppShell gate) · F16 (settings persistence, i18n IT/EN, timeout,
  multi-provider chat over the configured server)
- **Builds on:** ADR-0028 (browser API base is relative; proxy target is a server-only
  env var) · ADR-0039 (Tauri v2 desktop shell — scaffold, CI, single codebase)
- **Reference:** R13 (Tauri v2 — https://tauri.app) · CLAUDE.md §3 (invariants),
  §12 (no hardcoded config in code)
- **Invariants owned:** I5 (Obsidian compat — unaffected; pure transport/UI plumbing) ·
  I6 (pluggable inference — backend-side; not touched by desktop packaging) ·
  I9 (do not reinvent — Tauri v2 is the planned R13 tool; reuse existing HTTP clients).
  No invariant is traded for convenience.
- **Author:** solution-architect
- **Implementers:** frontend-engineer (`frontend/src/api/base.ts`, `frontend/src/api/*Client.ts`,
  AppShell + ConnectScreen + Header, `en.json`/`it.json`) · devops-engineer
  (`backend/app/config.py` CORS default, `src-tauri/tauri.conf.json`, `.github/workflows/`,
  `src-tauri/icons/`) · tech-writer (D6b DEPLOY notes)

---

## 1. Context

ADR-0039 established the Tauri v2 shell on the assumption that the desktop app and the
backend share an origin (`VITE_API_BASE` build-time inlining, ADR-0028). That assumption
holds for the **PWA served by the same FastAPI process**, but it is **false for the packaged
desktop app**:

- A packaged Tauri app loads its frontend from a **fixed webview origin** — `tauri://localhost`
  on macOS/Linux (WebKit), `http://tauri.localhost` on Windows (WebView2). It is **not**
  served by the backend and has **no** same-origin backend to fall back to.
- The user's backend is a **separate dockerized Synapse service** reachable at a URL the
  build cannot know: `http://127.0.0.1:8000`, a Tailscale MagicDNS name, or a Cloudflare
  Tunnel hostname. A build-time-inlined `VITE_API_BASE` cannot express a value the end user
  chooses at first launch.

So the desktop app needs a **runtime, user-supplied backend URL**, persisted on the device,
entered once at first launch. This is a new configuration surface that ADR-0028's two-variable
model (browser-relative `""` + server-only `BACKEND_PROXY_TARGET`) does not cover, because in
packaged desktop mode there is neither a same-origin backend nor a Vite dev proxy.

Verified facts (prior exploration, re-confirmed here):
- 12 clients (`frontend/src/api/*Client.ts`) declare a module-level
  `const API_BASE = (import.meta.env["VITE_API_BASE"] ?? "")` and build `${API_BASE}/path`.
- Chat streaming is `fetch` + `ReadableStream` NDJSON (no WebSocket/EventSource), so the
  base-URL fix is uniform across every call — including the stream — with no separate
  transport to special-case.
- `src-tauri/tauri.conf.json` exists (identifier `ai.synapse.app`, `security.csp: null`,
  `frontendDist ../frontend/dist`, `devUrl :5173`), **no `bundle.icon`** yet.
- Backend CORS default is `"http://localhost:5173,http://127.0.0.1:5173"` and the middleware
  runs with **`allow_credentials=True`** (`backend/app/main.py:930`).
- `GET /status` exists and returns `{vault_id, data_version, ...}` — a cheap, side-effect-free
  reachability probe.
- Branding assets `frontend/src/assets/synapse-logo.svg` + `synapse-appicon.svg` are committed.

---

## 2. Decision

Ratify the proposed contract. Amendments are limited to two hardening points (§2.4, §2.7)
and one confirmation of a CORS constraint (§2.4). All six contract points are otherwise
accepted verbatim.

### 2.1 (C1) `frontend/src/api/base.ts` — call-time base resolution — RATIFIED

A single new module resolves the API base **at call time** (not at module load), with a
strict priority order:

1. `localStorage["synapse.serverUrl"]` — trimmed, trailing slash stripped (desktop runtime).
2. `import.meta.env["VITE_API_BASE"]` — build-time inline (web/PWA split-origin escape hatch, ADR-0028).
3. `""` — relative / same-origin (web + Vite dev proxy, the ADR-0028 default).

```ts
export function apiBase(): string          // resolved per call, priority above
export function getServerUrl(): string | null   // localStorage["synapse.serverUrl"] or null
export function setServerUrl(url: string): void  // trims + strips trailing slash, then persists
export function clearServerUrl(): void
export function isTauri(): boolean          // typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
```

**Why call-time, not module-const:** the user sets the URL *after* the bundle has loaded
(at the Connect gate). A module-level const captured at import would be stale for the whole
session. Call-time resolution keeps a single source of truth (localStorage) and lets
"change server" take effect without a reload.

**`isTauri()` via `__TAURI_INTERNALS__`:** this is the v2 runtime marker injected into
`window` by the Tauri webview. It is a read-only presence check — **not** a Tauri API call —
so it does not violate ADR-0039's "no `window.__TAURI__`/`invoke` in React" rule, which
targets *the IPC/command surface*. Detecting the container is transport plumbing, not a bridge
call. This ADR records that carve-out explicitly so a reviewer does not flag it against
ADR-0039 §9.1.

### 2.2 (C2) Mechanical refactor of all 12 clients — RATIFIED

Every `frontend/src/api/*Client.ts` replaces the module-level `const API_BASE = …` with a
call-time `apiBase()` at each fetch site (`` `${apiBase()}/path` ``). **No behavior change in
web mode:** with no `localStorage` key and no `VITE_API_BASE`, `apiBase()` returns `""` —
byte-identical to today's relative default. Acceptance grep after the change:
`grep -rn 'import.meta.env\["VITE_API_BASE"\]' frontend/src/api/*Client.ts` returns
**nothing** (the only reader of that env is now `base.ts`).

### 2.3 (C3) ConnectScreen gate in AppShell — RATIFIED

AppShell renders `<ConnectScreen/>` **instead of** the app when `isTauri() && !getServerUrl()`.
The gate is web-invisible: in a browser `isTauri()` is false, so the app renders unchanged.
ConnectScreen: full-screen branded (new logo), URL input, validates by `GET {url}/status`
with a timeout; on 2xx → `setServerUrl(url)` → app renders. Header shows the connected server
+ a "change server" action (Tauri only) that calls `clearServerUrl()` and returns to the gate.
New i18n namespace `connect.*` in `en.json` + `it.json` (the existing parity test enforces
key equality). This satisfies I5 (no vault change) and does not touch I2/I3/I4.

### 2.4 (C4) Backend CORS default extension — RATIFIED, with one confirmation

Extend `backend/app/config.py` `cors_allow_origins` default to include the two webview
origins: `tauri://localhost` (macOS/Linux WebKit) and `http://tauri.localhost` (Windows
WebView2). CORS is genuinely required: the packaged webview does a **cross-origin** fetch
(`tauri://localhost` → the user's `http://…:8000`), which is a real CORS preflight.

**Confirmation (not an amendment):** the middleware runs with `allow_credentials=True`
(`main.py:930`). Under the CORS spec, credentials mode **forbids the `*` wildcard** and
requires the server to echo an **exact** origin. The contract's explicit-origin approach is
therefore the *correct* one — a `*` default would silently break credentialed requests. The
default list must stay explicit; adding the two literal origins is spec-compatible. Deployers
who front the backend behind a tunnel still override via `CORS_ALLOW_ORIGINS` env (ADR-0028
principle: no hardcoded prod origins).

**Mixed-content risk accepted for v0.6:** `tauri://` (secure context) → `http://` backend may
be blocked by WebKit's mixed-content policy on macOS. Documented fallback:
`@tauri-apps/plugin-http` (Rust-side fetch bypasses the webview's mixed-content gate). Not
wired in v0.6 to keep the shell thin (ADR-0039 §9.8); recorded as the first fallback if the
LIVE macOS build blocks `http://` backends. HTTPS backends (tunnel/Tailscale-with-TLS) are
unaffected.

### 2.5 (C5) Bundle icons + CI matrix — RATIFIED

Add the `bundle.icon` array to `tauri.conf.json`, generated from
`src-tauri/icons/appicon-1024.png` via `npx tauri icon`. Bundle targets narrow to the two
in scope for this deliverable — macOS (`dmg`/`app`) + Windows (`nsis`); Linux targets from
ADR-0039 remain valid but are not part of this desktop-binding deliverable. CI:
`tauri-apps/tauri-action`, matrix `macos-latest` + `windows-latest`, trigger on tag
`desktop-v*` + `workflow_dispatch`. Unsigned artifacts (code-signing/notarization deferred —
see §4 risk). This is consistent with ADR-0039's tag-gated, non-blocking build posture.

### 2.6 (C6) Branding — RATIFIED

New logo (synaptic S-curve through graph nodes, gradient `#2563eb`→`#8250df`), already
committed as `synapse-logo.svg` + `synapse-appicon.svg`, applied in Header (replacing the
`⚡` emoji), favicon, PWA manifest icons, and Tauri icons (via C5's `tauri icon` from the
1024px raster). Pure presentation; no invariant impact.

### 2.7 Amendment — URL validation hardening in `setServerUrl`/ConnectScreen

Ratified with two mandatory guards on the persisted value, to prevent a foot-gun that would
brick the gate:

1. **Scheme allowlist:** accept only `http://` and `https://`. Reject empty, `javascript:`,
   `file:`, `tauri:`, and bare hosts without a scheme (or auto-prefix `http://` and re-parse).
   This keeps a hostile/typo'd string from being concatenated into `${apiBase()}/path`.
2. **Persist only after a successful `GET /status` probe.** `setServerUrl` may store the raw
   value, but ConnectScreen MUST NOT transition to the app on a non-2xx or timed-out probe;
   it shows a `connect.error.*` message and stays on the gate. This guarantees the app never
   renders against an unreachable base (which, with call-time resolution, would fail every
   request with no obvious recovery except "change server" — which must therefore remain
   reachable from the error state).

These are additive to C1/C3 and do not change the signatures.

---

## 3. Consequences

**Positive**
- The packaged desktop app becomes usable against *any* reachable backend the user names at
  first launch, with zero rebuild — the missing piece ADR-0039 left open.
- One resolution point (`apiBase()`), one persisted source of truth (`localStorage`),
  identical web behavior (relative `""`). ADR-0028's two-variable web contract is preserved
  intact; localStorage is simply a higher-priority third source that only desktop populates.
- NDJSON streaming needs no special-casing: it is `fetch`, so it inherits `apiBase()` like
  every other call.
- CORS stays explicit and credential-safe; no `*` wildcard regression.

**Trade-offs / limitations (stated explicitly)**
- Call-time resolution costs a `localStorage` read per request. Negligible (synchronous,
  microseconds) and only on the request path, not render.
- `localStorage` is per-webview-origin and per-device: the desktop server URL does not sync
  across machines and is cleared if the user wipes app data. Acceptable — first-launch
  re-entry is a 10-second action.
- Mixed-content (`tauri://` → `http://`) on macOS is an *accepted risk* for v0.6, mitigated
  by the documented `plugin-http` fallback, not eliminated.
- Unsigned artifacts trigger Gatekeeper/SmartScreen warnings; users must right-click-open
  (macOS) / "More info → Run anyway" (Windows). Signing deferred (§4).

**Invariant check**
- **I2/I3/I4:** untouched — this is transport + a gate screen; no layout, no per-token work,
  no editor change.
- **I5 (Obsidian):** unaffected — no vault, frontmatter, or wikilink change.
- **I6 (pluggable inference):** backend-side and unchanged; the desktop app still talks to a
  FastAPI backend that routes to providers via `provider_config`. No provider is hardcoded.
- **I9 (do not reinvent):** Tauri v2 is the planned R13 tool; the 12 clients and the `/status`
  endpoint are reused as-is; no new client/proxy/transport framework introduced.
- No invariant is traded for convenience. **CONFIRMED: none violated.**

---

## 4. Risks (surfaced to orchestrator)

1. **Mixed-content on macOS:** WebKit may block `tauri://localhost` → `http://…` backends; if
   the LIVE macOS build fails against an HTTP backend, wire `@tauri-apps/plugin-http` before
   ship (documented fallback, not yet implemented).
2. **Unsigned binaries:** Gatekeeper/SmartScreen will warn on first open; without notarization
   some macOS 15+ setups make "open anyway" hard to find — set user expectations in DEPLOY.md.
3. **CORS + credentials:** because `allow_credentials=True`, the CORS default must never be
   set to `*` (spec-invalid with credentials); any deployer override via `CORS_ALLOW_ORIGINS`
   must list exact origins or credentialed calls silently fail preflight.

---

## 5. Contract the implementers follow verbatim

1. `frontend/src/api/base.ts` exports `apiBase()`, `getServerUrl()`, `setServerUrl(url)`,
   `clearServerUrl()`, `isTauri()` per §2.1; `apiBase()` priority is
   localStorage → `VITE_API_BASE` → `""`; `setServerUrl` trims + strips trailing slash and
   rejects non-`http(s)` schemes (§2.7.1).
2. Every `frontend/src/api/*Client.ts` uses `` `${apiBase()}/path` `` at call time; after the
   change `grep -rn 'import.meta.env\["VITE_API_BASE"\]' frontend/src/api/*Client.ts` returns
   nothing except within `base.ts`.
3. AppShell renders `ConnectScreen` iff `isTauri() && !getServerUrl()`; ConnectScreen persists
   the URL **only after** a 2xx `GET {url}/status` within timeout (§2.7.2), else stays on the
   gate with a `connect.error.*` message; Header (Tauri only) shows the server + a
   change-server action calling `clearServerUrl()`.
4. `connect.*` keys added to both `en.json` and `it.json` (parity test must pass).
5. `backend/app/config.py` default `cors_allow_origins` adds `tauri://localhost` and
   `http://tauri.localhost`; the default stays an explicit list (never `*`, per §2.4 + risk 3).
6. `src-tauri/tauri.conf.json` gains a `bundle.icon` array from `npx tauri icon`
   (`src-tauri/icons/appicon-1024.png`); targets `dmg`/`app` + `nsis`.
7. CI workflow uses `tauri-apps/tauri-action`, matrix `macos-latest` + `windows-latest`,
   trigger `desktop-v*` tag + `workflow_dispatch`; artifacts unsigned.

## 6. Do NOT (reject any PR that does these)

1. Do NOT re-introduce a module-level `const API_BASE` in any client — resolution must be
   call-time (a stale const breaks "change server" and first-launch binding).
2. Do NOT gate the ConnectScreen on anything other than `isTauri() && !getServerUrl()` — the
   web/PWA path must remain byte-identical (relative `""`).
3. Do NOT set the CORS default to `*` — invalid with `allow_credentials=True` (risk 3).
4. Do NOT transition ConnectScreen to the app on a failed/timed-out `/status` probe.
5. Do NOT call Tauri IPC/commands (`invoke`, `window.__TAURI__` API) in React — `isTauri()`
   is a passive `__TAURI_INTERNALS__` presence check only (ADR-0039 §9.1 carve-out).
6. Do NOT hardcode any backend URL in the frontend or in Tauri config — the desktop URL is
   user-supplied at runtime; the web base stays relative.
