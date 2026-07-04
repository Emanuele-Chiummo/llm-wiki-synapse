/**
 * SectionInterface.tsx — display preferences / theme (ADR-0048).
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, Field } from "../ui";
import {
  useSettingsStore,
  selectTheme,
  selectSetTheme,
  type Theme,
} from "../../../store/settingsStore";

export function SectionInterface() {
  const { t } = useTranslation();
  const theme = useSettingsStore(selectTheme);
  const setTheme = useSettingsStore(selectSetTheme);

  const THEME_OPTIONS: { value: Theme; labelKey: string }[] = [
    { value: "system", labelKey: "settings.theme.system" },
    { value: "light",  labelKey: "settings.theme.light" },
    { value: "dark",   labelKey: "settings.theme.dark" },
  ];

  return (
    <div>
      <SectionHeader title={t("settings.nav.interface")} desc={t("settings.interface.desc")} />

      <Field label={t("settings.theme.label")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.theme.help")}
        </p>
        <div style={{ display: "flex", gap: 6 }}>
          {THEME_OPTIONS.map(({ value, labelKey }) => (
            <button
              key={value}
              data-testid={`theme-btn-${value}`}
              onClick={() => setTheme(value)}
              aria-pressed={theme === value}
              style={{
                padding: "6px 14px",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                background: theme === value ? "var(--syn-accent-soft)" : "transparent",
                color: theme === value ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: theme === value ? 600 : 400,
              }}
            >
              {t(labelKey)}
            </button>
          ))}
        </div>
      </Field>
    </div>
  );
}
