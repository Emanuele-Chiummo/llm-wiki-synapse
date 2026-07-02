/**
 * Header.tsx — top bar: branding + provider-selector (F17).
 *
 * Light design: var(--syn-bg) background, var(--syn-border) divider, var(--syn-text) labels.
 * Phase 2 (v0.5): Provider Selector slot wired to real <ProviderSelector/>.
 * v0.6 (ADR-0047 §2.6 + §2.3):
 *   - Logo replaces the ⚡ lightning emoji.
 *   - Tauri-only: server chip shows the connected host + "change server" action.
 */

import { useTranslation } from "react-i18next";
import { ProviderSelector } from "./provider/ProviderSelector";
import logoUrl from "../assets/synapse-logo.svg";
import { isTauri } from "../api/base";
import { useSettingsStore, selectServerUrl, selectClearServerUrl } from "../store/settingsStore";

export function Header() {
  const { t } = useTranslation();
  const serverUrl = useSettingsStore(selectServerUrl);
  const clearServerUrl = useSettingsStore(selectClearServerUrl);
  const inTauri = isTauri();

  /** Extract just the host (hostname:port) from the saved URL for the chip label. */
  let serverHost: string | null = null;
  if (inTauri && serverUrl !== null) {
    try {
      serverHost = new URL(serverUrl).host;
    } catch {
      serverHost = serverUrl;
    }
  }

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
      <div
        className="app-header__brand"
        style={{ display: "flex", alignItems: "center", gap: 8 }}
      >
        <img
          src={logoUrl}
          alt=""
          aria-hidden="true"
          width={22}
          height={22}
          style={{ display: "block", flexShrink: 0 }}
        />
        <span
          style={{
            fontSize: 15,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            color: "var(--syn-text)",
          }}
        >
          Synapse
        </span>
        <span
          style={{
            fontSize: 11,
            color: "var(--syn-text-dim)",
            fontWeight: 400,
          }}
        >
          v0.6
        </span>
      </div>

      {/* Tauri-only: connected server chip (ADR-0047 §2.3) */}
      {inTauri && serverHost !== null && (
        <div
          data-testid="server-chip"
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
              background: "#22c55e",
              flexShrink: 0,
            }}
          />
          <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
            {serverHost}
          </span>
          <button
            type="button"
            onClick={clearServerUrl}
            data-testid="change-server-btn"
            title={t("connect.changeServer")}
            style={{
              marginLeft: 4,
              padding: "0 4px",
              fontSize: 11,
              color: "var(--syn-text-dim)",
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              textUnderlineOffset: 2,
            }}
          >
            {t("connect.changeServer")}
          </button>
        </div>
      )}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Provider Selector (F17) */}
      <div className="app-header__provider-slot">
        <ProviderSelector />
      </div>
    </header>
  );
}
