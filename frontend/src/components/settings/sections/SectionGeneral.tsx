/**
 * SectionGeneral.tsx — context window + budget split (F14).
 * Extracted from SettingsPanel monolith (ADR-0055).
 *
 * Changes write to the DRAFT layer; the SettingsSaveFooter commits on Save (F16).
 */
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, Field, BudgetRow } from "../ui";
import {
  useSettingsStore,
  selectDraftContextWindow,
  selectSetDraftContextWindow,
  CONTEXT_WINDOW_OPTIONS,
  computeBudgetSplit,
  formatTokenCount,
  type ContextWindowTokens,
} from "../../../store/settingsStore";

// LLM Wiki card style — bordered surface card (brand colors only, never black).
const GEN_CARD: CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "14px 16px",
  marginBottom: 16,
};

export function SectionGeneral() {
  const { t } = useTranslation();
  const draftContextWindow = useSettingsStore(selectDraftContextWindow);
  const setDraftContextWindow = useSettingsStore(selectSetDraftContextWindow);
  const budget = computeBudgetSplit(draftContextWindow);

  return (
    <div>
      <SectionHeader title={t("settings.nav.general")} desc={t("settings.contextWindowHelp")} />

      <div style={GEN_CARD}>
        <Field label={t("settings.contextWindow")}>
          <select
            id="ctx-select"
            value={draftContextWindow}
            onChange={(e) => setDraftContextWindow(Number(e.target.value) as ContextWindowTokens)}
            className="syn-input"
          >
            {CONTEXT_WINDOW_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>{formatTokenCount(opt)}</option>
            ))}
          </select>
        </Field>
      </div>

      <div style={GEN_CARD}>
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
