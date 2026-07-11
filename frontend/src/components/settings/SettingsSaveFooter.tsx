/**
 * SettingsSaveFooter.tsx — unified Save bar for client-preference sections (F16).
 *
 * Renders only when `isDirty` (any staged draft differs from committed value).
 * Primary action: "Salva modifiche" — commits drafts, persists, applies theme to DOM,
 *   then calls i18n.changeLanguage so the UI language switches immediately.
 * Secondary action: "Annulla" — discards all staged drafts (reverts to committed values).
 *
 * BRANDING: primary button uses var(--syn-accent) fill with white text — never black (F16).
 */
import { useTranslation } from "react-i18next";
import {
  useSettingsStore,
  selectIsDirty,
  selectDraftLanguage,
  selectCommitDraft,
  selectDiscardDraft,
} from "../../store/settingsStore";

export function SettingsSaveFooter() {
  const { t, i18n } = useTranslation();
  const isDirty = useSettingsStore(selectIsDirty);
  const draftLanguage = useSettingsStore(selectDraftLanguage);
  const commitDraft = useSettingsStore(selectCommitDraft);
  const discardDraft = useSettingsStore(selectDiscardDraft);

  if (!isDirty) return null;

  const handleSave = () => {
    // Capture before commit (commit resets drafts to match committed)
    const lang = draftLanguage;
    commitDraft();
    // Apply i18n language switch after commit so the language key is persisted first
    void i18n.changeLanguage(lang);
  };

  return (
    <div
      data-testid="settings-save-footer"
      style={{
        flexShrink: 0,
        borderTop: "1px solid var(--syn-border)",
        background: "var(--syn-surface)",
        padding: "12px 40px",
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: 10,
      }}
    >
      <button
        data-testid="settings-discard-btn"
        onClick={discardDraft}
        style={{
          padding: "7px 18px",
          border: "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-sm, 6px)",
          background: "var(--syn-surface)",
          color: "var(--syn-text-muted)",
          fontSize: 13,
          cursor: "pointer",
          fontWeight: 400,
        }}
      >
        {t("settings.footer.discard")}
      </button>
      <button
        data-testid="settings-save-btn"
        onClick={handleSave}
        style={{
          padding: "7px 20px",
          border: "none",
          borderRadius: "var(--syn-radius-sm, 6px)",
          background: "var(--syn-accent)",
          color: "#fff",
          fontSize: 13,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        {t("settings.footer.save")}
      </button>
    </div>
  );
}
