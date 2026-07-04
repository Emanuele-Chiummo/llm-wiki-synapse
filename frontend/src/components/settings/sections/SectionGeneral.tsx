/**
 * SectionGeneral.tsx — context window + budget split (F14).
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useTranslation } from "react-i18next";
import { SectionHeader, Field, BudgetRow, INPUT_STYLE } from "../ui";
import {
  useSettingsStore,
  selectContextWindow,
  selectSetContextWindow,
  CONTEXT_WINDOW_OPTIONS,
  computeBudgetSplit,
  formatTokenCount,
} from "../../../store/settingsStore";

export function SectionGeneral() {
  const { t } = useTranslation();
  const contextWindow = useSettingsStore(selectContextWindow);
  const setContextWindow = useSettingsStore(selectSetContextWindow);
  const budget = computeBudgetSplit(contextWindow);

  return (
    <div>
      <SectionHeader title={t("settings.nav.general")} desc={t("settings.contextWindowHelp")} />

      <Field label={t("settings.contextWindow")}>
        <select
          id="ctx-select"
          value={contextWindow}
          onChange={(e) => setContextWindow(Number(e.target.value) as typeof contextWindow)}
          style={INPUT_STYLE}
        >
          {CONTEXT_WINDOW_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>{formatTokenCount(opt)}</option>
          ))}
        </select>
      </Field>

      <div style={{ marginTop: 24 }}>
        <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
          {t("settings.budgetSplit")}
        </p>
        <BudgetRow label={t("settings.budgetHistory")}    pct={60} tokens={budget.history} />
        <BudgetRow label={t("settings.budgetRetrieved")}  pct={20} tokens={budget.retrieved} />
        <BudgetRow label={t("settings.budgetSystem")}     pct={5}  tokens={budget.system} />
        <BudgetRow label={t("settings.budgetGeneration")} pct={15} tokens={budget.generation} />
      </div>
    </div>
  );
}
