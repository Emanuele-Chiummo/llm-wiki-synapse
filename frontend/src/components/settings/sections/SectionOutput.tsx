/**
 * SectionOutput.tsx — language + conversation history (F16).
 * Extracted from SettingsPanel monolith (ADR-0055).
 *
 * Changes write to the DRAFT layer; the SettingsSaveFooter commits on Save (F16).
 * i18n.changeLanguage is called by SettingsSaveFooter at commit time, not here.
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, Field } from "../ui";
import {
  useSettingsStore,
  selectDraftLanguage,
  selectDraftConversationHistoryLength,
  selectSetDraftLanguage,
  selectSetDraftConversationHistoryLength,
  CONV_HISTORY_OPTIONS,
  type ConvHistoryLength,
} from "../../../store/settingsStore";

export function SectionOutput() {
  const { t } = useTranslation();
  const draftLanguage = useSettingsStore(selectDraftLanguage);
  const setDraftLanguage = useSettingsStore(selectSetDraftLanguage);
  const draftConvHistory = useSettingsStore(selectDraftConversationHistoryLength);
  const setDraftConvHistory = useSettingsStore(selectSetDraftConversationHistoryLength);

  const turns = Math.round(draftConvHistory / 2);

  return (
    <div>
      <SectionHeader title={t("settings.output.title")} desc={t("settings.output.desc")} />

      <Field label={t("settings.language")}>
        <div style={{ display: "flex", gap: 6 }}>
          {(["en", "it"] as const).map((lang) => {
            const on = draftLanguage === lang;
            return (
              <button
                key={lang}
                onClick={() => setDraftLanguage(lang)}
                aria-pressed={on}
                style={{
                  padding: "7px 16px",
                  border: `1px solid ${on ? "var(--syn-accent)" : "var(--syn-border)"}`,
                  borderRadius: 8,
                  background: on ? "var(--syn-accent)" : "var(--syn-surface)",
                  color: on ? "#fff" : "var(--syn-text-muted)",
                  fontSize: 12.5,
                  cursor: "pointer",
                  fontWeight: on ? 600 : 500,
                  transition: "background 0.12s, color 0.12s",
                }}
              >
                {lang === "en" ? t("settings.languageEn") : t("settings.languageIt")}
              </button>
            );
          })}
        </div>
      </Field>

      <Field label={t("settings.output.convHistoryTitle")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.output.convHistoryDesc")}
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {CONV_HISTORY_OPTIONS.map((n) => {
            const on = draftConvHistory === n;
            return (
              <button
                key={n}
                onClick={() => setDraftConvHistory(n as ConvHistoryLength)}
                aria-pressed={on}
                style={{
                  width: 40,
                  height: 40,
                  border: `1px solid ${on ? "var(--syn-accent)" : "var(--syn-border)"}`,
                  borderRadius: 8,
                  background: on ? "var(--syn-accent)" : "var(--syn-surface)",
                  color: on ? "#fff" : "var(--syn-text-muted)",
                  fontSize: 13,
                  fontWeight: on ? 600 : 500,
                  cursor: "pointer",
                  transition: "background 0.12s, color 0.12s",
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("settings.output.convHistoryLabel", { count: draftConvHistory, turns })}
        </p>
      </Field>
    </div>
  );
}
