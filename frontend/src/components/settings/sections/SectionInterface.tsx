/**
 * SectionInterface.tsx — display preferences / theme (ADR-0048).
 * Extracted from SettingsPanel monolith (ADR-0055).
 *
 * Changes write to the DRAFT layer; the SettingsSaveFooter commits on Save (F16).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, Field } from "../ui";
import {
  useSettingsStore,
  selectDraftTheme,
  selectSetDraftTheme,
  type Theme,
} from "../../../store/settingsStore";

export function SectionInterface() {
  const { t } = useTranslation();
  const draftTheme = useSettingsStore(selectDraftTheme);
  const setDraftTheme = useSettingsStore(selectSetDraftTheme);

  // LLM Wiki order: Light · Dark · System.
  const THEME_OPTIONS: { value: Theme; labelKey: string }[] = [
    { value: "light",  labelKey: "settings.theme.light" },
    { value: "dark",   labelKey: "settings.theme.dark" },
    { value: "system", labelKey: "settings.theme.system" },
  ];

  return (
    <div>
      <SectionHeader title={t("settings.nav.interface")} desc={t("settings.interface.desc")} />

      <Field label={t("settings.theme.label")}>
        {/* Segmented control — selected = brand accent (never black), help BELOW the buttons */}
        <div style={{ display: "flex", gap: 6 }}>
          {THEME_OPTIONS.map(({ value, labelKey }) => {
            const on = draftTheme === value;
            return (
              <button
                key={value}
                data-testid={`theme-btn-${value}`}
                onClick={() => setDraftTheme(value)}
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
                {t(labelKey)}
              </button>
            );
          })}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
          {t("settings.theme.help")}
        </p>
      </Field>
    </div>
  );
}
