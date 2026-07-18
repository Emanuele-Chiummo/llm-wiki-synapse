# ADR-0088 — iOS app redesign foundation: native SwiftUI, brand-aligned design system, graph-render spike (Track iOS 2.1, Fase A)

- **Status:** Accepted (foundation) — graph-render choice is **Proposed, pending on-device verification** (see §Graph rendering)
- **Date:** 2026-07-18
- **Invariants touched:** I2 (graph layout stays server-side / FA2-precomputed — the redesign consumes `GET /graph` coords, it does **not** run a client force layout), I8 (this ADR + branch is the D7 artefact for Fase A)
- **Feature refs:** F1 (app shell / navigation), F4 (graph viewer), F15 (cross-platform — native iOS client), F16 (theming, light/dark parity)
- **Workstream:** 2.1 — Track iOS 2.0, **Fase A (design foundation)**

## Context

The owner's directive (explicit, not inferred): the iOS app needs a **complete redesign** —
graphics, performance, usability — and must end up **at least comparable to the desktop app**.
This is *not* an API refresh of the existing client.

State of play at the start of Fase A:

- A native SwiftUI app already lives in `ios/` (`Synapse.xcodeproj`, XcodeGen-generated from
  `project.yml`, bundle id `ai.synapse.mobile`). It is functional but built against a pre-2.0
  API and — critically — its design tokens (`ios/Synapse/Theme/Theme.swift`) were ported from
  an **old** mobile design handoff (`Synapse.dc.html`). Those tokens use Apple-system indigo
  (`#4F46E5`) as the accent and **literal pure black** (`#000000`) for primary label and dark
  background. Both violate the current Synapse brand: the desktop v1.7.0 / 1.9.3 "UI kit" uses
  brand blue `#2563eb` as the accent and **never** pure black (light ink `#0f1729`, dark ground
  deep-navy `#0b1120`). The iOS visual language had drifted from the desktop's.
- Three stale branches were inspected before starting:
  - `feature/ios-target` — an **abandoned Tauri v2 web-wrapper** iOS attempt. Parked. Its
    `ios/` tree is a net *deletion* vs `main` (main is far ahead). **Not resurrected** — see
    the platform decision below for why a web wrapper is the wrong tool for this redesign.
  - `feat/ios-native-app` — the *original* native-app commit. Its `ios/` diff vs `main` is a
    net deletion: `main` already contains it plus every subsequent refinement. Superseded.
  - `feature/ios-neural-refresh` — an early "neural" visual pass (the `NeuralMotif` /
    `AuroraBackground` motifs). Also a net deletion vs `main`: those motifs already live in
    `main`'s `ios/Synapse/Shared/Components.swift`. Superseded.
  - **Conclusion: nothing to salvage.** `main` is the most advanced iOS state; all three
    branches are behind it. Fase A builds forward from `main`, it does not merge any of them.

Fase A is the **design foundation**: it must land (1) this ADR, (2) a brand-aligned SwiftUI
design system as real views, (3) a real, demoable navigation skeleton, and (4) mock Home + Wiki
screens in light and dark — so the owner can approve the visual direction **before** Fase B
wires real API-backed surfaces onto the new shell.

## Decision 1 — Native SwiftUI is confirmed as the platform (not Tauri / web-wrapper)

The redesign target — "at least comparable to the desktop app" *on mobile* — is a UX target,
not a "render the same pixels" target. Meeting it requires **mobile-native interaction
patterns** that a web wrapper cannot deliver convincingly:

- **Native navigation**: a real `TabView` + `NavigationStack` with the system push/pop
  transitions, large-title collapse, swipe-back edge gesture, and toolbar semantics. A web
  wrapper reimplements these in JS and always feels a step behind.
- **Gestures & haptics**: swipe actions on list rows, pull-to-refresh, context menus,
  `sensoryFeedback`. These are first-class in SwiftUI and awkward-to-impossible to make feel
  right in a WKWebView shell.
- **Performance & memory**: a native list (`List` / `LazyVStack`) with cell reuse beats a
  DOM/virtualised web list inside a webview for scroll smoothness and memory on a phone.
