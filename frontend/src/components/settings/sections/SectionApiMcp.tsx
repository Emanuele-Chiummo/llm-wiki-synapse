/**
 * SectionApiMcp.tsx — HTTP API + MCP server (ADR-0027, ADR-0032, ADR-0033, ADR-0043).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * I3: single fetch on mount; no Zustand store; toggle = one fetch/PUT, local state only.
 * I6: nothing hardcoded — server_name, entry_point_command from API payload.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, EmbedRow, BTN_PRIMARY } from "../ui";
import {
  fetchMcpInfo,
  setRemoteMcpEnabled,
  setMcpRemoteWrite,
  setMcpAuth,
  type McpInfoResponse,
  type McpRemoteStateResponse,
  type McpRemoteWriteStateResponse,
  type McpAuthResponse,
} from "../../../api/providerClient";

// ─── helpers ──────────────────────────────────────────────────────────────────

function buildClaudeDesktopSnippet(mcpInfo: McpInfoResponse): string {
  const tokens = mcpInfo.entry_point_command.trim().split(/\s+/);
  const command = tokens[0] ?? "";
  const args = tokens.slice(1);
  const payload = {
    mcpServers: {
      [mcpInfo.server_name]: { command, args },
    },
  };
  return JSON.stringify(payload, null, 2);
}

function buildRemoteMcpSnippet(remoteUrl: string): string {
  const payload = {
    mcpServers: {
      synapse_remote: { type: "http", url: remoteUrl },
    },
  };
  return JSON.stringify(payload, null, 2);
}

// ─── SectionApiMcp ────────────────────────────────────────────────────────────

export function SectionApiMcp() {
  const { t } = useTranslation();
  const [info, setInfo] = useState<McpInfoResponse | null>(null);
  const [err, setErr] = useState(false);
  const [copied, setCopied] = useState(false);

  const [remoteEnabled, setRemoteEnabled] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [tokenSource, setTokenSource] = useState<"db" | "env" | "none">("none");
  const [allowWithoutToken, setAllowWithoutToken] = useState(false);
  const [mountPath, setMountPath] = useState("/mcp/server");
  const [remoteWrite, setRemoteWrite] = useState(false);
  const [toggleBusy, setToggleBusy] = useState(false);
  const [writeBusy, setWriteBusy] = useState(false);
  const [copiedRemote, setCopiedRemote] = useState(false);
  const [copiedRemoteSnippet, setCopiedRemoteSnippet] = useState(false);

  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [copiedGenToken, setCopiedGenToken] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchMcpInfo(ac.signal)
      .then((data) => {
        setInfo(data);
        setErr(false);
        setRemoteEnabled(data.remote_enabled);
        setTokenConfigured(data.token_configured);
        setTokenSource(data.token_source);
        setAllowWithoutToken(data.allow_without_token);
        setMountPath(data.mount_path);
        setRemoteWrite(data.remote_write_enabled);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    return () => {
      ac.abort();
    };
  }, []);

  const applyAuthResponse = (resp: McpAuthResponse) => {
    setTokenConfigured(resp.token_configured);
    setTokenSource(resp.token_source);
    setAllowWithoutToken(resp.allow_without_token);
    setRemoteEnabled(resp.remote_enabled);
    setMountPath(resp.mount_path);
  };

  const handleCopy = () => {
    if (!info) return;
    navigator.clipboard
      .writeText(buildClaudeDesktopSnippet(info))
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const handleRemoteToggle = async () => {
    const canEnable = tokenConfigured || allowWithoutToken;
    if (toggleBusy || !canEnable) return;
    const next = !remoteEnabled;
    setToggleBusy(true);
    try {
      const resp: McpRemoteStateResponse = await setRemoteMcpEnabled(next);
      setRemoteEnabled(resp.remote_enabled);
      setTokenConfigured(resp.token_configured);
      setMountPath(resp.mount_path);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setToggleBusy(false);
    }
  };

  const handleWriteToggle = async () => {
    if (writeBusy || !canEnableRemote) return;
    const next = !remoteWrite;
    setWriteBusy(true);
    try {
      const resp: McpRemoteWriteStateResponse = await setMcpRemoteWrite(next);
      if (!resp.clamped) {
        setRemoteWrite(resp.remote_write_enabled);
      }
      // If clamped, server refused to enable — leave toggle off (already false); no flip.
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setWriteBusy(false);
    }
  };

  const handleGenerateToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null);
    try {
      const resp = await setMcpAuth({ rotate_token: true });
      applyAuthResponse(resp);
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
      const resp = await setMcpAuth({ clear_token: true });
      applyAuthResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  const handleAllowWithoutTokenToggle = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    try {
      const resp = await setMcpAuth({ allow_without_token: !allowWithoutToken });
      applyAuthResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
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

  const remoteUrl = `${window.location.origin}${mountPath}`;

  const handleCopyRemoteUrl = () => {
    navigator.clipboard
      .writeText(remoteUrl)
      .then(() => {
        setCopiedRemote(true);
        setTimeout(() => setCopiedRemote(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const handleCopyRemoteSnippet = () => {
    navigator.clipboard
      .writeText(buildRemoteMcpSnippet(remoteUrl))
      .then(() => {
        setCopiedRemoteSnippet(true);
        setTimeout(() => setCopiedRemoteSnippet(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const canEnableRemote = tokenConfigured || allowWithoutToken;

  const tokenPostureKey = !tokenConfigured
    ? "settings.apiMcp.access.postureNone"
    : tokenSource === "db"
      ? "settings.apiMcp.access.postureDb"
      : "settings.apiMcp.access.postureEnv";

  return (
    <div>
      <SectionHeader title={t("settings.nav.apiMcp")} desc={t("settings.apiMcp.desc")} />

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>
          {t("settings.apiMcp.error")}
        </p>
      ) : info === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>
          {t("settings.apiMcp.loading")}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {/* ── Access sub-block — ADR-0033 §2.6 ── */}
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
              {t("settings.apiMcp.access.title")}
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
                  data-testid="mcp-token-posture"
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
                  data-testid="mcp-generate-token-btn"
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
                      ? t("settings.apiMcp.access.rotateToken")
                      : t("settings.apiMcp.access.generateToken")}
                </button>
                {tokenConfigured && (
                  <button
                    data-testid="mcp-clear-token-btn"
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
                    {t("settings.apiMcp.access.clearToken")}
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
                  {t("settings.apiMcp.access.revealWarning")}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    data-testid="mcp-generated-token"
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
                    data-testid="mcp-copy-generated-token-btn"
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
                    {copiedGenToken ? t("settings.apiMcp.copied") : t("common.copy")}
                  </button>
                </div>
                <button
                  data-testid="mcp-dismiss-generated-token-btn"
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
                  {t("settings.apiMcp.access.dismissToken")}
                </button>
              </div>
            )}

            {/* Allow without token switch */}
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${allowWithoutToken ? "color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)" : "var(--syn-border)"}`,
                borderRadius: 8,
                opacity: authBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              <label
                style={{
                  display: "flex",
                  alignItems: "flex-start",
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
                    marginTop: 1,
                  }}
                >
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={t("settings.apiMcp.access.allowWithoutTokenLabel")}
                    data-testid="mcp-allow-without-token"
                    checked={allowWithoutToken}
                    disabled={authBusy}
                    onChange={() => {
                      void handleAllowWithoutTokenToggle();
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
                      background: allowWithoutToken ? "var(--syn-amber)" : "var(--syn-border)",
                      border: `1px solid ${allowWithoutToken ? "var(--syn-amber)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: allowWithoutToken ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: allowWithoutToken ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: allowWithoutToken ? "var(--syn-amber)" : "var(--syn-text-muted)",
                    }}
                  >
                    {t("settings.apiMcp.access.allowWithoutTokenLabel")}
                  </span>
                  <p
                    data-testid="mcp-allow-without-token-caveat"
                    style={{
                      margin: "4px 0 0",
                      fontSize: 11,
                      color: "var(--syn-amber)",
                      lineHeight: 1.5,
                    }}
                  >
                    {t("settings.apiMcp.access.allowWithoutTokenCaveat")}
                  </p>
                </div>
              </label>
            </div>
          </div>

          {/* ── Remote sub-section — ADR-0032 §2.7 ── */}
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
              {t("settings.apiMcp.remote.title")}
            </p>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${remoteEnabled ? "var(--syn-accent)" : "var(--syn-border)"}`,
                borderRadius: 8,
                marginBottom: 10,
                opacity: toggleBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  cursor: canEnableRemote ? "pointer" : "not-allowed",
                  userSelect: "none",
                  flex: 1,
                }}
                title={canEnableRemote ? undefined : t("settings.apiMcp.remote.noTokenNote")}
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
                    aria-label={t("settings.apiMcp.remote.enabledLabel")}
                    data-testid="mcp-remote-toggle"
                    checked={remoteEnabled}
                    disabled={!canEnableRemote || toggleBusy}
                    onChange={() => {
                      void handleRemoteToggle();
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
                      background: remoteEnabled ? "var(--syn-accent)" : "var(--syn-border)",
                      border: `1px solid ${remoteEnabled ? "var(--syn-accent)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: remoteEnabled ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: remoteEnabled ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: remoteEnabled ? "var(--syn-text)" : "var(--syn-text-muted)",
                    }}
                  >
                    {t("settings.apiMcp.remote.enabledLabel")}
                  </span>
                  {!canEnableRemote && (
                    <p
                      style={{
                        margin: "3px 0 0",
                        fontSize: 11,
                        color: "var(--syn-amber)",
                        lineHeight: 1.5,
                      }}
                    >
                      {t("settings.apiMcp.remote.noTokenNote")}
                    </p>
                  )}
                </div>
              </label>

              {remoteEnabled && (
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    cursor: canEnableRemote ? "pointer" : "not-allowed",
                    userSelect: "none",
                    flexShrink: 0,
                  }}
                  title={canEnableRemote ? undefined : t("settings.apiMcp.remote.noTokenNote")}
                >
                  <span
                    style={{ position: "relative", display: "inline-block", width: 36, height: 20 }}
                  >
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label={t("settings.apiMcp.remote.writeToggleLabel")}
                      data-testid="mcp-remote-write-toggle"
                      checked={remoteWrite}
                      disabled={!canEnableRemote || writeBusy}
                      onChange={() => {
                        void handleWriteToggle();
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
                        background: remoteWrite ? "var(--syn-green)" : "var(--syn-border)",
                        border: `1px solid ${remoteWrite ? "var(--syn-green)" : "var(--syn-border-subtle)"}`,
                        transition: "background 0.15s, border-color 0.15s",
                      }}
                    />
                    <span
                      aria-hidden="true"
                      style={{
                        position: "absolute",
                        top: 3,
                        left: remoteWrite ? 19 : 3,
                        width: 14,
                        height: 14,
                        borderRadius: "50%",
                        background: remoteWrite ? "#fff" : "var(--syn-text-dim)",
                        transition: "left 0.15s, background 0.15s",
                      }}
                    />
                  </span>
                  <div>
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: remoteWrite ? "var(--syn-green)" : "var(--syn-text-muted)",
                      }}
                    >
                      {remoteWrite
                        ? t("settings.apiMcp.remote.readWriteBadge")
                        : t("settings.apiMcp.remote.readOnlyBadge")}
                    </span>
                    {!remoteWrite && (
                      <p
                        style={{
                          margin: "2px 0 0",
                          fontSize: 10,
                          color: "var(--syn-text-dim)",
                          lineHeight: 1.4,
                        }}
                      >
                        {t("settings.apiMcp.remote.writeToggleNote")}
                      </p>
                    )}
                  </div>
                </label>
              )}
            </div>

            {remoteEnabled && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
                <div>
                  <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.remote.urlLabel")}
                  </p>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      data-testid="mcp-remote-url"
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
                      {remoteUrl}
                    </span>
                    <button
                      data-testid="mcp-remote-url-copy"
                      onClick={handleCopyRemoteUrl}
                      style={{
                        padding: "5px 10px",
                        border: "1px solid var(--syn-border)",
                        borderRadius: 4,
                        background: copiedRemote
                          ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                          : "transparent",
                        color: copiedRemote ? "var(--syn-green)" : "var(--syn-text-muted)",
                        fontSize: 11,
                        cursor: "pointer",
                        flexShrink: 0,
                        transition: "background 0.15s, color 0.15s",
                      }}
                    >
                      {copiedRemote ? t("settings.apiMcp.copied") : t("common.copy")}
                    </button>
                  </div>
                </div>

                <div>
                  <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.remote.snippetLabel")}
                  </p>
                  <div
                    data-testid="mcp-remote-snippet"
                    style={{
                      fontFamily: "var(--syn-font-mono)",
                      fontSize: 11,
                      background: "var(--syn-surface-sunken)",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                      padding: "10px 12px",
                      color: "var(--syn-text-muted)",
                      whiteSpace: "pre",
                      overflowX: "auto",
                      marginBottom: 6,
                    }}
                  >
                    {buildRemoteMcpSnippet(remoteUrl)}
                  </div>
                  <button
                    data-testid="mcp-remote-snippet-copy"
                    onClick={handleCopyRemoteSnippet}
                    style={{
                      padding: "5px 12px",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                      background: copiedRemoteSnippet
                        ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                        : "transparent",
                      color: copiedRemoteSnippet ? "var(--syn-green)" : "var(--syn-text-muted)",
                      fontSize: 11,
                      cursor: "pointer",
                      transition: "background 0.15s, color 0.15s",
                    }}
                  >
                    {copiedRemoteSnippet
                      ? t("settings.apiMcp.copied")
                      : t("settings.apiMcp.remote.copySnippet")}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── Connection sub-section (stdio — read-only) ── */}
          <div>
            <p
              style={{
                margin: "0 0 10px",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              {t("settings.apiMcp.connectionTitle")}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <EmbedRow label={t("settings.apiMcp.transportLabel")} value={info.transport} mono />
              <EmbedRow
                label={t("settings.apiMcp.entryPointLabel")}
                value={info.entry_point_command}
                mono
              />
            </div>

            <div style={{ marginTop: 14 }}>
              <div
                data-testid="mcp-snippet"
                style={{
                  fontFamily: "var(--syn-font-mono)",
                  fontSize: 11,
                  background: "var(--syn-surface-sunken)",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 6,
                  padding: "10px 12px",
                  color: "var(--syn-text-muted)",
                  whiteSpace: "pre",
                  overflowX: "auto",
                  marginBottom: 8,
                }}
              >
                {buildClaudeDesktopSnippet(info)}
              </div>
              <button
                onClick={handleCopy}
                data-testid="mcp-copy-btn"
                style={{
                  padding: "5px 12px",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 6,
                  background: copied
                    ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                    : "transparent",
                  color: copied ? "var(--syn-green)" : "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {copied ? t("settings.apiMcp.copied") : t("settings.apiMcp.copySnippet")}
              </button>
            </div>
          </div>

          {/* ── Tools sub-section ── */}
          <div>
            <p
              style={{
                margin: "0 0 10px",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              {t("settings.apiMcp.toolsTitle")}
              <span
                style={{
                  marginLeft: 8,
                  fontWeight: 400,
                  textTransform: "none",
                  color: "var(--syn-text-dim)",
                }}
              >
                ({info.tool_count})
              </span>
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {info.tools.map((tool) => {
                const paramCount = Object.keys(tool.input_schema.properties ?? {}).length;
                const firstSentence = (tool.description ?? "").split(/[.!?]/)[0] ?? "";
                const truncated =
                  firstSentence.length > 80 ? firstSentence.slice(0, 79) + "…" : firstSentence;
                return (
                  <div
                    key={tool.name}
                    data-testid={`mcp-tool-row-${tool.name}`}
                    className="settings-mcp-tool-row"
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr auto",
                      gap: 10,
                      alignItems: "center",
                      padding: "8px 12px",
                      background: "var(--syn-bg-soft)",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                    }}
                  >
                    <span
                      data-testid={`mcp-tool-name-${tool.name}`}
                      style={{
                        fontFamily: "var(--syn-font-mono)",
                        fontSize: 12,
                        color: "var(--syn-text)",
                        fontWeight: 600,
                      }}
                    >
                      {tool.name}
                    </span>
                    <span
                      style={{
                        fontSize: 12,
                        color: "var(--syn-text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {truncated}
                    </span>
                    <span
                      data-testid={`mcp-tool-params-${tool.name}`}
                      data-param-count={paramCount}
                      style={{ fontSize: 11, color: "var(--syn-text-dim)", whiteSpace: "nowrap" }}
                    >
                      {t("settings.apiMcp.paramCount", { count: paramCount })}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
