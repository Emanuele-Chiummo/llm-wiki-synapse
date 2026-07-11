# Brand v1.0 integration (2026-07-10)

> Source: owner-provided `Brand/` (absolute: `<repo>/Brand/`) — `Synapse Brand Guidelines.pdf`
> + `png/` (app-icon light + dark, favicon, mark variants). This is the **BR (branding) stream**
> of the v1.5 program, run in parallel with the feature phases.

## Facts (from the guidelines PDF)

**Concept:** neural connection + linked-notes graph + a hidden "S". Two terminals firing across a
central junction. Tagline: *"Connect everything."*

**Color (light):**
- Blue `#1D4ED8` · Indigo `#4338CA` · Violet `#7C3AED` · Accent·light `#2563EB`
- Gradient runs **diagonally, bottom-left → top-right**. Max 2–3 colors, never rainbow.

**Color (dark icon):** background `#0B0E20 → #211A4F` (deep indigo-black); mark
`#58A6FF → #C084FC` (brighter blue→violet — "a signal firing in the dark").

**App icon:** squircle, **corner radius 22%**. Master icon at **≥48px**; below **24px** use the
**simplified favicon variant** (three nodes, one thicker S, no satellites). Keep gradient BL→TR;
give clear space. Do NOT rotate/reflow, add text inside, recolor, or stretch/skew.

**Typography:** **Geist** — Regular 400 / Medium 500 / Semibold 600. Wordmark = Geist Semibold,
letter-spacing **−0.02em**. Mono reserved for labels, code, metadata.

**Provided assets (`Brand/png/`):**
- `app-icon/synapse-icon-{16,32,64,180,256,512,1024}.png` — light squircle icon.
- `app-icon-dark/synapse-icon-dark-{...}.png` — dark squircle icon.
- `favicon/synapse-favicon-{16,32,48}.png` — simplified small-size mark.
- `mark/synapse-mark-{blue,ink,lightblue,white}-1024.png` — the S-mark, no tile.

## Alignment vs current Synapse
- Accent tokens already match: `--syn-accent` `#2563eb` (light) / `#58a6ff` (dark) in
  `frontend/src/styles/theme.css`. **No accent change needed.**
- New/changed: the **logo art** (new master mark), the **dark app-icon variant**, **Geist**
  wordmark, and formalized **gradient tokens**.

## Integration targets (BR stream)
1. **Copy brand source** into the repo: `docs/assets/brand/` (light+dark+favicon+mark PNGs) +
   keep the PDF under `docs/assets/brand/`. Single source of truth in-repo.
2. **Web app icon / favicon:** `frontend/public/favicon.svg` (+ any `favicon.png`) → the simplified
   favicon variant; wire `<link rel="icon">` to serve the right size. Update
   `frontend/src/assets/synapse-appicon.svg` + `docs/assets/synapse-appicon.svg` /
   `synapse-logo.svg` to the new master mark (rebuild SVG from the new art, or embed PNG).
3. **In-app logo/mark:** the inline logo SVG in `frontend/src/components/connect/ConnectScreen.tsx`
   and `TokenGate.tsx` → new mark. Must be **theme-aware**: light mark on light, white-knockout
   mark on dark (use `mark/synapse-mark-white` on dark surfaces).
4. **Tauri desktop icons:** replace `src-tauri/icons/*` (Square*Logo.png, appicon-1024.png,
   StoreLogo.png, `icon.icns`, `icon.ico`) from the light master; verify `tauri.conf.json` icon list.
5. **macOS tray/menu-bar icon** (v1.4 system-tray): use a monochrome/template version so it adapts
   to the menu-bar theme.
6. **iOS AppIcon:** `ios/Synapse/Assets.xcassets/AppIcon.appiconset` — light (+ dark/tinted slots
   if the iOS build uses them).
7. **Theme tokens:** add brand gradient tokens to `theme.css`
   (`--syn-brand-grad: linear-gradient(to top right, #1D4ED8, #4338CA, #7C3AED)`;
   dark-icon variant tokens). Do NOT change existing `--syn-accent` values.
8. **Typography — Geist:** self-host Geist (Regular/Medium/Semibold) locally (no external CDN — CSP
   / offline-first), wire as the UI font stack; wordmark Geist Semibold `-0.02em`; keep the current
   mono for labels/code.

## Guardrails
- Self-hosted / offline-first: **no external font or asset CDNs** (bundle Geist locally).
- Dark-mode variants are mandatory (icon + in-app mark).
- Keep changes on the BR worktree branch; merge into `release/v1.5.0-llmwiki-parity` after review + preview.