- **System integration**: Dynamic Type, SF Symbols, safe-area handling, Reduce Motion,
  light/dark following the system — all free and correct natively.
- **Head start**: the existing native app is a *real* asset (networking layer, Codable models,
  feature screens). A web wrapper would throw that away to re-embed the desktop SPA, which is
  explicitly the layout we do **not** want to copy literally on a phone.

The desktop's own responsive CSS (ADR-0057, the `≤767px` tier) already proves the web UI *can*
run on a phone — but that is the *fallback*, a shrunk desktop. The whole point of this track is
to go past that to a genuinely native experience. **Tauri/web-wrapper is rejected for the
Synapse iOS app.** `feature/ios-target` stays parked.

## Decision 2 — Brand-aligned SwiftUI design system (supersedes the legacy `Theme.swift`)

A new `ios/Synapse/DesignSystem/` package is the source of truth for the redesign's visual
language, ported **faithfully from the desktop `frontend/src/styles/theme.css`** (the 1.9.3 UI
kit), translated to native iOS idioms:

- **Color tokens** (`SynColor`) — light/dark dynamic colours resolved against the system
  `colorScheme`, matching the desktop token-for-token:
  - accent `#2563eb` (light) / `#58a6ff` (dark); accent-strong, accent-soft.
  - ink `#0f1729` (light) / `#e7ecf7` (dark) — **never `#000000`**; ground `#ffffff` (light) /
    `#0b1120` deep-navy (dark) — **never `#000000`**.
  - the per-type jewel-tone palette (concept violet, entity blue, source teal, synthesis
    indigo, comparison copper, query amber), lightened for the navy ground in dark, matching
    the desktop `--syn-type-*` values.
  - the brand gradient `#1d4ed8 → #4338ca → #7c3aed`, used with restraint (wordmark, hero,
    primary CTA).
- **Type scale** (`SynFont`) — built on the native text styles so **Dynamic Type** works,
  with the wordmark tightened (`-0.02em` tracking) to echo the desktop Geist wordmark.
- **Spacing / radius tokens** (`SynSpace`, `SynRadius`) — the desktop 4/6/8/10/12/16 spacing
  ramp and 7/9/12/pill radii.
- **Core components as real views**: `SynButton` (primary/secondary/ghost/destructive),
  `SynCard`, `SynChip`, `SynListRow`, `SynEmptyState`, `SynSkeleton` (shimmer honouring Reduce
  Motion). Icons use **SF Symbols** chosen per meaning rather than transliterating the desktop
  lucide set.

The legacy `Theme.swift` / `Components.swift` are **not deleted** (the existing feature screens
still compile against them, so nothing is lost for Fase B to port), but they are marked legacy:
the redesign shell renders **only** `SynColor`, so no pure-black token is ever painted in the
new experience. Migrating the remaining real screens off `Theme` onto `SynColor` is Fase B work.

## Decision 3 — Navigation: native 5-tab shell (Home · Wiki · Chat · Graph · More)

The desktop's 3-panel shell (tree / chat / preview) is **not** copied literally — that layout
is wrong for a phone. Instead a native `TabView` exposes the five primary destinations, each an
independent `NavigationStack`. This is the demoable skeleton: Home and Wiki carry realistic mock
content; Chat, Graph and More are honest placeholders built from the design system. `Home` is
new relative to the old shell (which led with Wiki) and becomes the landing surface (F18 home
dashboard lineage). The app entry (`SynapseApp`) now presents the redesign root; the old
`RootTabView` and its screens remain compiled (available for Fase B to port) but are not the
runtime entry.

## Graph rendering — documented spike (I2-preserving either way)

The knowledge graph (F4) is the one surface where "native vs embed" is a genuine engineering
fork. Both candidates **consume the server-side, FA2-precomputed coordinates from `GET /graph`**
— neither runs a force layout on the device, so **I2 holds in both cases**. The question is only
*who draws the precomputed points*.

