/**
 * AppShell.tsx — top-level layout (ADR-0018 §7 / Phase 2 rewire).
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  Header (48px) — branding + ProviderSelector (F17)          │
 *   ├──────┬───────────────────────────────────────────────────────┤
 *   │ NavRail│  SectionRouter                                      │
 *   │ 72px  │  pages → PanelGroup (NavTree│Center│PreviewPanel)    │
 *   │       │  graph → GraphPanel full-bleed                       │
 *   │       │  ingest → IngestView + IngestRunDetail               │
 *   │       │  settings → SettingsPanel                            │
 *   ├──────┴───────────────────────────────────────────────────────┤
 *   │  ActivityBar (28px)                                          │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * INVARIANT I2: NavRail never imports graph layout code.
 * INVARIANT I3: NavRail reads only activeSection + runningCount (separate stores).
 * ToastHost renders here once — showToast() calls from anywhere are captured.
 *
 * ADR-0047 §2.3 (C3): if isTauri() && no serverUrl → render ConnectScreen instead.
 * Web/PWA: isTauri() is false, so this gate is never visible in the browser.
 */

import { Header } from "./Header";
import { NavRail } from "./nav/NavRail";
import { SectionRouter } from "./SectionRouter";
import { ActivityBar } from "./activity/ActivityBar";
import { ToastHost } from "./common/Toast";
import { ConnectScreen } from "./connect/ConnectScreen";
import { isTauri } from "../api/base";
import { useSettingsStore, selectServerUrl } from "../store/settingsStore";

export function AppShell() {
  // ADR-0047 §2.3: gate is active only in Tauri and only when no server URL is set.
  // selectServerUrl reads the Zustand field (initialized from localStorage); when
  // ConnectScreen calls storeSetServerUrl, the field updates and this re-renders.
  const serverUrl = useSettingsStore(selectServerUrl);
  const inTauri = isTauri();

  if (inTauri && serverUrl === null) {
    return <ConnectScreen />;
  }

  return (
    <div
      className="app-shell"
      data-testid="app-shell"
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100vw",
        height: "100vh",
        overflow: "hidden",
        background: "var(--syn-bg)",
        color: "var(--syn-text)",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
      }}
    >
      {/* ── Row 1: Header ──────────────────────────────────────────────────── */}
      <Header />

      {/* ── Row 2: NavRail + SectionRouter ─────────────────────────────────── */}
      {/* minHeight:0 ensures height:100% inside children resolves in flex-column. */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "row",
          overflow: "hidden",
        }}
      >
        <NavRail />
        {/* SectionRouter fills remaining horizontal space */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <SectionRouter />
        </div>
      </div>

      {/* ── Row 3: ActivityBar ─────────────────────────────────────────────── */}
      <ActivityBar />

      {/* ── Toast notifications (singleton, outside all panels) ────────────── */}
      <ToastHost />
    </div>
  );
}
