/**
 * SectionAbout.tsx — version + links.
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { PRODUCT_IDENTITY } from "../../../config/productIdentity";

declare const __APP_VERSION__: string;

export function SectionAbout() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("settings.nav.about")}
        desc={`${PRODUCT_IDENTITY.displayName} — ${PRODUCT_IDENTITY.descriptor}`}
      />

      <p
        style={{
          margin: "0 0 20px",
          color: "var(--syn-text-muted)",
          fontFamily: "var(--syn-font-wordmark)",
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        {PRODUCT_IDENTITY.tagline}
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "8px 16px",
          fontSize: 12,
          marginBottom: 24,
        }}
      >
        <span style={{ color: "var(--syn-text-dim)" }}>{t("settings.about.version")}</span>
        <span style={{ color: "var(--syn-text)", fontFamily: "monospace" }}>
          v{__APP_VERSION__}
        </span>
      </div>

      <p
        style={{
          margin: "0 0 8px",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--syn-text-dim)",
        }}
      >
        {t("settings.about.links")}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <a
          href="https://github.com/nashsu/llm_wiki"
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 12, color: "var(--syn-accent)", textDecoration: "none" }}
        >
          {t("settings.about.github")} ↗
        </a>
      </div>
    </div>
  );
}
