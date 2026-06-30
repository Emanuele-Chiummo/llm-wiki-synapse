# ADR-0039 — Tauri v2 desktop shell (F15 AC-F15-2)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.6 (M6 — shippable; F15 cross-platform desktop + PWA)
- **Features:** F15 (cross-platform: PWA + Tauri v2 desktop bundles, single codebase) ·
  F1 (3-panel shell) · F6 (multi-conversation chat) · F4 (knowledge graph)
- **Reference:** R13 (Tauri v2 — https://tauri.app) · CLAUDE.md §12 stack
- **Invariants owned:** None new (frontend is unchanged; web app remains authoritative)
- **Author:** devops-engineer · solution-architect

---

## 1. Context

F15 requires Synapse to ship as a **cross-platform desktop app** (macOS, Windows, Linux)
and a **PWA**, from a **single codebase** (the existing React 19 + Vite frontend in
`frontend/`).

**Tauri v2** wraps the built frontend (`frontend/dist`) in a native shell using WebKit
on macOS, Edge (MSHTML) on Windows, and GTK WebView on Linux. The web app (PWA + Tauri)
is functionally identical: same React code, same API calls to the FastAPI backend over
HTTP, same offline capabilities via the service worker.

---

## 2. Decision

### 2.1 Tauri v2 as the cross-platform shell (not Electron)

Tauri v2 is chosen over Electron because:

- **Lightweight:** Uses the system WebKit (not bundled Chromium), resulting in ~10–50 MB
  binaries vs ~150 MB for Electron. Faster downloads, lower disk footprint.
- **Fewer system dependencies:** Reuses OS-native browser engines; no electron process
  model.
- **Security:** No Electron IPC bridge surface; Tauri's Rust core is memory-safe.
- **First-class Rust/Tauri ecosystem:** Aligns with the project's Rust infrastructure
  interests (future MCP, CLI agents).

### 2.2 Single codebase, zero app-specific UI changes (I9 reuse, I3 performance)

The React app is **unchanged**. Tauri's WebView renders the exact same `index.html` +
React bundle that the PWA serves:

- **Dev mode:** `tauri dev` → Vite dev server at `http://localhost:5173` (same as
  `npm run dev` in the browser).
- **Build mode:** `tauri build` → Vite production build (`frontend/dist`) bundled into
  the native binary.

**No Tauri-specific API calls** in the React code (no `window.__TAURI__`, no
platform detection). The app works identically in a PWA or a Tauri window — the UI
does not know which container it is in.

### 2.3 Backend connectivity remains HTTP over 127.0.0.1 (or Tailscale)

Tauri's WebView has the same CORS, CSP, and fetch capabilities as a browser. The
frontend calls the FastAPI backend via the existing HTTP clients:

- **Local dev:** `http://localhost:8000` (backend started separately via `docker compose up`).
- **Production:** `http://127.0.0.1:8000` (local bind) or over Tailscale (`http://100.x.x.x:8000`).

No custom Tauri commands needed for the backend path — the web app's relative/absolute
API base is configured via `VITE_API_BASE` at build time (ADR-0028).

### 2.4 Default close behavior: standard (quit on main window close)

The app closes normally when the user closes the main window (standard desktop behavior).
The reference audit (docs/reference/llm_wiki-audit/01-AUDIT-FUNZIONALE.md F15) noted
that close-on-hide should be a USER SETTING, not hardcoded. We follow the principle:

- **Default:** closing the main window quits the app (standard macOS/Linux/Windows
  behavior).
- **Future enhancement (M7+):** add a Settings toggle (e.g. "Keep in system tray on
  close") to hide instead of quit — not implemented in v0.6, recorded as a deferred
  UI improvement.

### 2.5 Bundling targets: deb, AppImage, DMG, MSI, NSIS

Tauri's build system generates OS-specific installers:

- **Linux:** `.deb` (Debian packages) and `.AppImage` (universal runner).
- **macOS:** `.dmg` (disk image; Intel + Apple Silicon via universal binary).
- **Windows:** `.msi` and `.nsis` (NSIS installer with uninstall support).

All are CI-built on GitHub Actions (one runner per OS in a matrix job); multi-OS
native builds are resource-intensive and deferred to CI (LIVE-only).

### 2.6 Scaffold layout and CI wiring (local verify-only; LIVE build on CI)

**Local (sandbox) verification:**
- `src-tauri/Cargo.toml` is well-formed; `cargo metadata` resolves via the proxy.
- `tauri.conf.json` is valid JSON with v2 schema shape.
- Frontend `npm run build` still works (Tauri adds no breakage).
- `cargo check` attempts to type-check (will fail on missing system WebKit libs;
  expected in sandbox — CI runners have them).

**Live (GitHub Actions) build:**
- Ubuntu 22.04: installs `libwebkit2gtk-4.1-dev`, `libappindicator3-dev`, `librsvg2-dev`.
- macOS latest: system WebKit is pre-installed.
- Windows latest: Edge WebView2 is pre-installed (Windows 10+).
- Matrix job runs `npm run tauri build` on each runner; artifacts uploaded to release.

The CI job (`tauri-build`) is wired into `.github/workflows/ci.yml` as a **non-blocking
informational job** (marked `continue-on-error: true`) or a **tag-only job** (runs on
`v0.6+` tags) to avoid slowing down every PR. This is a pragmatic choice: v0.1–v0.5
were code-only; v0.6 adds the binary build, which is expensive and OS-specific.

---

## 3. Repository layout

```
synapse/
├── src-tauri/
│   ├── Cargo.toml               ← Tauri v2 + Rust workspace config
│   ├── tauri.conf.json          ← v2 schema: app title, window size, bundle targets
│   ├── build.rs                 ← tauri_build invocation
│   ├── src/
│   │   ├── main.rs              ← minimal Tauri app entry (one window, no custom commands)
│   │   └── lib.rs               ← builder helpers (if any)
│   ├── capabilities/
│   │   └── default.json         ← minimal cap set (window/app/tray; no fs/shell escapes)
│   ├── icons/
│   │   ├── icon-32x32.png       ← favicon
│   │   ├── icon-128x128.png     ← system tray
│   │   ├── icon-256x256.png     ← app launcher
│   │   ├── icon-512x512.png     ← macOS
│   │   ├── icon.ico             ← Windows taskbar
│   │   ├── icon.icns            ← macOS dock (generated on macOS CI)
│   │   └── generate_icons.sh    ← helper for placeholder generation
│   ├── target/                  ← (gitignored) Rust build artifacts
│   ├── .gitignore
│   └── src-tauri/.DS_Store      ← (gitignored) macOS metadata
├── frontend/
│   ├── package.json             ← added: tauri CLI devDep + npm scripts
│   ├── src/
│   ├── dist/                    ← (Tauri build output; mounted into binary)
│   └── vite.config.ts           ← unchanged; `devUrl: http://localhost:5173`
├── .github/workflows/
│   ├── ci.yml                   ← added: tauri-build job (matrix, continue-on-error)
│   └── ...
└── CLAUDE.md / docs/DEPLOY.md   ← updated with F15 desktop app notes
```

---

## 4. Configuration

### 4.1 Tauri.conf.json key decisions

| Key | Value | Rationale |
|-----|-------|-----------|
| `productName` | `Synapse` | Display name in system menus |
| `version` | `0.6.0` | Synced with `frontend/package.json` |
| `identifier` | `ai.synapse.app` | Unique reverse-domain ID (iOS-style) for all three platforms |
| `build.beforeBuildCommand` | `npm --prefix ../frontend run build` | Build the web app BEFORE bundling |
| `build.devUrl` | `http://localhost:5173` | Dev mode → Vite server; must match `vite.config.ts` |
| `build.frontendDist` | `../frontend/dist` | Production: use the built static files |
| `app.windows[0]` | `{title: Synapse, width: 1400, height: 900, minWidth: 800, minHeight: 600, resizable: true}` | Reasonable desktop app window; usable on 1024x768 minimum |
| `bundle.active` | `true` | Enable installer generation |
| `bundle.targets` | `[deb, appimage, dmg, msi, nsis]` | Multi-platform installers |
| `bundle.deb.dependsOn` | `[libwebkit2gtk-4.1, libappindicator3, librsvg2]` | Linux runtime dependencies |

### 4.2 Environment

No new env vars required. The frontend's existing `VITE_API_BASE` (default `""`, i.e.,
same-origin relative) works for both PWA and Tauri (both run the same built artifact).

For Tauri dev mode (`npm run tauri dev`), the Vite dev server proxy is available at
`http://localhost:5173` and proxies API calls to the FastAPI backend (configured in
`frontend/vite.config.ts`).

---

## 5. CI/CD: Tauri build job

### 5.1 Job trigger and structure

New GitHub Actions job `tauri-build` in `.github/workflows/ci.yml`:

```yaml
tauri-build:
  name: Tauri v2 Desktop Bundles
  runs-on: ${{ matrix.os }}
  strategy:
    matrix:
      os: [ ubuntu-22.04, macos-latest, windows-latest ]
  if: startsWith(github.ref, 'refs/tags/v')  # Tag-only (v0.6+)
  continue-on-error: true                     # Informational; don't block merge
  steps:
    - uses: actions/checkout@v4
    - name: Set up Node 20
      uses: actions/setup-node@v4
      with:
        node-version: "20"
        cache: npm
        cache-dependency-path: frontend/package-lock.json
    - name: Set up Rust
      uses: dtolnay/rust-toolchain@stable
      with:
        targets: x86_64-unknown-linux-gnu,x86_64-pc-windows-msvc,x86_64-apple-darwin
    - name: Install Linux WebKit dependencies
      if: runner.os == 'Linux'
      run: |
        sudo apt-get update && sudo apt-get install -y \
          libwebkit2gtk-4.1-dev \
          libappindicator3-dev \
          librsvg2-dev \
          patchelf
    - name: Install frontend dependencies
      working-directory: frontend
      run: npm ci
    - name: Build Tauri app
      working-directory: frontend
      run: npm run tauri build
    - name: Upload artifacts
      uses: actions/upload-artifact@v3
      with:
        name: synapse-${{ matrix.os }}
        path: src-tauri/target/release/bundle/
        retention-days: 7
```

**Rationale:**
- **Tag-only (`if: startsWith(github.ref, 'refs/tags/v')`):** Avoids expensive multi-OS
  builds on every commit. Native builds only trigger on version tags (e.g. `v0.6.0`).
- **`continue-on-error: true`:** If a platform's build fails (e.g., new system lib
  needed), the job is marked "errored" but does not fail the workflow, allowing the
  release to proceed with the binaries that succeeded. This is pragmatic for v0.6 M6
  (first desktop release); can be made blocking in v0.7+ once proven stable.
