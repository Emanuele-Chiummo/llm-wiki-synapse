# ADR-0049 — Desktop auto-update over GitHub Releases (unified `v*` tag, minisign-verified)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v0.6 (M6 — shippable; F15 cross-platform desktop packaging)
- **Features:** F15 (cross-platform: Tauri v2 desktop bundles + self-update, macOS + Windows) ·
  F16 (settings/i18n IT/EN — `desktop.update.*` namespace)
- **Builds on:** ADR-0039 (Tauri v2 shell — scaffold, CI, single codebase; no `invoke`/
  `window.__TAURI__` in React) · ADR-0047 (desktop runtime server URL, `base.ts`, `isTauri()`
  container check, Connect gate) · ADR-0048 (desktop pack: `tauri-plugin-notification`,
  `capabilities/default.json`, Tauri-only UI guarded by `isTauri()`)
- **Reference:** R13 (Tauri v2 — https://tauri.app; `tauri-plugin-updater`,
  `tauri-plugin-process`, `tauri-action`, minisign updater signatures) ·
  CLAUDE.md §3 (invariants I5/I6/I7/I9), §11 (branch/commit conventions), §12 (no hardcoded
  config in code)
- **Invariants owned:** I5 (Obsidian compat — unaffected; pure client-side update transport) ·
  I6 (pluggable inference — backend-side; not touched by desktop self-update) ·
  I7 (loops bounded — the update check is a **single startup check, no polling loop**) ·
  I9 (do not reinvent — Tauri's first-party updater over GitHub Releases; no bespoke
  update server, no third-party update framework). No invariant is traded for convenience.
- **Author:** solution-architect
- **Implementers:** devops-engineer (`.github/workflows/desktop-release.yml`,
  `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`, `src-tauri/capabilities/default.json`,
  `src-tauri/src/main.rs`, version bump + `frontend/package.json` sync) · frontend-engineer
  (`components/**` update banner/dialog, `store/**` update state, `en.json`/`it.json`
  `desktop.update.*`) · tech-writer (D6b DEPLOY: release/tag procedure + key-loss caveat;
  D6a USER: what the update banner does)

---

## 1. Context

ADR-0047 and ADR-0048 delivered the packaged Tauri v2 desktop app (macOS-arm64, Windows-x64),
unsigned at the OS level (Gatekeeper/SmartScreen right-click-open accepted per ADR-0047 §4),
with `tauri-plugin-notification` already wired. A `v0.6.0` GitHub release exists (created
manually with `gh`). There is currently **no in-app update path**: an installed client stays
frozen at the version it was installed with, and the owner would have to hand-deliver each new
build.

The owner explicitly requested a **fully GitHub-hosted** desktop auto-update flow — no
self-hosted update server, no third-party service. Tauri v2 ships a first-party updater
(`tauri-plugin-updater`) that reads a static `latest.json` manifest, verifies each artifact
against an app-embedded **minisign public key**, downloads the platform bundle, and installs
it; `tauri-action` produces the updater artifacts and `latest.json` and can attach them to a
GitHub release. This is the canonical R13 mechanism (I9: reuse, do not reinvent).

