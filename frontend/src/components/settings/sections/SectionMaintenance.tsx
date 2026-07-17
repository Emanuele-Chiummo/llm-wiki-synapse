/**
 * SectionMaintenance.tsx — duplicate detection + settings reset.
 * Extracted from SettingsPanel monolith (ADR-0055).
 *
 * FE-A11Y-2: replaced window.confirm with ConfirmDialog for accessible confirmation.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { SectionHeader } from "../ui";
import { Button } from "../../ui/Button";
import {
  useSettingsStore,
  selectResetSettings,
} from "../../../store/settingsStore";
import { ConfirmDialog } from "../../common/ConfirmDialog";

export function SectionMaintenance() {
  const { t, i18n } = useTranslation();
  const reset = useSettingsStore(selectResetSettings);
  const [showResetDialog, setShowResetDialog] = useState(false);

  const handleReset = () => {
    setShowResetDialog(true);
  };

  const confirmReset = () => {
    setShowResetDialog(false);
    reset();
    void i18n.changeLanguage("en");
  };

  const cancelReset = () => {
    setShowResetDialog(false);
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.maintenance")} desc={t("settings.maintenance.desc")} />

      {/* Detect duplicates */}
      <div style={{ padding: 16, border: "1px solid var(--syn-border)", borderRadius: 8, background: "var(--syn-bg-soft)", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ marginTop: 1, opacity: 0.6 }}>
            <Wrench size={16} strokeWidth={1.75} aria-hidden="true" />
          </span>
          <div style={{ flex: 1 }}>
            <p style={{ margin: "0 0 4px", fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
              {t("settings.maintenance.duplicates")}
            </p>
            <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.maintenance.duplicatesDesc")}
            </p>
            <Button variant="accent-ghost" disabled>
              {t("settings.maintenance.duplicatesScan")}
            </Button>
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

      {showResetDialog && (
        <ConfirmDialog
          title={t("settings.maintenance.dangerZone")}
          body={t("settings.maintenance.resetConfirm")}
          confirmLabel={t("settings.maintenance.reset")}
          cancelLabel={t("common.cancel")}
          danger
          onConfirm={confirmReset}
          onCancel={cancelReset}
        />
      )}
    </div>
  );
}