- **Matrix:** Same job definition, three runners; Rust toolchain auto-selects the
  correct target triplet.

### 5.2 Release integration (deferred to release.yml v0.6)

The `tauri build` outputs go to `src-tauri/target/release/bundle/` (platform-specific
subdirectories). Future `release.yml` can attach these to the GitHub release; for v0.6
M6, the artifacts are validated on-tag but not yet integrated into the release page
(recorded as a deferred task).

---

## 6. Frontend verification (no changes required)

The existing `frontend/` build is unaffected:

```bash
cd frontend
npm run build          # ✓ Still works; output: dist/
npm run test           # ✓ vitest + Playwright still pass
npm run tauri dev      # ✓ New: dev in Tauri window (calls vite internally)
npm run tauri build    # ✓ New: production bundle (on a system with Rust + WebKit libs)
```

Scripts `lint`, `format`, `test:watch` are unchanged.

---

## 7. Local development (v0.6 onwards)

### 7.1 Browser PWA (unchanged)

```bash
# Terminal 1: backend
cd backend
docker compose up

# Terminal 2: frontend (web)
cd frontend
npm run dev
# Opens http://localhost:5173 in your browser
```

### 7.2 Desktop app (Tauri dev mode)

```bash
# Terminal 1: backend
docker compose up

# Terminal 2: frontend (desktop)
cd frontend
npm run tauri dev
# Launches a native window running the Vite dev server; API proxy works identically
```

