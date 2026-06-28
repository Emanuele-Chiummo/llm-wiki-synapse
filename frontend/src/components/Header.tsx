/**
 * Header.tsx — top bar: branding + provider-selector slot.
 *
 * Phase 1 (v0.4): Provider Selector slot is a styled placeholder.
 * Phase 2 will replace the placeholder with the real F17 dropdown.
 */

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
          v0.4
        </span>
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Provider selector slot — F17 will replace this placeholder */}
      <div
        className="app-header__provider-slot"
        role="group"
        aria-label="Provider selector (coming in Phase 2)"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "4px 10px",
          border: "1px solid #21262d",
          borderRadius: 6,
          color: "#484f58",
          fontSize: 12,
          cursor: "not-allowed",
          opacity: 0.6,
        }}
        title="Provider Selector — coming in v0.4 Phase 2 (F17)"
      >
        <span aria-hidden="true" style={{ fontSize: 10 }}>&#11835;</span>
        <span>Provider</span>
        <span aria-hidden="true" style={{ fontSize: 10, opacity: 0.5 }}>&#9660;</span>
      </div>
    </header>
  );
}
