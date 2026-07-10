/**
 * SectionCliAuth.tsx — CLI subscription auth sub-block (ADR-0043 §2.6).
 * Extracted from SectionApiMcp so it can be co-located on the Provider page
 * alongside SectionLlmModels (item 9 fix / v1.3.9 IA redesign).
 *
 * Logic is identical to the original inline function in SectionApiMcp.
 * I3: single fetch on mount; no Zustand store; local state only.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { INPUT_STYLE, BTN_PRIMARY } from "../ui";
import { getCliAuthConfig, setCliAuthConfig } from "../../../api/providerClient";
import type { CliAuthConfig } from "../../../api/types";

export function SectionCliAuth({ embedded = false }: { embedded?: boolean } = {}) {
  const { t } = useTranslation();
  const [posture, setPosture] = useState<CliAuthConfig | null>(null);
  const [err, setErr] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    getCliAuthConfig(ac.signal)
      .then((data) => { setPosture(data); setErr(false); })
      .catch((e: unknown) => { if (!(e instanceof Error) || e.name !== "AbortError") setErr(true); });
    return () => { ac.abort(); };
  }, []);

  const applyPosture = (resp: CliAuthConfig) => {
    setPosture(resp);
    setTokenInput("");
    setSaveErr(null);
  };

  const handleSave = async () => {
    if (busy) return;
    const trimmed = tokenInput.trim();
    if (trimmed === "") { setSaveErr(t("settings.cliAuth.emptyTokenError")); return; }
    setBusy(true);
    setSaveErr(null);
    try {
      const resp = await setCliAuthConfig({ token: trimmed });
      applyPosture(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setSaveErr(t("settings.cliAuth.saveError"));
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    if (busy) return;
    setBusy(true);
    setSaveErr(null);
    try {
      const resp = await setCliAuthConfig({ clear: true });
      applyPosture(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setSaveErr(t("settings.cliAuth.saveError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="cli-auth-section">
      {!embedded && (
        <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          {t("settings.cliAuth.title")}
        </p>
      )}

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.cliAuth.error")}</p>
      ) : posture === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.cliAuth.loading")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span data-testid="cli-auth-configured-badge" style={{ padding: "2px 8px", borderRadius: 4, background: posture.token_configured ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)", border: `1px solid ${posture.token_configured ? "color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)" : "color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)"}`, color: posture.token_configured ? "var(--syn-green)" : "var(--syn-red)", fontSize: 11, fontWeight: 600 }}>
              {posture.token_configured ? t("settings.cliAuth.configuredBadge") : t("settings.cliAuth.notConfiguredBadge")}
            </span>
            <span data-testid="cli-auth-source-badge" style={{ padding: "2px 8px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-muted)", fontSize: 11 }}>
              {t("settings.cliAuth.sourceBadge", { source: posture.token_source })}
            </span>
            <span data-testid="cli-auth-mode-badge" style={{ padding: "2px 8px", borderRadius: 4, background: posture.auth_mode === "subscription" ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "var(--syn-surface-hover)", color: posture.auth_mode === "subscription" ? "var(--syn-green)" : "var(--syn-text-muted)", fontSize: 11 }}>
              {t(`settings.cliAuth.authMode.${posture.auth_mode}`)}
            </span>
          </div>

          <div>
            <label style={{ display: "block", marginBottom: 6, fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
              {t("settings.cliAuth.tokenLabel")}
            </label>
            <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.cliAuth.tokenHelp")}
            </p>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ position: "relative", flex: 1, minWidth: 200, display: "flex" }}>
                <input type={showToken ? "text" : "password"} data-testid="cli-auth-token-input" value={tokenInput} onChange={(e) => { setTokenInput(e.target.value); setSaveErr(null); }} placeholder={t("settings.cliAuth.tokenPlaceholder")} autoComplete="off" style={{ ...INPUT_STYLE, flex: 1, minWidth: 0, paddingRight: 36 }} />
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
              <button data-testid="cli-auth-save-btn" onClick={() => { void handleSave(); }} disabled={busy} style={{ ...BTN_PRIMARY, opacity: busy ? 0.4 : 1, cursor: busy ? "not-allowed" : "pointer", flexShrink: 0 }}>
                {busy ? "…" : t("settings.cliAuth.saveButton")}
              </button>
              {posture.token_configured && (
                <button data-testid="cli-auth-clear-btn" onClick={() => { void handleClear(); }} disabled={busy} style={{ padding: "6px 14px", border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)", borderRadius: 6, background: "transparent", color: "var(--syn-red)", fontSize: 12, cursor: busy ? "not-allowed" : "pointer", fontWeight: 500, opacity: busy ? 0.4 : 1, flexShrink: 0 }}>
                  {t("settings.cliAuth.clearButton")}
                </button>
              )}
            </div>
            {saveErr && <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}>{saveErr}</p>}
          </div>

          <div data-testid="cli-auth-guide" style={{ padding: "10px 14px", background: "var(--syn-bg-soft)", border: "1px solid var(--syn-border)", borderRadius: 8, fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.7 }}>
            <p style={{ margin: "0 0 6px", fontWeight: 600, color: "var(--syn-text-muted)" }}>{t("settings.cliAuth.guideTitle")}</p>
            <p style={{ margin: 0, whiteSpace: "pre-line" }}>{t("settings.cliAuth.guideSteps")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
