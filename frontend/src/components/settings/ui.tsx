/**
 * ui.tsx — shared sub-components for the Settings surface.
 *
 * Exports: SectionHeader, Field, BudgetRow, GroupDivider, and icon helpers.
 *
 * BTN_PRIMARY/BTN_SECONDARY/INPUT_STYLE inline-style constants were removed
 * (W4 audit FE-QUAL-8, eliminates a parallel styling system). Consumers now
 * use components/ui/Button (variant="accent-ghost" | "ghost") and the shared
 * .syn-input CSS class (styles/components.css) directly.
 *
 * I3: no Zustand subscriptions here — pure presentational.
 */

import { type ReactNode } from "react";

// ─── Shared sub-components ────────────────────────────────────────────────────

export function SectionHeader({ title, desc }: { title: string; desc: string }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ margin: "0 0 6px", fontSize: 16, fontWeight: 700, color: "var(--syn-text)" }}>
        {title}
      </h2>
      <p style={{ margin: 0, fontSize: 12, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
        {desc}
      </p>
    </div>
  );
}

export function Field({
  label,
  children,
  compact,
}: {
  label: string;
  children: ReactNode;
  compact?: boolean;
}) {
  return (
    <div style={{ marginBottom: compact ? 10 : 20 }}>
      <label
        style={{
          display: "block",
          marginBottom: 6,
          fontSize: 12,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

export function BudgetRow({ label, pct, tokens }: { label: string; pct: number; tokens: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "120px 32px 1fr 60px",
        gap: 8,
        alignItems: "center",
        marginBottom: 4,
      }}
    >
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)" }}>{label}</span>
      <span
        style={{ fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "var(--syn-font-mono)" }}
      >
        {pct}%
      </span>
      <div
        style={{ height: 4, background: "var(--syn-border)", borderRadius: 2, overflow: "hidden" }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: "var(--syn-accent)",
            borderRadius: 2,
          }}
        />
      </div>
      <span
        style={{
          fontSize: 11,
          color: "var(--syn-text-muted)",
          fontFamily: "var(--syn-font-mono)",
          textAlign: "right",
        }}
      >
        {tokens >= 1048576
          ? `${tokens / 1048576}M`
          : tokens >= 1024
            ? `${Math.round(tokens / 1024)}K`
            : `${tokens}`}
      </span>
    </div>
  );
}

export function GroupDivider() {
  return <div style={{ borderTop: "1px solid var(--syn-border)", margin: "32px 0" }} />;
}

/** EmbedRow: read-only label+value pair used by Embeddings and ApiMcp sections. */
export function EmbedRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)" }}>{label}</span>
      <span
        style={{
          fontSize: 12,
          color: "var(--syn-text)",
          fontFamily: mono ? "var(--syn-font-mono)" : undefined,
          padding: "5px 8px",
          background: "var(--syn-surface-sunken)",
          borderRadius: 4,
          border: "1px solid var(--syn-border)",
          wordBreak: "break-all",
        }}
      >
        {value}
      </span>
    </div>
  );
}

