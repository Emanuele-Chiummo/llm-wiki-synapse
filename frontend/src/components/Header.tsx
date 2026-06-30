/**
 * Header.tsx — top bar: branding + provider-selector (F17).
 *
 * Phase 2 (v0.5): Provider Selector slot wired to real <ProviderSelector/>.
 */

import { ProviderSelector } from "./provider/ProviderSelector";

export function Header() {
  return (
    <header
      className="app-header"
      data-testid="app-header"
      style={{
        display: "flex",
        alignItems: "center",
        height: 48,
        padding: "0 16px",
        background: "#161b22",
        borderBottom: "1px solid #21262d",
        flexShrink: 0,
        gap: 16,
      }}
    >
      {/* Branding */}
      <div
        className="app-header__brand"
        style={{ display: "flex", alignItems: "center", gap: 8 }}
      >
        <span
          aria-hidden="true"
          style={{ fontSize: 18 }}
        >
          &#9889;
        </span>
        <span
          style={{
            fontSize: 15,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            color: "#e6edf3",
          }}
        >
          Synapse
        </span>
        <span
          style={{
            fontSize: 11,
            color: "#484f58",
            fontWeight: 400,
          }}
        >
          v0.5
        </span>
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Provider Selector (F17) */}
      <div className="app-header__provider-slot">
        <ProviderSelector />
      </div>
    </header>
  );
}
