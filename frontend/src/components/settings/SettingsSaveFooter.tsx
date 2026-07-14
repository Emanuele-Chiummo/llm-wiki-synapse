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
        className="syn-btn syn-btn--ghost"
        style={{ fontSize: 13, padding: "7px 18px" }}
      >
        {t("settings.footer.discard")}
      </button>
      <button
        data-testid="settings-save-btn"
        onClick={handleSave}
        className="syn-btn syn-btn--primary"
        style={{ fontSize: 13, padding: "7px 20px" }}
      >
        {t("settings.footer.save")}
      </button>
    </div>
  );
}
