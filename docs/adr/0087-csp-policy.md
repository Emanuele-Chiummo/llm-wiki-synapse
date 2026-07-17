# ADR-0087 — Content Security Policy (SEC-CSP-1)

- **Status:** Accepted
- **Date:** 2026-07-17
- **Invariants touched:** I8 (docs-as-DoD: Playwright E2E spec added, ADR written)
- **Feature refs:** F8 (LaTeX rendering), F16 (themes), F15 (cross-platform / CI)
- **Workstream:** 2.0.0 — "One engine" SEC-CSP-1

## Context

`backend/app/security_headers.py` (added in the ADR-0052 hardening pass) explicitly deferred
adding a `Content-Security-Policy` header:

> *Intentionally NO Content-Security-Policy here: a correct CSP for the SPA (inline styles,
> KaTeX, workers) needs its own tested policy and would risk breaking the UI — that is a
> separate, deliberate change, not a zero-risk header.*

`frontend/nginx.conf.template` had only the minimal directive `frame-ancestors 'none'` — not
a real script-src / style-src policy.

This ADR is the "separate, deliberate change" that was deferred. It covers the investigation,
findings, the chosen policy, and the Playwright test suite that proves the policy works in both
light and dark themes.

## Investigation

### 1. Existing CSP infrastructure

- `security_headers.py`: no CSP, deliberately deferred.
- `nginx.conf.template`: `Content-Security-Policy: "frame-ancestors 'none'"` only.
- `vite.config.ts`: no headers configured.

### 2. KaTeX usage and CSP requirements (F8)

`frontend/src/components/chat/renderMarkdown.ts` imports KaTeX as:

```typescript
import katex from "katex";
import "katex/dist/katex.min.css";
```

and calls:
```typescript
katex.renderToString(latex, {
  displayMode: true,
  throwOnError: false,
  trust: false,
  output: "htmlAndMathml",
});
```

`output: "htmlAndMathml"` produces HTML like:
```html
<span class="katex-display">
  <span class="katex">
    <span class="katex-html" aria-hidden="true">
      <span class="base">
        <span class="strut" style="height:0.6944em;"></span>
        ...
```

Every `<span>` has an inline `style` attribute for positioning. **This is intrinsic to KaTeX's
HTML output mode and cannot be avoided without switching to a different output mode.**

KaTeX options for avoiding inline styles:
- `output: "mathml"` — produces only MathML, no inline styles; rendering quality depends on
  browser MathML support (incomplete in Safari ≤ 15, absent in some Firefox versions).
- `output: "svg"` (no such built-in option; KaTeX HTML mode is the only HTML output).
- `output: "htmlAndMathml"` with `'unsafe-inline'` for style-src — current choice.

**Decision**: keep `output: "htmlAndMathml"` (best rendering quality, KaTeX's recommended
mode) and accept `style-src 'unsafe-inline'`. This is a widely accepted tradeoff.

### 3. Other sources requiring `style-src 'unsafe-inline'`

Independent of KaTeX, two additional sources require `style-src 'unsafe-inline'`:

**a) `frontend/index.html` inline `<style>` block** — the HTML template contains a 70-line
inline `<style>` section with the baseline reset, `box-sizing`, `font-family`, `overflow`,
and markdown body styles. This is rendered as an inline style block in the HTML document.
Under `style-src 'self'` (without `'unsafe-inline'`), this inline style block would be blocked
in all browsers.

Mitigation options: (1) move the block to a separate CSS file imported by Vite — viable but
requires ensuring the file loads before the React app mounts to avoid FOUC; (2) hash the block
(`style-src 'sha256-<hash>'`) — fragile, breaks whenever the block changes; (3) accept
`'unsafe-inline'` — chosen here.

**b) React inline `style={{}}` props** — grep finds 1,765 occurrences of `style={{` in TSX
files. Key uses: `react-resizable-panels` (uses inline styles for panel widths/heights),
nav-tree type-color badges (`style={{ color: var(--syn-type-*) }}`), and numerous layout
adjustments. These are element-level `style` attributes in the rendered DOM, which also
require `style-src 'unsafe-inline'`.

Mitigation: replace all inline styles with CSS classes or CSS variables — significant
refactoring; deferred.

### 4. Script sources

The production Vite build emits only external hashed JS chunks:
```html
<script type="module" crossorigin src="/assets/main-[hash].js"></script>
```

No inline scripts are injected. `vite-plugin-pwa` is configured with `injectRegister: null`
(SW registration handled in `main.tsx` via an external module import). **`script-src 'self'`
is sufficient with no caveats.**

### 5. Font sources

KaTeX fonts are bundled by Vite from `katex/dist/fonts/` and served at the same origin. The
`@font-face` declarations in the bundled `katex.min.css` use relative paths that Vite rewrites
to hashed asset paths (e.g. `/assets/KaTeX_Main-Regular-[hash].woff2`). **`font-src 'self'`
is sufficient.** `data:` is included as a safety net for any CSS-embedded font data URIs.

### 6. Image sources

