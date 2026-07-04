/**
 * SectionMaintenance.tsx — duplicate detection + settings reset.
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, BTN_PRIMARY } from "../ui";
import {
  useSettingsStore,
  selectResetSettings,
} from "../../../store/settingsStore";

export function SectionMaintenance() {
  const { t, i18n } = useTranslation();
  const reset = useSettingsStore(selectResetSettings);

  const handleReset = () => {
    if (window.confirm(t("settings.maintenance.resetConfirm"))) {
      reset();
      void i18n.changeLanguage("en");
    }
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.maintenance")} desc={t("settings.maintenance.desc")} />

      {/* Detect duplicates */}
      <div style={{ padding: 16, border: "1px solid var(--syn-border)", borderRadius: 8, background: "var(--syn-bg-soft)", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ marginTop: 1, opacity: 0.6 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
            </svg>
          </span>
          <div style={{ flex: 1 }}>
            <p style={{ margin: "0 0 4px", fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
              {t("settings.maintenance.duplicates")}
            </p>
            <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.maintenance.duplicatesDesc")}
            </p>
            <button disabled style={{ ...BTN_PRIMARY, opacity: 0.4, cursor: "not-allowed" }}>
              {t("settings.maintenance.duplicatesScan")}
            </button>
            <span style={{ marginLeft: 8, fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("settings.maintenance.duplicatesComingSoon")}
            </span>
          </div>
        </div>
      </div>

      {/* Danger zone */}
      <div style={{ padding: 16, border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)", borderRadius: 8, marginBottom: 16 }}>
        <p style={{ margin: "0 0 4px", fontSize: 12, fontWeight: 600, color: "var(--syn-red)" }}>
          {t("settings.maintenance.dangerZone")}
        </p>
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.maintenance.resetDesc")}
        </p>
        <button
          onClick={handleReset}
          data-testid="settings-reset-btn"
          style={{
            padding: "6px 16px",
            border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
            borderRadius: 6,
            background: "transparent",
            color: "var(--syn-red)",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {t("settings.maintenance.reset")}
        </button>
      </div>
    </div>
  );
}
