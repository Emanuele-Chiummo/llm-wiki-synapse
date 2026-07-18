/**
 * QuickActionsBlock.tsx — compact row of 3 navigation shortcuts [F18][v1.5].
 * Pure UI; no API calls. Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useTranslation } from "react-i18next";
import { Upload, MessageCircle, FlaskConical } from "lucide-react";
import type { Section } from "../../store/appStore";

interface QuickActionsBlockProps {
  setActiveSection: (section: Section) => void;
}

export function QuickActionsBlock({ setActiveSection }: QuickActionsBlockProps) {
  const { t } = useTranslation();

  const ACTIONS = [
    {
      label: t("home.quickActions.ingestSource"),
      icon: <Upload size={13} aria-hidden="true" />,
      section: "ingest" as Section,
      testId: "home-quick-action-ingest",
    },
    {
      label: t("home.quickActions.askQuestion"),
      icon: <MessageCircle size={13} aria-hidden="true" />,
      section: "chat" as Section,
      testId: "home-quick-action-chat",
    },
    {
      label: t("home.quickActions.deepResearch"),
      icon: <FlaskConical size={13} aria-hidden="true" />,
      section: "deep-search" as Section,
      testId: "home-quick-action-deep-search",
    },
  ] as const;

  return (
    <section
      aria-label={t("home.quickActions.ariaLabel")}
      data-testid="home-quick-actions"
      style={{ display: "flex", gap: 10 }}
    >
      {ACTIONS.map((action) => {
        const primary = action.section === "ingest";
        return (
          <button
            key={action.section}
            type="button"
            data-testid={action.testId}
            onClick={() => setActiveSection(action.section)}
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              padding: "10px 12px",
              borderRadius: "var(--syn-radius-md)",
              border: `1px solid ${primary ? "var(--syn-accent)" : "var(--syn-border)"}`,
              background: primary ? "var(--syn-accent)" : "var(--syn-bg-soft)",
              color: primary ? "#fff" : "var(--syn-text-muted)",
              fontSize: 12,
              fontWeight: primary ? 600 : 500,
              cursor: "pointer",
              transition: "border-color 0.1s ease, color 0.1s ease, background 0.1s ease",
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLButtonElement;
              if (primary) {
                el.style.background = "var(--syn-accent-strong)";
                el.style.borderColor = "var(--syn-accent-strong)";
              } else {
                el.style.borderColor = "var(--syn-accent)";
                el.style.color = "var(--syn-accent)";
                el.style.background = "var(--syn-surface-hover)";
              }
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLButtonElement;
              if (primary) {
                el.style.background = "var(--syn-accent)";
                el.style.borderColor = "var(--syn-accent)";
              } else {
                el.style.borderColor = "var(--syn-border)";
                el.style.color = "var(--syn-text-muted)";
                el.style.background = "var(--syn-bg-soft)";
              }
            }}
          >
            {action.icon}
            {action.label}
          </button>
        );
      })}
    </section>
  );
}