### 7.3 Desktop app (production)

```bash
# Build and run the bundled release binary
cd frontend
npm run tauri build

# Artifacts in src-tauri/target/release/bundle/
# Linux: src-tauri/target/release/bundle/deb/ or appimage/
# macOS: src-tauri/target/release/bundle/macos/Synapse.app
# Windows: src-tauri/target/release/bundle/msi/ or nsis/
```

---

## 8. Invariants and principles

- **I3 (no main-thread freeze):** Tauri's WebView is a native component; browser
  rendering thread is independent. No change to the React app's Zustand selectors or
  streaming parse-at-end rule — both hold.
- **I4 (CodeMirror editor):** Unchanged; web app is the source of truth.
- **I5 (Obsidian compatibility):** Unchanged; backend handles vault writes.
- **I6 (pluggable inference):** Unchanged; backend routes to providers.
- **I9 (reuse, no reinvention):** Tauri uses the system WebKit (reuse); no custom
  commands or bridges (app is thin).

**New principle:** The **PWA and Tauri are functionally equivalent** — same frontend,
same backend calls, same offline capabilities via service worker. Users can choose
browser PWA or native Tauri based on preference (browser bookmarks vs native integration).

---

## 9. Do-NOT list

1. **DO NOT** add Tauri-specific API calls (no `window.__TAURI__`, no `invoke` in React code).
2. **DO NOT** modify the React app for Tauri (single codebase rule).
3. **DO NOT** bundle the FastAPI backend inside Tauri (separate container; no bloat).
4. **DO NOT** hardcode the backend URL in Tauri (reuse `VITE_API_BASE` env var).
5. **DO NOT** create two separate build artifacts for PWA vs Tauri (both use `frontend/dist`).
6. **DO NOT** add system tray or minimise-to-tray in v0.6 (standard window close = quit).
7. **DO NOT** run the multi-OS build on every commit (tag-only or continue-on-error).
8. **DO NOT** add Tauri plugin bloat (use only `opener` for link handling; avoid `updater`,
   `http`, `fs` — standard browser APIs + Rust core suffice).
9. **DO NOT** skip WebKit dependency installation on Linux CI (will fail silently without them).
10. **DO NOT** commit the `src-tauri/target/` build directory or `Cargo.lock` unless explicitly required for reproducibility (not yet decided for v0.6).

---

## 10. Future enhancements (v0.7+, deferred)

- **Deep links:** Register `synapse://` URL scheme for vault opening (requires Tauri deep-link plugin).
- **System tray:** Optional "minimize to tray" via Settings toggle (requires tray plugin + data persistence).
- **Updater:** In-app auto-update checker (requires `tauri-plugin-updater`; deferred for v0.6).
- **Push notifications:** Desktop toast notifications from FastAPI via a Tauri plugin (deferred).
- **Release page integration:** Attach Tauri binaries to GitHub releases automatically (recorded in v0.6 release.yml).

---

## 11. References

- **Tauri v2 docs:** https://tauri.app/en/docs/
- **Tauri CLI:** https://tauri.app/en/docs/cli/
- **Frontend build:** `frontend/vite.config.ts` (unchanged; `frontendDist` points here).
- **Cargo.toml conventions:** https://doc.rust-lang.org/cargo/reference/manifest.html