Verified facts (re-confirmed against the tree at this ADR's date):

- **CI** (`.github/workflows/desktop-release.yml`): triggers on `desktop-v*` tags +
  `workflow_dispatch`; matrix `macos-latest` (arm64) + `windows-latest` (x64); uses
  `tauri-apps/tauri-action@v0` with `tauriScript: node frontend/node_modules/@tauri-apps/cli/tauri.js`
  (invoked from repo root so the CLI finds `./src-tauri`); `tagName`/`releaseName` set only on
  `desktop-v*` tag pushes (`workflow_dispatch` = build-only, empty `tagName`). No signing env
  vars are passed today; no `latest.json`/updater artifacts are produced.
- **`src-tauri/tauri.conf.json`**: `version` `"0.6.0"`, identifier `ai.synapse.app`,
  `security.csp: null`, bundle targets `["app","dmg","nsis"]`, `bundle.icon` present.
  **No `bundle.createUpdaterArtifacts`; no `plugins.updater` block.**
- **`src-tauri/Cargo.toml`**: `tauri = "2"`, `tauri-plugin-notification = "2"`. **No
  `tauri-plugin-updater`, no `tauri-plugin-process`.**
- **`src-tauri/capabilities/default.json`** (v1, window `"main"`): core window/app perms +
  `notification:default`. **No `updater:default`, no `process:allow-restart`.**
- **`src-tauri/src/main.rs`**: `tauri::Builder::default().plugin(tauri_plugin_notification::init())`
  … `.setup(...)` … `.run(...)`. The updater/process plugins are **not** initialised. (`lib.rs`
  has a dead `run()` stub that is not the active entry point — main.rs is.)
- **Version is duplicated** in `src-tauri/tauri.conf.json` and `src-tauri/Cargo.toml`
  (both `0.6.0`) and in `frontend/package.json`.
- **Updater keypair already generated** (minisign): private key + password live in GitHub
  Actions secrets `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the
  private key is **not** in the repo (local copy in `~/.tauri/`). Public key (to embed):
  `dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDY2NTI4RkIwMTRFOUUyNzEKUldSeDR1a1VzSTlTWnJSRGtqa0JyWml5ZEltdlVGM1F4NmErSkUxZHdiQjdGdlRCM1NoTHRlQUQK`
- All existing desktop-only UI (ADR-0047/0048) is guarded by `isTauri()`
  (`__TAURI_INTERNALS__` presence check), so a browser build renders every desktop branch
  inert. The update UI follows the same guard.

The updater's security guarantee is **independent of Apple/Microsoft OS signing**: Tauri
verifies **our** minisign signature over each artifact before installing. So the unsigned-at-OS
posture of ADR-0047 does not weaken update integrity — the private key is the only trust root
for the update chain, which makes key custody the load-bearing risk (§4).

---

## 2. Decision

Ratify the contract U1–U6 verbatim, with two consolidations recorded (not amendments to
intent): the version source-of-truth reconciliation (§2.5, because Cargo.toml also carries the
version) and the release-notes source for the in-app dialog (§2.4). All six contract points are
accepted.

### 2.1 (U1) Unified release channel — `v*` tag on GitHub Releases — RATIFIED

`.github/workflows/desktop-release.yml` moves to **one tag per product release** on the
pattern `v*` (e.g. `v0.7.0`). The `desktop-v*` trigger is **removed** — there is a single
release channel for the whole product, and a desktop build/release is produced from the same
`v*` tag.

- Matrix stays `macos-latest` (arm64) + `windows-latest` (x64); `tauri-action@v0` invoked with
  the existing `tauriScript` (`node frontend/node_modules/@tauri-apps/cli/tauri.js` from repo
  root — do NOT `npm --prefix frontend run`, which would chdir and break `./src-tauri`
  detection; unchanged from ADR-0047/0039).
- `tauri-action` **creates/updates the GitHub release for the tag**, uploads the OS bundles
  (`.app.tar.gz` + `.sig`, NSIS `.exe` + `.sig`) **and** `latest.json`
  (`includeUpdaterJson: true`), signing the updater artifacts with the two secrets:
  `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` passed as `env` to the
  build step. `contents: write` permission is retained.
- `tagName`/`releaseName` are gated on `startsWith(github.ref, 'refs/tags/v')`;
  `workflow_dispatch` remains build-only (empty `tagName`, no release). `releaseDraft: false`,
  `prerelease: false` as today.
- **Two-runner `latest.json` merge:** because both runners target the same release for one tag,
  the workflow must produce a single `latest.json` carrying **both** platform entries
  (`darwin-aarch64`, `windows-x86_64`). `tauri-action`'s `includeUpdaterJson` merges platform
  entries into the release's `latest.json` across matrix jobs; the implementer MUST verify the
  final release asset contains **both** platforms before the release is considered good
  (acceptance check in §5.1). The manual `v0.6.0` release predates the updater and has no
  `latest.json` — the **first** `v*` release cut under this ADR (≥ `v0.7.0`) is the first one
  installed clients can update to.

### 2.2 (U2) `tauri.conf.json` — updater artifacts + endpoint — RATIFIED

- `bundle.createUpdaterArtifacts: true` (this is what makes `tauri build` emit the
  `.app.tar.gz`/`.sig` + NSIS `.sig` updater bundles alongside the installers).
- `plugins.updater` block with:
  - `pubkey`: the base64 minisign public key above (embedded in the binary — this is the
    trust root; it is public by design and safe to commit).
  - `endpoints`:
    `["https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases/latest/download/latest.json"]`
    — GitHub's `releases/latest/download/<asset>` redirect always resolves to the most recent
    non-prerelease release's asset, so the endpoint is **static** and needs no bump per release
    (I9: no update server; GitHub is the host).
- The public key committed in config MUST match the private key in the GitHub secrets, or every
  client will reject every update (signature check fails). This pairing is a release-time
  invariant (§4 risk 1, §5.1 acceptance).

### 2.3 (U3) Rust plugins + capabilities — RATIFIED

- `src-tauri/Cargo.toml`: add `tauri-plugin-updater = "2"` and `tauri-plugin-process = "2"`
  (process plugin provides the post-install relaunch).
- `src-tauri/src/main.rs`: initialise **both** in the builder chain, alongside the existing
  notification plugin:
  `.plugin(tauri_plugin_updater::Builder::new().build())` and
  `.plugin(tauri_plugin_process::init())`. No new command handlers are registered
  (`invoke_handler` stays empty) — the frontend calls the plugins' JS APIs, consistent with
  ADR-0039's "no bespoke `invoke` commands in React" posture (these are first-party plugin
  APIs, not custom IPC commands).
- `src-tauri/capabilities/default.json`: add `"updater:default"` and
  `"process:allow-restart"` to the `permissions` array (window `"main"`), next to the existing
  `notification:default`. `process:allow-restart` is the **minimal** process permission — do
  NOT add `process:allow-exit` or broader process perms (least privilege; the only process
  action needed is relaunch after install).

### 2.4 (U4) Frontend update UX — startup check, single prompt, no polling — RATIFIED

All update UI is **Tauri-only**, every plugin call **behind `isTauri()`** with the plugin JS
modules **dynamically imported** inside the guard (so the web/PWA bundle never imports
`@tauri-apps/plugin-updater`/`plugin-process` and every branch is dead code in a browser —
identical pattern to ADR-0048 §2.4c notifications).

- **When:** on app start, **after the shell has rendered**, run `check()` **once**,
  **non-blocking** (fire-and-forget; a failed/timed-out check MUST NOT block or crash the app —
  catch and no-op). **No periodic polling, no interval, no background loop** — one check per
  process start. This is the I7 bound: the "loop" is a single iteration by construction
  (§2.7 invariant check).
- **If an update is available:** show a **dismissible** banner/dialog with the new
  `{{version}}`, the release notes (`update.body` from the manifest), and two actions —
  **Update now** / **Later**. i18n namespace `desktop.update.*` in both `en.json` and `it.json`
  (keys at least: `available` with `{{version}}` interpolation, `notes`/notes-label,
  `updateNow`, `later`, `downloading` (+progress), `installing`, `error`; parity test must
  pass).
- **On "Update now":** call `downloadAndInstall()` and surface **progress state**
  (bytes/percent from the plugin's progress callback) in the UI; on completion call
  `relaunch()` (from `@tauri-apps/plugin-process`) to restart into the new version.
- **On "Later":** dismiss and **do not re-prompt until the next app start** (no re-nag within
  the session; the next startup `check()` will surface it again). No persistence of "skip this
  version" is required for v0.6 — dismissal is session-scoped by design (simplest correct
  behaviour; a per-version mute can be a future ADR if it proves annoying).

### 2.5 (U5) Version source of truth — RATIFIED, with a reconciliation

- The updater compares the running app version against `latest.json`'s `version`; the running
  version is the **`version` field of `src-tauri/tauri.conf.json`** (Tauri's authoritative app
  version). This is the source of truth, bumped per release (`0.7.0`, …).
- **Reconciliation (recorded, not a change of intent):** the version is currently duplicated in
  **three** places — `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`, and
  `frontend/package.json`. `tauri.conf.json` `version` is authoritative for the updater; the
  release procedure MUST bump **all three together** so they never diverge (a mismatched
  Cargo.toml version produces a binary whose reported version disagrees with the manifest,
  breaking the compare). The tag (`vX.Y.Z`) MUST equal the `tauri.conf.json` version. This
  three-way bump is part of the DEPLOY release checklist (D6b) and the §5.1 acceptance.

### 2.6 (U6) macOS unsigned caveat — RATIFIED (documented, not mitigated here)

The updater artifacts are **ad-hoc-signed `.app` tarballs** (no Apple Developer ID / no
notarization — ADR-0047 §4 posture). Tauri's updater verifies **our minisign signature** over
each artifact, which is **independent of Apple notarization**: update integrity does not depend
on OS-level signing. This is acceptable per the ADR-0047 unsigned posture.

**Documented consequence (D6b):** the minisign private key
(`TAURI_SIGNING_PRIVATE_KEY` + password; local copy `~/.tauri/`) is the **sole trust root** for
the update chain. **If the private key is lost, all installed clients can no longer receive
updates** — a new keypair produces signatures that embedded clients reject, so the only recovery
is a manual re-install of a build carrying the new public key. Therefore the key MUST be backed
up securely off-machine (and the GitHub secret must not be rotated without a coordinated
re-release). This is the primary operational risk (§4 risk 1). Additionally, macOS
first-**install** (not update) still triggers Gatekeeper (unchanged from ADR-0047 §4) — the
updater path only applies to an already-installed, already-trusted app updating itself.

### 2.7 Invariant check

- **I1 (incremental index):** N/A — this is a desktop client self-update; no vault scan, no
  Postgres/Qdrant writes. **Unaffected.**
- **I2 / I3 / I4 (graph / per-token / editor):** untouched — no graph, no chat streaming, no
  editor surface is involved; the update UI is a banner/dialog rendered once on an app-lifecycle
  event, not per token or per frame. **Hold.**
- **I5 (Obsidian compat):** no vault, frontmatter, or wikilink change. **Unaffected.**
- **I6 (pluggable inference):** backend-side and unchanged; no provider is referenced,
  configured, or hardcoded anywhere in the update path. **Holds.**
- **I7 (loops bounded):** the update check is a **single call on startup** — no polling
  interval, no retry loop, no unbounded iteration. It is bounded to exactly one iteration per
  process start by construction; `downloadAndInstall` is a single one-shot download. No
  `max_iter`/`token_budget` needed because there is no loop. **Holds — explicitly.**
- **I8 (docs-as-DoD):** D6b (DEPLOY) gains the release/tag procedure + three-way version bump +
  key-loss caveat; D6a (USER) gains the update-banner description. C4 (D1): topology does not
  change (no new container/component — GitHub Releases is an existing external, the desktop app
  is an existing client); **no C4 update required**, recorded here so the docs gate is not
  falsely tripped.
- **I9 (do not reinvent):** uses Tauri's first-party updater over **GitHub Releases** as the
  static host — no bespoke update server, no third-party updater. Reuses the existing
  `tauri-action` CI and the existing minisign keypair. **Holds.**
- No invariant is traded for convenience. **CONFIRMED: none violated.**

---

## 3. Consequences

**Positive**
- Installed desktop clients self-update from GitHub Releases with zero self-hosted
  infrastructure; the owner cuts a `v*` tag and every client picks it up on next launch.
- One unified release channel (`v*`) for the whole product replaces the separate `desktop-v*`
  channel — simpler mental model, one tag per release.
- Update integrity is cryptographically guaranteed by our minisign key, **independent** of the
  (absent) Apple/Microsoft OS signing — the ADR-0047 unsigned posture is preserved without
  weakening the update chain.
- The startup-only, no-polling design keeps the client simple and bounded (I7) and the update
  UI web-inert (guarded by `isTauri()` + dynamic import), so the PWA/web bundle is unchanged.

**Trade-offs / limitations (stated explicitly)**
- **Single trust root:** losing the minisign private key permanently breaks updates for all
  installed clients (recovery = manual re-install). This is the cost of client-verified updates;
  mitigated only by disciplined key backup, not by design.
- **Startup-only check:** a long-running desktop session will not learn about a release cut
  mid-session until the next launch. Accepted as the simplest bounded behaviour; periodic
  polling was deliberately rejected (I7 simplicity, avoids a background loop).
- **Three-way version duplication** (tauri.conf.json / Cargo.toml / package.json) is a manual
  bump hazard; mitigated by the DEPLOY checklist + §5.1 acceptance, not by tooling in this ADR.
- **First install still unsigned:** the updater does not solve Gatekeeper/SmartScreen on the
  initial download (ADR-0047 §4 unchanged) — only the self-update of an already-trusted app.
- **`releases/latest` semantics:** the static endpoint always points at the newest
  non-prerelease; a broken/incomplete `latest.json` (e.g. only one platform merged) would offer
  a bad update — hence the two-platform acceptance check (§5.1).

---

## 4. Risks (surfaced to orchestrator)

1. **Private-key loss / pubkey-privkey mismatch = broken update chain.** If
   `TAURI_SIGNING_PRIVATE_KEY` is lost, or the committed `plugins.updater.pubkey` does not pair
   with the secret used to sign, **every** client rejects **every** update, and recovery is a
   manual re-install. Back up the key off-machine; verify the pubkey/privkey pairing at first
   `v*` release (§5.1). Highest-severity, lowest-reversibility risk.
2. **Incomplete `latest.json` across the two-runner matrix.** Both `macos-latest` and
   `windows-latest` write to the same release for one tag; if the merge misses a platform,
   half the fleet gets no update (or a 404 on download). Acceptance gate: the release's
   `latest.json` MUST list **both** `darwin-aarch64` and `windows-x86_64` with valid
   signatures before the release is announced.
3. **Version drift** between `tauri.conf.json` and `Cargo.toml`/`package.json`. A binary whose
   compiled version disagrees with the manifest either offers a spurious update or never
   updates. The three-way bump is a manual step; enforce via the DEPLOY checklist and reject any
   release PR that bumps only one.

---

## 5. Contract the implementers follow verbatim

### 5.1 Release / CI (devops-engineer)

1. `.github/workflows/desktop-release.yml`: replace the `desktop-v*` tag trigger with `v*`;
   gate `tagName`/`releaseName` on `startsWith(github.ref, 'refs/tags/v')`; keep
   `workflow_dispatch` build-only. Keep the matrix (`macos-latest` arm64 + `windows-latest`
   x64) and the `tauriScript: node frontend/node_modules/@tauri-apps/cli/tauri.js` invocation.
2. Pass to the `tauri-action` build step (`env` or `with`): `includeUpdaterJson: true`,
   `TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}`,
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}`.
   Retain `permissions: contents: write` and `GITHUB_TOKEN`.
