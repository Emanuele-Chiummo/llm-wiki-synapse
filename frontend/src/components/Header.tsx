/**
 * Header.tsx — top bar: branding + provider-selector (F17).
 *
 * Light design: var(--syn-bg) background, var(--syn-border) divider, var(--syn-text) labels.
 * Phase 2 (v0.5): Provider Selector slot wired to real <ProviderSelector/>.
 * v0.6 (ADR-0047 §2.6 + §2.3):
 *   - Logo replaces the ⚡ lightning emoji.
 *   - Tauri-only: server chip shows the connected host + "change server" action.
 * v0.6 (ADR-0048 §T4a + §T4b):
 *   - Server chip becomes a dropdown listing known servers (Tauri-only).
 *   - Switching to a known server calls setServerUrl then window.location.reload() —
 *     full reload is the simplest correct state reset: it guarantees no stale
 *     cross-server data (cached queries, graph, conversations, dataVersion) leaks into
 *     the new session. Selective invalidation is rejected as clever-and-fragile (ADR-0048 §2.4).
 *   - Zoom hook (useDesktopZoom) registered here — Header is always mounted.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { ChevronUp, ChevronDown, Search } from "lucide-react";
import { ProviderSelector } from "./provider/ProviderSelector";
import { SynapseMark } from "./brand/SynapseMark";
import { isTauri, getKnownServers } from "../api/base";
import {
  useSettingsStore,
  selectServerUrl,
  selectClearServerUrl,
  selectSetServerUrl,
} from "../store/settingsStore";
import { useGraphStore, selectActiveSection } from "../store/graphStore";
import { useDesktopZoom } from "../hooks/useDesktopZoom";
import { PRODUCT_IDENTITY } from "../config/productIdentity";

export function Header() {
  const { t } = useTranslation();
  const serverUrl = useSettingsStore(selectServerUrl);
  const clearServerUrl = useSettingsStore(selectClearServerUrl);
  const setServerUrl = useSettingsStore(selectSetServerUrl);
  const activeSection = useGraphStore(selectActiveSection);
  const inTauri = isTauri();

  // Register Cmd/Ctrl +/-/0 zoom shortcuts (Tauri-only, no-op in browser)
  useDesktopZoom();

  /** Extract just the host (hostname:port) from the saved URL for the chip label. */
  let serverHost: string | null = null;
  if (inTauri && serverUrl !== null) {
    try {
      serverHost = new URL(serverUrl).host;
    } catch {
      serverHost = serverUrl;
    }
  }

  // ── Server dropdown state (Tauri-only, ADR-0048 §T4a) ────────────────────
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on Escape or outside click
  useEffect(() => {
    if (!dropdownOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDropdownOpen(false);
    };
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleClick);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleClick);
    };
  }, [dropdownOpen]);

  const knownServers = inTauri ? getKnownServers() : [];

  const handleSwitchServer = useCallback(
    (url: string) => {
      setDropdownOpen(false);
      // Only switch if it is not already the active server
      if (url === serverUrl) return;
      // setServerUrl validates + persists, then full reload resets all state.
      setServerUrl(url);
      window.location.reload();
    },
    [serverUrl, setServerUrl],
  );

  const handleChangeServer = useCallback(() => {
    setDropdownOpen(false);
    clearServerUrl();
  }, [clearServerUrl]);

  return (
    <header
      className="app-header"
      data-testid="app-header"
      style={{
        display: "flex",
        alignItems: "center",
        height: 48,
        padding: "0 16px",
        background: "var(--syn-bg)",
        borderBottom: "1px solid var(--syn-border)",
        flexShrink: 0,
        gap: 16,
      }}
    >
      {/* Branding */}
      <div className="app-header__brand" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <SynapseMark size={22} style={{ display: "block", flexShrink: 0 }} />
        <span
          data-testid="app-wordmark"
          style={{
            fontSize: 15,
            fontFamily: "var(--syn-font-wordmark)",
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: "var(--syn-text)",
          }}
        >
          {PRODUCT_IDENTITY.displayName}
        </span>
        <span
          style={{
            fontSize: 11,
            color: "var(--syn-text-dim)",
            fontWeight: 400,
          }}
        >
          v{__APP_VERSION__}
        </span>
      </div>

      {/* Section breadcrumb — shows the active section name as topbar context [F2 v1.7.0] */}
      <div
        aria-hidden="true"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          color: "var(--syn-text-dim)",
          fontFamily: "var(--syn-font-mono)",
          fontSize: 11,
          userSelect: "none",
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ opacity: 0.5 }}>/</span>
        <span style={{ color: "var(--syn-text-muted)", fontWeight: 500 }}>
          {t(
            `nav.${activeSection === "pages" ? "wiki" : activeSection === "deep-search" ? "deepSearch" : activeSection}`,
          )}
        </span>
      </div>

      {/* Tauri-only: connected server chip with dropdown (ADR-0047 §2.3 + ADR-0048 §T4a) */}
      {inTauri && serverHost !== null && (
        <div ref={dropdownRef} data-testid="server-chip" style={{ position: "relative" }}>
          {/* Chip button — toggles dropdown */}
          <button
            type="button"
            aria-haspopup="true"
            aria-expanded={dropdownOpen}
            aria-label={t("desktop.serverChip.label", { host: serverHost })}
            data-testid="server-chip-btn"
            onClick={() => setDropdownOpen((o) => !o)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px 2px 6px",
              background: "var(--syn-surface, #f1f5f9)",
              border: "1px solid var(--syn-border)",
              borderRadius: 20,
              fontSize: 11,
              color: "var(--syn-text-dim)",
              userSelect: "none",
              cursor: "pointer",
            }}
          >
            {/* green dot */}
            <span
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--syn-green)",
                flexShrink: 0,
              }}
            />
            <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
              {serverHost}
            </span>
            {/* caret — UXA-25: Lucide icon instead of Unicode triangle */}
            {dropdownOpen ? (
              <ChevronUp size={9} aria-hidden="true" style={{ marginLeft: 2, flexShrink: 0 }} />
            ) : (
              <ChevronDown size={9} aria-hidden="true" style={{ marginLeft: 2, flexShrink: 0 }} />
            )}
          </button>

          {/* Dropdown menu */}
          {dropdownOpen && (
            <div
              role="menu"
              aria-label={t("desktop.serverChip.menuLabel")}
              data-testid="server-chip-menu"
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                left: 0,
                minWidth: 200,
                background: "var(--syn-bg)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
                zIndex: 200,
                overflow: "hidden",
              }}
            >
              {/* Known-server list */}
              {knownServers.map((url) => {
                let host = url;
                try {
                  host = new URL(url).host;
                } catch {
                  /* keep url */
                }
                const isCurrent = url === serverUrl;
                return (
                  <button
                    key={url}
                    type="button"
                    role="menuitem"
                    data-testid={`server-item-${host}`}
                    onClick={() => handleSwitchServer(url)}
                    className="syn-btn syn-btn--ghost"
                    style={{
                      width: "100%",
                      justifyContent: "flex-start",
                      fontSize: 12,
                      padding: "7px 12px",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                      fontWeight: isCurrent ? 600 : 400,
                      color: "var(--syn-text)",
                      cursor: isCurrent ? "default" : "pointer",
                    }}
                  >
                    {isCurrent && (
                      <span
                        aria-hidden="true"
                        style={{
                          display: "inline-block",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: "var(--syn-green)",
                          flexShrink: 0,
                        }}
                      />
                    )}
                    {!isCurrent && (
                      <span
                        aria-hidden="true"
                        style={{ display: "inline-block", width: 6, flexShrink: 0 }}
                      />
                    )}
                    <span>{host}</span>
                    {isCurrent && (
                      <span
                        style={{ marginLeft: "auto", fontSize: 10, color: "var(--syn-text-dim)" }}
                      >
                        {t("desktop.serverChip.current")}
                      </span>
                    )}
                  </button>
                );
              })}

              {/* Divider */}
              {knownServers.length > 0 && (
                <div style={{ height: 1, background: "var(--syn-border)", margin: "2px 0" }} />
              )}

              {/* Change server (returns to Connect gate) */}
              <button
                type="button"
                role="menuitem"
                data-testid="change-server-btn"
                onClick={handleChangeServer}
                className="syn-btn syn-btn--ghost"
                style={{
                  width: "100%",
                  justifyContent: "flex-start",
                  fontSize: 12,
                  padding: "7px 12px",
                }}
              >
                {t("connect.changeServer")}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* UXA-26 / a11y-cmdk-pill: ⌘K search affordance — real <button> so touch/mobile
          users can reach it. Dispatches synapse:openPalette; AppShell owns the state.
          CSS class .app-header__cmdk collapses to icon-only on mobile (≤767px). */}
      <button
        type="button"
        className="app-header__cmdk"
        aria-label={t("palette.openLabel")}
        onClick={() => window.dispatchEvent(new Event("synapse:openPalette"))}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          height: 28,
          padding: "0 10px",
          minWidth: 180,
          border: "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-sm)",
          background: "var(--syn-bg-soft)",
          color: "var(--syn-text-dim)",
          fontSize: 12,
          userSelect: "none",
          whiteSpace: "nowrap",
          cursor: "pointer",
        }}
      >
        <Search size={13} aria-hidden="true" className="app-header__cmdk-icon" />
        <span className="app-header__cmdk-text" style={{ flex: 1, opacity: 0.7 }}>
          {t("palette.searchHint")}
        </span>
        <kbd
          className="app-header__cmdk-kbd"
          style={{
            marginLeft: "auto",
            fontFamily: "var(--syn-font-mono)",
            fontSize: 10,
            color: "var(--syn-text-dim)",
            background: "var(--syn-surface-hover)",
            border: "1px solid var(--syn-border)",
            borderRadius: 4,
            padding: "1px 5px",
          }}
        >
          {t("palette.trigger")}
        </kbd>
      </button>

      {/* Provider Selector (F17) */}
      <div className="app-header__provider-slot">
        <ProviderSelector />
      </div>
    </header>
  );
}
