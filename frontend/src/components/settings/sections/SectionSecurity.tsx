/**
 * SectionSecurity.tsx — client-side token management (ADR-0052 §4.6).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * The Authorization header is NEVER constructed here — only setAuthToken /
 * clearAuthToken are called (base.ts keeps the single injection point, ADR-0052 Do-NOT §10).
 */
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAuthToken, setAuthToken, clearAuthToken, apiBase } from "../../../api/base";
import { INPUT_STYLE, BTN_PRIMARY, BTN_SECONDARY } from "../ui";

export function SectionSecurity() {
  const { t } = useTranslation();
  const [newToken, setNewToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [saved, setSaved] = useState(false);
  const [cleared, setCleared] = useState(false);

  const currentServerUrl = apiBase() || window.location.origin;
  const hasToken = getAuthToken() !== null;

  const handleUpdate = useCallback(() => {
    const trimmed = newToken.trim();
    if (!trimmed) return;
    setAuthToken(trimmed);
    setNewToken("");
    setSaved(true);
    setCleared(false);
    setTimeout(() => setSaved(false), 2500);
  }, [newToken]);

  const handleClear = useCallback(() => {
    clearAuthToken();
    setCleared(true);
    setSaved(false);
    setTimeout(() => setCleared(false), 2500);
  }, []);

  return (
    <div>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4, color: "var(--syn-text)" }}>
        {t("settings.security.title")}
      </h2>
      <p style={{ fontSize: 13, color: "var(--syn-text-dim)", marginBottom: 24, lineHeight: 1.6 }}>
        {t("settings.security.desc")}
      </p>

      {/* Server URL (read-only) */}
      <div style={{ marginBottom: 20 }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", display: "block", marginBottom: 4 }}>
          {t("settings.security.serverUrl")}
        </label>
        <div style={{ fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", color: "var(--syn-text-dim)", background: "var(--syn-bg-soft)", border: "1px solid var(--syn-border)", borderRadius: 6, padding: "8px 12px" }}>
          {currentServerUrl}
        </div>
        <p style={{ fontSize: 11, color: "var(--syn-text-dim)", marginTop: 4 }}>
          {hasToken ? t("settings.security.tokenPresent") : t("settings.security.tokenAbsent")}
        </p>
      </div>

      {/* Rotate token field */}
      <div style={{ marginBottom: 12 }}>
        <label htmlFor="settings-security-token" style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", display: "block", marginBottom: 4 }}>
          {t("settings.security.rotateLabel")}
        </label>
        <div style={{ position: "relative", marginBottom: 8 }}>
          <input
            id="settings-security-token"
            type={showToken ? "text" : "password"}
            value={newToken}
            onChange={(e) => setNewToken(e.target.value)}
            placeholder={t("settings.security.rotatePlaceholder")}
            autoComplete="new-password"
            style={{ ...INPUT_STYLE, paddingRight: 40, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
          />
          <button
            type="button"
            onClick={() => setShowToken((v) => !v)}
            aria-label={showToken ? t("connect.hideToken") : t("connect.showToken")}
            style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", padding: 2, color: "var(--syn-text-dim)", display: "flex", alignItems: "center" }}
          >
            {showToken ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
            )}
          </button>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" onClick={handleUpdate} disabled={newToken.trim().length === 0} style={{ ...BTN_PRIMARY, opacity: newToken.trim().length === 0 ? 0.5 : 1 }} data-testid="security-update-btn">
            {t("settings.security.update")}
          </button>
          <button type="button" onClick={handleClear} style={BTN_SECONDARY} data-testid="security-clear-btn">
            {t("settings.security.clear")}
          </button>
          {saved && <span style={{ fontSize: 12, color: "var(--syn-green)" }}>{t("settings.security.saved")}</span>}
          {cleared && <span style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("settings.security.cleared")}</span>}
        </div>
      </div>

      {/* Asymmetry banner (ADR-0052 §2.6 / AC-R10-2-5) */}
      <div style={{ marginTop: 24, padding: "12px 14px", background: "var(--syn-notice-info-bg, #eff6ff)", border: "1px solid var(--syn-notice-info-border, #bfdbfe)", borderRadius: 8, fontSize: 12, color: "var(--syn-text)", lineHeight: 1.6 }}>
        <p style={{ fontWeight: 600, marginBottom: 4 }}>{t("settings.security.asymmetryTitle")}</p>
        <p style={{ margin: 0, color: "var(--syn-text-dim)" }}>{t("settings.security.asymmetryNote")}</p>
      </div>
    </div>
  );
}