| | (a) WKWebView embed of the sigma.js viewer | (b) Native renderer (Canvas / SpriteKit / Metal) |
|---|---|---|
| **I2 compliance** | ✅ coords server-side, sigma just renders | ✅ coords server-side, native code just renders |
| **Engineering cost** | ~none — reuse the shipped web viewer | High — build + maintain a *second* renderer |
| **Native feel** | Poor — a webview island inside a native app; gestures/haptics/theming don't match the rest | Excellent — real pinch/pan, momentum, haptics, SF-styled controls, exact theme match |
| **Maintenance** | Follows the web viewer for free | Diverges; every graph feature done twice |
| **Perf on device** | Sigma is WebGL, usually fine; webview bridge + memory overhead unknown on older phones | Potentially best (SpriteKit/Metal), but unproven until built and measured |
| **Consistency w/ redesign goal** | Undercuts "at least comparable to desktop" — it *is* the desktop, embedded | Fully delivers the redesign intent |

**Recommendation: (b) a native renderer — but staged.** For Fase A the Graph tab is a
placeholder. The recommendation is to build the native renderer in Fase B starting with the
lightest technology that meets the bar — **SwiftUI `Canvas` first** (draw the precomputed nodes
/ edges, add `MagnificationGesture` + `DragGesture` for pinch/pan), escalating to **SpriteKit**
only if `Canvas` can't hold frame rate on a large vault, and to **Metal** only if SpriteKit
can't. This keeps the second renderer as small as the performance target allows.

**Pragmatic hedge:** ship **(a) the WKWebView embed as a fallback / behind a flag** in early
Fase B so the Graph tab is functional while (b) is built and measured. If on-device numbers for
(b) disappoint, (a) is the safety net — I2 is satisfied either way, so this hedge costs nothing
architecturally.

### What I could NOT verify — needs the owner's sign-off before Fase B locks the choice

A **physical-device performance check is a prerequisite** before committing to (b), and I
**cannot** perform it from this environment (no physical iOS device; only the Simulator is
reachable). The following must be measured on a real device against a real vault and signed off
by the owner:

- **fps** panning/zooming a realistic graph (hundreds–thousands of nodes) — target a smooth
  ~60 fps (120 on ProMotion) with no hitching.
- **memory footprint** of the native renderer vs the webview embed under the same graph.
- **gesture responsiveness** — pinch/pan latency, momentum feel, haptic timing.

Until those numbers exist and the owner approves, the graph-render decision is **Proposed**, not
Accepted. The rest of this ADR (native SwiftUI, design system, navigation) is Accepted.

## Consequences

- The redesign has a brand-correct foundation: the new shell can never paint pure black and its
  accent is the real brand blue, matching the desktop token-for-token.
- The existing native app is preserved (compiles, not deleted) so Fase B ports real data into
  the new shell screen-by-screen with the old screens as reference — no big-bang rewrite.
- Two visual languages coexist transiently (legacy `Theme` vs new `SynColor`). This is an
  accepted, time-boxed state; Fase B's exit criterion includes deleting `Theme.swift` once the
  last real screen is migrated.
- The graph renderer is deliberately left open with an I2-safe fallback, so Fase A does not
  gamble on an unverified performance assumption.

## Verification (Fase A)

- **Build:** `xcodegen generate` (project regenerated to include the new files) + a Simulator
  build (`xcodebuild … -destination 'platform=iOS Simulator,name=iPhone 17'`). What was actually
  run in this environment, and what still needs a human on a real Mac/device, is recorded in the
  PR description under "Verified locally vs needs owner". The honest posture per the track's
  "chiedere, non aggirare" mantra: Simulator evidence is not device evidence.

## Future work (Fase B and beyond)

1. Build the native graph renderer (Canvas → SpriteKit → Metal escalation), keep the WKWebView
   embed behind a flag as the fallback, run the on-device perf check, get owner sign-off.
2. Migrate the real feature screens (Wiki list/detail, Search, Chat, Review, Ingest, Research,
   Settings) onto `SynColor` + the new components; then delete `Theme.swift` / legacy
   `Components.swift`.
3. Wire the redesigned Home + Wiki to live API data (F18 home dashboard, F1/F4).
