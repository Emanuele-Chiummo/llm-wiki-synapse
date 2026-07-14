/**
 * SectionWebClipper.tsx — Chrome MV3 web clipper config (ADR-0040, F11).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * Mirrors SectionApiMcp token UX — generate/rotate/clear with one-time reveal.
 * I3: single fetch on mount; PUT on each user action; local state only.
 */
import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, Field, EmbedRow, INPUT_STYLE, BTN_PRIMARY } from "../ui";

// Match this section's existing soft-panel blocks (brand colors only, never black).
const WC_PANEL: CSSProperties = {
  padding: "12px 14px",
  background: "var(--syn-bg-soft)",
  border: "1px solid var(--syn-border)",
  borderRadius: 8,
};
import { fetchClipConfig, setClipConfig } from "../../../api/providerClient";
import type { ClipConfigResponse, ClipConfigStateResponse } from "../../../api/types";

export function SectionWebClipper() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<ClipConfigResponse | null>(null);
  const [err, setErr] = useState(false);

  const [enabled, setEnabled] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [tokenSource, setTokenSource] = useState<"db" | "env" | "none">("none");
  const [allowedOrigins, setAllowedOrigins] = useState<string[]>([]);
  const [originsInput, setOriginsInput] = useState("");
  const [maxBodyBytes, setMaxBodyBytes] = useState(0);

  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [copiedGenToken, setCopiedGenToken] = useState(false);

  const [authBusy, setAuthBusy] = useState(false);
  const [enableBusy, setEnableBusy] = useState(false);
  const [originsBusy, setOriginsBusy] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchClipConfig(ac.signal)
      .then((data) => {
        setCfg(data);
        setErr(false);
        setEnabled(data.enabled);
        setTokenConfigured(data.token_configured);
        setTokenSource(data.token_source);
        setAllowedOrigins(data.allowed_origins);
        setOriginsInput(data.allowed_origins.join(", "));
        setMaxBodyBytes(data.max_body_bytes);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    return () => {
      ac.abort();
    };
  }, []);

  const applyClipResponse = (resp: ClipConfigStateResponse) => {
    setEnabled(resp.enabled);
    setTokenConfigured(resp.token_configured);
    setTokenSource(resp.token_source);
    setAllowedOrigins(resp.allowed_origins);
    setOriginsInput(resp.allowed_origins.join(", "));
    setMaxBodyBytes(resp.max_body_bytes);
  };

  const handleEnableToggle = async () => {
    if (enableBusy) return;
    setEnableBusy(true);
    try {
      const resp = await setClipConfig({ set_enabled: !enabled });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setEnableBusy(false);
    }
  };

  const handleGenerateToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null);
    try {
      const resp = await setClipConfig({ rotate_token: true });
      applyClipResponse(resp);
      if (resp.generated_token) setGeneratedToken(resp.generated_token);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  const handleClearToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null);
    try {
      const resp = await setClipConfig({ clear_token: true });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  const handleSaveOrigins = async () => {
    if (originsBusy) return;
    setOriginsBusy(true);
    try {
      const resp = await setClipConfig({ set_allowed_origins: originsInput });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setOriginsBusy(false);
    }
  };

  const handleDismissGeneratedToken = () => {
    setGeneratedToken(null);
  };

  const handleCopyGeneratedToken = () => {
    if (!generatedToken) return;
    navigator.clipboard
      .writeText(generatedToken)
      .then(() => {
        setCopiedGenToken(true);
        setTimeout(() => setCopiedGenToken(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const clipUrl = `${window.location.origin}/clip`;

  const handleCopyClipUrl = () => {
    navigator.clipboard
      .writeText(clipUrl)
      .then(() => {
        setCopiedUrl(true);
        setTimeout(() => setCopiedUrl(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const tokenPostureKey = !tokenConfigured
    ? "settings.webClipper.postureNone"
    : tokenSource === "db"
      ? "settings.webClipper.postureDb"
      : "settings.webClipper.postureEnv";

  return (
    <div>
      <SectionHeader title={t("settings.nav.webClipper")} desc={t("settings.webClipper.desc")} />

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>
          {t("settings.webClipper.error")}
        </p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>
          {t("settings.webClipper.loading")}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {/* Enable toggle */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${enabled ? "var(--syn-accent)" : "var(--syn-border)"}`,
                borderRadius: 8,
                opacity: enableBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  cursor: "pointer",
                  userSelect: "none",
                  flex: 1,
                }}
              >
                <span
                  style={{
                    position: "relative",
                    display: "inline-block",
                    width: 36,
                    height: 20,
                    flexShrink: 0,
                  }}
                >
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={t("settings.webClipper.enabledLabel")}
                    data-testid="clip-enabled-toggle"
                    checked={enabled}
                    disabled={enableBusy}
                    onChange={() => {
                      void handleEnableToggle();
                    }}
                    style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      display: "block",
                      width: 36,
                      height: 20,
                      borderRadius: 10,
                      background: enabled ? "var(--syn-accent)" : "var(--syn-border)",
                      border: `1px solid ${enabled ? "var(--syn-accent)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: enabled ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: enabled ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: enabled ? "var(--syn-text)" : "var(--syn-text-muted)",
                    }}
                  >
                    {t("settings.webClipper.enabledLabel")}
                  </span>
                  <p
                    style={{
                      margin: "3px 0 0",
                      fontSize: 11,
                      color: "var(--syn-text-muted)",
                      lineHeight: 1.5,
                    }}
                  >
                    {t("settings.webClipper.enabledHelp")}
                  </p>
                </div>
              </label>
            </div>
          </div>

          {/* Token sub-block */}
          <div>
            <p
              style={{
                margin: "0 0 12px",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              {t("settings.webClipper.tokenTitle")}
            </p>

            <div
              style={{
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: "1px solid var(--syn-border)",
                borderRadius: 8,
                marginBottom: 10,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)",
                    flexShrink: 0,
                    display: "inline-block",
                  }}
                />
                <span
                  data-testid="clip-token-posture"
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)",
                  }}
                >
                  {t(tokenPostureKey)}
                </span>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  data-testid="clip-generate-token-btn"
                  onClick={() => {
                    void handleGenerateToken();
                  }}
                  disabled={authBusy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: authBusy ? 0.4 : 1,
                    cursor: authBusy ? "not-allowed" : "pointer",
                  }}
                >
                  {authBusy
                    ? "…"
                    : tokenConfigured
                      ? t("settings.webClipper.rotateToken")
                      : t("settings.webClipper.generateToken")}
                </button>
                {tokenConfigured && (
                  <button
                    data-testid="clip-clear-token-btn"
                    onClick={() => {
                      void handleClearToken();
                    }}
                    disabled={authBusy}
                    style={{
                      padding: "6px 14px",
                      border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                      borderRadius: 6,
                      background: "transparent",
                      color: "var(--syn-red)",
                      fontSize: 12,
                      cursor: authBusy ? "not-allowed" : "pointer",
                      fontWeight: 500,
                      opacity: authBusy ? 0.4 : 1,
                    }}
                  >
                    {t("settings.webClipper.clearToken")}
                  </button>
                )}
              </div>
            </div>

            {generatedToken !== null && (
              <div
                style={{
                  padding: "12px 14px",
                  background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
                  border:
                    "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                  borderRadius: 8,
                  marginBottom: 10,
                }}
              >
                <p
                  style={{
                    margin: "0 0 6px",
                    fontSize: 12,
                    fontWeight: 700,
                    color: "var(--syn-green)",
                  }}
                >
                  {t("settings.webClipper.revealWarning")}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    data-testid="clip-generated-token"
                    style={{
                      flex: 1,
                      fontFamily: "var(--syn-font-mono)",
                      fontSize: 12,
                      color: "var(--syn-text)",
                      padding: "6px 10px",
                      background: "var(--syn-bg)",
                      border:
                        "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      wordBreak: "break-all",
                      userSelect: "all",
                    }}
                  >
                    {generatedToken}
                  </span>
                  <button
                    data-testid="clip-copy-generated-token-btn"
                    onClick={handleCopyGeneratedToken}
                    style={{
                      padding: "6px 12px",
                      border:
                        "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      background: copiedGenToken ? "var(--syn-green)" : "transparent",
                      color: copiedGenToken ? "#fff" : "var(--syn-green)",
                      fontSize: 11,
                      cursor: "pointer",
                      flexShrink: 0,
                      transition: "background 0.15s, color 0.15s",
                    }}
                  >
                    {copiedGenToken ? t("settings.webClipper.copied") : t("common.copy")}
                  </button>
                </div>
                <button
                  data-testid="clip-dismiss-generated-token-btn"
                  onClick={handleDismissGeneratedToken}
                  style={{
                    marginTop: 8,
                    padding: "4px 10px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 4,
                    background: "transparent",
                    color: "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {t("settings.webClipper.dismissToken")}
                </button>
              </div>
            )}
          </div>

          {/* Allowed origins */}
          <div style={WC_PANEL}>
            <Field label={t("settings.webClipper.originsLabel")}>
              <p
                style={{
                  margin: "0 0 6px",
                  fontSize: 11,
                  color: "var(--syn-text-muted)",
                  lineHeight: 1.5,
                }}
              >
                {t("settings.webClipper.originsHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="clip-origins-input"
                  value={originsInput}
                  onChange={(e) => setOriginsInput(e.target.value)}
                  placeholder={t("settings.webClipper.originsPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="clip-origins-save"
                  onClick={() => {
                    void handleSaveOrigins();
                  }}
                  disabled={originsBusy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: originsBusy ? 0.4 : 1,
                    cursor: originsBusy ? "not-allowed" : "pointer",
                    flexShrink: 0,
                  }}
                >
                  {originsBusy ? "…" : t("settings.webClipper.originsSave")}
                </button>
              </div>
              {allowedOrigins.length > 0 && (
                <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {allowedOrigins.map((o) => (
                    <span
                      key={o}
                      data-testid={`clip-origin-tag-${o}`}
                      style={{
                        padding: "2px 8px",
                        borderRadius: 4,
                        background: "var(--syn-surface-hover)",
                        color: "var(--syn-text-muted)",
                        fontSize: 11,
                        fontFamily: "var(--syn-font-mono)",
                      }}
                    >
                      {o}
                    </span>
                  ))}
                </div>
              )}
            </Field>
          </div>

          {/* Clip endpoint URL */}
          <div style={WC_PANEL}>
            <p
              style={{
                margin: "0 0 6px",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
              }}
            >
              {t("settings.webClipper.extensionUrlLabel")}
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                data-testid="clip-endpoint-url"
                style={{
                  flex: 1,
                  fontFamily: "var(--syn-font-mono)",
                  fontSize: 12,
                  color: "var(--syn-accent)",
                  padding: "5px 8px",
                  background: "var(--syn-accent-soft)",
                  border:
                    "1px solid color-mix(in srgb, var(--syn-accent) 30%, var(--syn-mix-base) 70%)",
                  borderRadius: 4,
                  wordBreak: "break-all",
                }}
              >
                {clipUrl}
              </span>
              <button
                data-testid="clip-endpoint-url-copy"
                onClick={handleCopyClipUrl}
                style={{
                  padding: "5px 10px",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 4,
                  background: copiedUrl
                    ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                    : "transparent",
                  color: copiedUrl ? "var(--syn-green)" : "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  flexShrink: 0,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {copiedUrl ? t("settings.webClipper.copied") : t("common.copy")}
              </button>
            </div>
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 11,
                color: "var(--syn-text-dim)",
                lineHeight: 1.5,
              }}
            >
              {t("settings.webClipper.extensionHint")}
            </p>
          </div>

          {maxBodyBytes > 0 && (
            <div>
              <EmbedRow
                label={t("settings.webClipper.maxBodyLabel")}
                value={t("settings.webClipper.maxBodyBytes", { bytes: maxBodyBytes })}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
