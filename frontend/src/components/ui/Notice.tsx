/**
 * Notice.tsx — shared info/warning/error banner primitive (W4 audit FE-QUAL-8).
 *
 * Thin re-export of the existing SectionNotice component (components/common/
 * EmptyState.tsx) so callers have a components/ui entry point without a
 * second parallel implementation. `tone="error"` maps to SectionNotice's
 * "danger" tone (same CSS: .syn-section-notice--danger).
 */
import type { ReactNode } from "react";
import { SectionNotice } from "../common/EmptyState";

export interface NoticeProps {
  children: ReactNode;
  tone?: "info" | "success" | "warning" | "error";
  role?: "status" | "alert";
}

export function Notice({ children, tone = "info", role }: NoticeProps) {
  const sectionTone = tone === "error" ? "danger" : tone;
  return (
    <SectionNotice tone={sectionTone} role={role ?? (tone === "error" ? "alert" : "status")}>
      {children}
    </SectionNotice>
  );
}
