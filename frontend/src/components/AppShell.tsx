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
 *
 * ADR-0048 §T2: CommandPalette + global shortcuts mounted here (single listener).
 */

import { useState, useCallback, useEffect } from "react";
import { Header } from "./Header";
import { NavRail } from "./nav/NavRail";
import { SectionRouter } from "./SectionRouter";
import { ActivityBar } from "./activity/ActivityBar";
import { ToastHost } from "./common/Toast";
import { ConnectScreen } from "./connect/ConnectScreen";
import { TokenGate } from "./connect/TokenGate";
import { CommandPalette } from "./common/CommandPalette";
import { UpdateBanner } from "./common/UpdateBanner";
import { isTauri, register401Handler } from "../api/base";
import {
  useSettingsStore,
  selectServerUrl,
  selectAuthRequired,
  selectSetAuthRequired,
} from "../store/settingsStore";
import { useGlobalShortcuts } from "../hooks/useGlobalShortcuts";
import { useDesktopUpdater } from "../hooks/useDesktopUpdater";

export function AppShell() {
  // ADR-0047 §2.3: gate is active only in Tauri and only when no server URL is set.
  // selectServerUrl reads the Zustand field (initialized from localStorage); when
  // ConnectScreen calls storeSetServerUrl, the field updates and this re-renders.
  const serverUrl = useSettingsStore(selectServerUrl);
  const inTauri = isTauri();

  // ADR-0052: web-only 401 gate.
  const authRequired = useSettingsStore(selectAuthRequired);
  const setAuthRequired = useSettingsStore(selectSetAuthRequired);

  // Register the 401 callback once on mount (ADR-0052 §3.4).
  // apiFetch fires this when any protected endpoint returns 401; the callback
  // sets authRequired = true which causes TokenGate to overlay the shell.
  // Registered once — safe to call multiple times (last registration wins per ADR-0052).
  useEffect(() => {
    register401Handler(() => {
      setAuthRequired(true);
    });
  }, [setAuthRequired]);

  // ADR-0048 §T2: command palette open state.
  const [paletteOpen, setPaletteOpen] = useState(false);
  const handleTogglePalette = useCallback(() => setPaletteOpen((v) => !v), []);
  const handleClosePalette = useCallback(() => setPaletteOpen(false), []);

  // Wire global keyboard shortcuts (single listener per shell mount).
  useGlobalShortcuts({
    paletteOpen,
    onTogglePalette: handleTogglePalette,
  });

  // ADR-0049 §U4: startup update check — once, non-blocking, Tauri-only.
  const updaterState = useDesktopUpdater();

  if (inTauri && serverUrl === null) {
    return <ConnectScreen />;
  }

  // ADR-0052: web-only overlay — shown when any apiFetch call gets a 401.
  // TokenGate clears authRequired on successful token submission.
  if (!inTauri && authRequired) {
    return <TokenGate onSuccess={() => setAuthRequired(false)} />;
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

      {/* ── Row 1b: Update banner (ADR-0049 §U4 — Tauri-only, slim, dismissible) ── */}
      <UpdateBanner state={updaterState} />

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

      {/* ── Command palette (ADR-0048 §T2) ─────────────────────────────────── */}
      <CommandPalette open={paletteOpen} onClose={handleClosePalette} />
    </div>
  );
}
