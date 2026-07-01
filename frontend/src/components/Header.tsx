/**
 * Header.tsx — top bar: branding + provider-selector (F17).
 *
 * Light design: var(--syn-bg) background, var(--syn-border) divider, var(--syn-text) labels.
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