3. **Acceptance (release gate):** the cut `v*` release contains, per platform, the OS bundle +
   `.sig`, and a single `latest.json` listing **both** `darwin-aarch64` and `windows-x86_64`
   with signatures that verify against the committed pubkey. Version in `latest.json` == the tag
   == `tauri.conf.json.version` == `Cargo.toml.version` == `frontend/package.json.version`.

### 5.2 Tauri config + Rust (devops-engineer)

4. `src-tauri/tauri.conf.json`: add `"bundle": { …, "createUpdaterArtifacts": true }` and a
   `"plugins": { "updater": { "pubkey": "<base64 pubkey above>", "endpoints":
   ["https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases/latest/download/latest.json"] } }`
   block. Bump `version` per release.
5. `src-tauri/Cargo.toml`: add `tauri-plugin-updater = "2"` and `tauri-plugin-process = "2"`;
   keep `version` in sync with tauri.conf.json.
6. `src-tauri/src/main.rs`: chain
   `.plugin(tauri_plugin_updater::Builder::new().build())` and
   `.plugin(tauri_plugin_process::init())` alongside the existing notification plugin; leave
   `invoke_handler` empty.
7. `src-tauri/capabilities/default.json`: add `"updater:default"` and
   `"process:allow-restart"` to `permissions` (only these two — no broader process perms).

