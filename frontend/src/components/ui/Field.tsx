/**
 * Field.tsx — shared label + control + error primitive (W4 audit FE-QUAL-8).
 *
 * Replaces the settings/ui.tsx INPUT_STYLE inline-style constant: the control
 * rendered inside <Field> should use the shared `.syn-input` CSS class
 * (styles/components.css) instead of a spread inline-style object.
 *
 * Two usage shapes are supported:
 *   1. <Field label="…">  <input className="syn-input" .../>  </Field>
 *      — full control over the rendered control (select/textarea/custom).
 *   2. <Field label="…" htmlFor="…" error="…">…</Field>
 *      — label wired to a control via htmlFor/id, with an optional error line.
 */
import { type ReactNode } from "react";

export interface FieldProps {
  label: string;
  htmlFor?: string;
  error?: string;
  compact?: boolean;
  children: ReactNode;
}

export function Field({ label, htmlFor, error, compact, children }: FieldProps) {
  return (
    <div style={{ marginBottom: compact ? 10 : 20 }}>
      <label
        htmlFor={htmlFor}
        style={{
          display: "block",
          marginBottom: 6,
          fontSize: "var(--syn-font-sm)",
          fontWeight: 600,
          color: "var(--syn-text-muted)",
        }}
      >
        {label}
      </label>
      {children}
      {error && (
        <p
          role="alert"
          style={{
            margin: "6px 0 0",
            fontSize: "var(--syn-font-xs)",
            color: "var(--syn-red)",
          }}
        >
          {error}
        </p>
      )}
    </div>
  );
}
