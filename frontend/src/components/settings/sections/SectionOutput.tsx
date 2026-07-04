/**
 * SectionOutput.tsx — language + conversation history (F16).
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, Field } from "../ui";
import {
  useSettingsStore,
  selectLanguage,
  selectConversationHistoryLength,
  selectSetLanguage,
  selectSetConversationHistoryLength,
  CONV_HISTORY_OPTIONS,
  type ConvHistoryLength,
} from "../../../store/settingsStore";

export function SectionOutput() {
  const { t, i18n } = useTranslation();
  const language = useSettingsStore(selectLanguage);
  const setLanguage = useSettingsStore(selectSetLanguage);
  const convHistory = useSettingsStore(selectConversationHistoryLength);
  const setConvHistory = useSettingsStore(selectSetConversationHistoryLength);

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang);
    void i18n.changeLanguage(lang);
  };

  const turns = Math.round(convHistory / 2);

  return (
    <div>
      <SectionHeader title={t("settings.output.title")} desc={t("settings.output.desc")} />

      <Field label={t("settings.language")}>
        <div style={{ display: "flex", gap: 8 }}>
          {(["en", "it"] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => handleLanguageChange(lang)}
              aria-pressed={language === lang}
              style={{
                padding: "6px 16px",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                background: language === lang ? "var(--syn-accent-soft)" : "transparent",
                color: language === lang ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: language === lang ? 600 : 400,
              }}
            >
              {lang === "en" ? t("settings.languageEn") : t("settings.languageIt")}
            </button>
          ))}
        </div>
      </Field>

      <Field label={t("settings.output.convHistoryTitle")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.output.convHistoryDesc")}
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {CONV_HISTORY_OPTIONS.map((n) => (
            <button
              key={n}
              onClick={() => setConvHistory(n as ConvHistoryLength)}
              aria-pressed={convHistory === n}
              style={{
                width: 40,
                height: 40,
                border: "1px solid var(--syn-border)",
                borderRadius: 8,
                background: convHistory === n ? "var(--syn-accent-soft)" : "transparent",
                color: convHistory === n ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 13,
                fontWeight: convHistory === n ? 600 : 400,
                cursor: "pointer",
                outline: convHistory === n ? `1px solid var(--syn-accent)` : "none",
              }}
            >
              {n}
            </button>
          ))}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("settings.output.convHistoryLabel", { count: convHistory, turns })}
        </p>
      </Field>
    </div>
  );
}