### 5.3 Frontend (frontend-engineer)

8. Update-check module: on app start, **after render**, run `check()` **once**, non-blocking,
   inside an `isTauri()` guard, with `@tauri-apps/plugin-updater` / `@tauri-apps/plugin-process`
   **dynamically imported inside the guard**. Catch and no-op on error/timeout. **No interval,
   no polling.**
9. If update available → dismissible banner/dialog: `{{version}}` + `update.body` notes +
   **Update now** / **Later**. Update now → `downloadAndInstall()` with progress state →
   `relaunch()`. Later → dismiss; re-surface only on next app start (session-scoped).
10. `desktop.update.*` keys in both `en.json` and `it.json` (parity test passes): at minimum
    `available` (with `{{version}}`), a notes label, `updateNow`, `later`, `downloading`
    (+ progress), `installing`, `error`.

### 5.4 Docs (tech-writer)

11. D6b (DEPLOY): the `v*` tag release procedure, the three-way version bump, `includeUpdaterJson`
    + the two signing secrets, and the **key-loss caveat** (§2.6 — private key is the sole trust
    root; back it up; loss = manual re-install for all clients). D6a (USER): the update banner
    (what it offers, Update now vs Later). No C4 change (topology unchanged).

## 6. Do NOT (reject any PR that does these)

1. Do NOT add a polling interval / background update loop — startup check only, exactly one
   `check()` per process start (I7). A timer/interval is a reject-on-review condition.
2. Do NOT commit or embed the **private** minisign key or its password anywhere in the repo or
   in `tauri.conf.json` — only the **public** key is embedded; signing uses the GitHub secrets.
3. Do NOT block app startup on the update check, and do NOT crash on a failed/timed-out check —
   it must be fire-and-forget and caught.
4. Do NOT import `@tauri-apps/plugin-updater`/`plugin-process` at module top level or call them
   outside an `isTauri()` guard — dynamic import inside the guard, so the web/PWA bundle stays
   inert (ADR-0039 §9.1 / ADR-0048 §2.4c pattern).
5. Do NOT keep the `desktop-v*` trigger or run two release channels — `v*` is the single unified
   channel (U1).
6. Do NOT bump the version in only one of the three files — tauri.conf.json / Cargo.toml /
   package.json move together and equal the tag (I7/§5.1 acceptance; version drift breaks the
   compare).
7. Do NOT broaden the process capability beyond `process:allow-restart` (least privilege).
8. Do NOT hardcode any inference provider or touch the inference layer (I6 untouched).