SVG favicons and PNG icons are served from the same origin. Potential data-URI images may
appear in wiki page content rendered in the preview panel. Sigma.js graph exports use `blob:`
URLs. **`img-src 'self' data: blob:`** covers all cases.

### 7. Connect sources (XHR / fetch / SSE / WebSocket)

In production (nginx), all API paths (`/graph`, `/pages`, `/chat`, `/search`, …) are
reverse-proxied by nginx to the backend, so browser requests go to the same origin.
**`connect-src 'self'` is sufficient for production.**

In development and CI, the frontend is served at `:5173` and the backend at `:8000` — different
origins. `VITE_API_BASE=http://localhost:8000` is embedded at build time; all fetch/XHR/SSE
calls target `http://localhost:8000`, which would be blocked by `connect-src 'self'`.

**Decision**: in `vite.config.ts` (dev and preview headers), use:
```
connect-src 'self' http://localhost:* ws://localhost:* wss://localhost:*
```
Production nginx uses `connect-src 'self'` only.

### 8. Worker sources

The PWA service worker is a same-origin external file (`/sw.js`). `worker-src 'self'` is
sufficient. `blob:` is included for future-proofing.

## Decision

The following CSP is applied across all layers:

### Production (nginx — `nginx.conf.template`)
```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
font-src 'self' data:;
img-src 'self' data: blob:;
connect-src 'self';
worker-src 'self' blob:;
frame-ancestors 'none';
object-src 'none';
base-uri 'self'
```

### Dev / CI (Vite server + preview — `vite.config.ts`)
Same as above, except `connect-src` is broadened for cross-origin localhost:
```
connect-src 'self' http://localhost:* ws://localhost:* wss://localhost:*
```

### FastAPI middleware (`security_headers.py`)
Production policy (same as nginx). Applied to API responses as defense-in-depth; the primary
enforcement path for the SPA document is nginx / vite.

## Rationale for `style-src 'unsafe-inline'`

`'unsafe-inline'` in `style-src` is a weaker protection than `'unsafe-inline'` in `script-src`.
With `style-src 'unsafe-inline'`:
- An attacker who can inject arbitrary CSS can read data via CSS-based side channels (e.g.
  timing attacks, selector exfiltration) or cause visual misleading (clickjacking via CSS
  overlay). They CANNOT execute JavaScript.
- The complementary `script-src 'self'` (without `'unsafe-inline'`) is the critical protection:
  it prevents XSS via inline script injection.

This tradeoff is widely accepted for SPAs that use KaTeX or CSS-in-JS patterns, and is
consistent with the policies of major React-based applications.

**This is explicitly NOT a shortcut.** The three independent sources (KaTeX inline styles,
React inline style props, index.html `<style>` block) all genuinely require it. Eliminating
`'unsafe-inline'` from `style-src` would require: switching KaTeX to MathML-only output
(quality regression), eliminating all 1,765 React inline style={{}} props (major refactoring),
and moving the index.html style block to an external file. These are accepted future work items,
not blockers for the CSP.

## Verification

A Playwright E2E suite at `frontend/e2e/csp.spec.ts` (SEC-CSP-1) verifies:

| Test | Acceptance Criterion |
|------|---------------------|
| CSP header present | AC-CSP-1 |
| `script-src 'self'` without `unsafe-inline` | AC-CSP-2 |
| `style-src 'self' 'unsafe-inline'` present | AC-CSP-3 |
| `frame-ancestors 'none'` present | AC-CSP-4 |
| Zero violations — light theme, all surfaces | AC-CSP-5 |
| Zero violations — dark theme, all surfaces | AC-CSP-6 |
| Zero violations during KaTeX math rendering | AC-CSP-7 |
| Zero violations during sigma.js WebGL render | AC-CSP-8 |

The suite runs in CI via the existing Playwright E2E job (`e2e/` glob). Both light and dark
themes are exercised per the SEC-CSP-1 workstream requirement.

## Consequences

- `script-src 'self'`: inline JS injection XSS is blocked. The production Vite build
  satisfies this without any changes.
- `style-src 'self' 'unsafe-inline'`: CSS injection attacks are partially mitigated (external
  stylesheets blocked; inline styles allowed as a documented tradeoff).
- `frame-ancestors 'none'`: the SPA cannot be embedded in an iframe on any origin.
- `object-src 'none'`: browser plugin execution is blocked.
- `base-uri 'self'`: `<base>` tag injection is blocked.

## Future work

1. **Eliminate `style-src 'unsafe-inline'`** by: (a) moving `index.html` `<style>` block to
   an external CSS file (`src/styles/shell-baseline.css`), (b) auditing and migrating the
   highest-value inline-style usages to CSS classes, (c) switching KaTeX to MathML-only
   output (`output: "mathml"`) once browser MathML support is sufficiently universal.
   When all three are done, `style-src 'self'` becomes viable and this ADR should be updated.

2. **Nonce-based script-src**: for environments where additional inline scripts are needed
   (e.g. analytics snippets), generate a per-request nonce and inject it into the HTML
   template. This is out of scope for the self-hosted single-page model.
