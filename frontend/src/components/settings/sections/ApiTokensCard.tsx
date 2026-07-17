/**
 * ApiTokensCard.tsx — scoped, revocable API token management (PF-AUTH-1, 1.9.4 W4).
 *
 * Extends the client-side bootstrap-token editor above it (SectionSecurity) with
 * server-side, DB-backed tokens: list (no secret), create (plaintext shown ONCE in a
 * reveal dialog), revoke. Mirrors the MCP "generated token" reveal UX (SectionApiMcp).
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../ui/Button";
import {
  fetchApiTokens,
  createApiToken,
  revokeApiToken,
} from "../../../api/apiTokensClient";
import type { ApiTokenListItem } from "../../../api/types";

const CARD: CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "16px",
  marginBottom: 16,
};

const LABEL_STYLE: CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "var(--syn-text-muted)",
  display: "block",
  marginBottom: 4,
};

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function ApiTokensCard() {
  const { t } = useTranslation();

  const [tokens, setTokens] = useState<ApiTokenListItem[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [label, setLabel] = useState("");
  const [vaultId, setVaultId] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(false);
  const [revokeErrorId, setRevokeErrorId] = useState<string | null>(null);
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const loadTokens = useCallback(async (signal?: AbortSignal) => {
    try {
      const resp = await fetchApiTokens(signal);
      setTokens(resp.tokens);
      setLoadError(false);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setLoadError(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadTokens(controller.signal);
    return () => controller.abort();
  }, [loadTokens]);

  const handleCreate = useCallback(async () => {
    const trimmedLabel = label.trim();
    if (!trimmedLabel || creating) return;
    setCreating(true);
    setCreateError(false);
    try {
      const resp = await createApiToken({
        label: trimmedLabel,
        vault_id: vaultId.trim() || null,
        read_only: readOnly,
      });
      setRevealedToken(resp.token);
      setLabel("");
      setVaultId("");
      setReadOnly(false);
      await loadTokens();
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setCreateError(true);
    } finally {
      setCreating(false);
    }
  }, [label, vaultId, readOnly, creating, loadTokens]);

  const handleRevoke = useCallback(
    async (id: string) => {
      if (!window.confirm(t("settings.security.apiTokens.revokeConfirm"))) return;
      setRevokeErrorId(null);
      try {
        await revokeApiToken(id);
        await loadTokens();
      } catch (e: unknown) {
        if (e instanceof Error && e.name === "AbortError") return;
        setRevokeErrorId(id);
      }
    },
    [loadTokens, t],
  );

  const handleCopyRevealed = () => {
    if (!revealedToken) return;
    navigator.clipboard
      .writeText(revealedToken)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  const handleDismissRevealed = () => {
    setRevealedToken(null);
    setCopied(false);
  };

  return (
    <div style={CARD} data-testid="api-tokens-card">
      <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 4, color: "var(--syn-text)" }}>
        {t("settings.security.apiTokens.title")}
      </h3>
      <p style={{ fontSize: 12, color: "var(--syn-text-dim)", marginBottom: 12, lineHeight: 1.6 }}>
        {t("settings.security.apiTokens.desc")}
      </p>

      {/* One-time reveal dialog */}
      {revealedToken && (
        <div
          data-testid="api-token-reveal"
          style={{
            marginBottom: 14,
            padding: "10px 12px",
            background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-surface) 92%)",
            border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
            borderRadius: 8,
          }}
        >
          <p style={{ margin: "0 0 6px", fontSize: 12, fontWeight: 700, color: "var(--syn-green)" }}>
            {t("settings.security.apiTokens.revealTitle")}
          </p>
          <p style={{ margin: "0 0 8px", fontSize: 11, color: "var(--syn-text-dim)" }}>
            {t("settings.security.apiTokens.revealWarning")}
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              data-testid="api-token-reveal-value"
              style={{
                flex: 1,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 12,
                color: "var(--syn-text)",
                padding: "6px 10px",
                background: "var(--syn-bg)",
                border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                borderRadius: 4,
                wordBreak: "break-all",
                userSelect: "all",
              }}
            >
              {revealedToken}
            </span>
            <button
              data-testid="api-token-reveal-copy-btn"
              onClick={handleCopyRevealed}
              style={{
                padding: "6px 12px",
                border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                borderRadius: 4,
                background: copied ? "var(--syn-green)" : "transparent",
                color: copied ? "#fff" : "var(--syn-green)",
                fontSize: 11,
                cursor: "pointer",
                flexShrink: 0,
                transition: "background 0.15s, color 0.15s",
              }}
            >
              {copied ? t("settings.apiMcp.copied") : t("common.copy")}
            </button>
          </div>
          <button
            data-testid="api-token-reveal-dismiss-btn"
            onClick={handleDismissRevealed}
            style={{
              marginTop: 8,
              background: "none",
              border: "none",
              color: "var(--syn-text-dim)",
              fontSize: 11,
              cursor: "pointer",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {t("settings.security.apiTokens.dismiss")}
          </button>
        </div>
      )}

      {/* Create form */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
        <div style={{ flex: "1 1 160px" }}>
          <label htmlFor="api-token-label" style={LABEL_STYLE}>
            {t("settings.security.apiTokens.labelLabel")}
          </label>
          <input
            id="api-token-label"
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t("settings.security.apiTokens.labelPlaceholder")}
            className="syn-input"
            style={{ fontSize: 12 }}
          />
        </div>
        <div style={{ flex: "1 1 160px" }}>
          <label htmlFor="api-token-vault-scope" style={LABEL_STYLE}>
            {t("settings.security.apiTokens.vaultScopeLabel")}
          </label>
          <input
            id="api-token-vault-scope"
            type="text"
            value={vaultId}
            onChange={(e) => setVaultId(e.target.value)}
            placeholder={t("settings.security.apiTokens.vaultScopePlaceholder")}
            className="syn-input"
            style={{ fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
          />
        </div>
      </div>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          color: "var(--syn-text)",
          marginBottom: 12,
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={readOnly}
          onChange={(e) => setReadOnly(e.target.checked)}
          data-testid="api-token-readonly-checkbox"
        />
        {t("settings.security.apiTokens.readOnlyLabel")}
      </label>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <Button
          variant="accent-ghost"
          onClick={handleCreate}
          disabled={label.trim().length === 0 || creating}
          data-testid="api-token-create-btn"
        >
          {t("settings.security.apiTokens.create")}
        </Button>
        {createError && (
          <span style={{ fontSize: 12, color: "var(--syn-red, #dc2626)" }}>
            {t("settings.security.apiTokens.createError")}
          </span>
        )}
      </div>

      {/* Active token list */}
      {loadError && (
        <p style={{ fontSize: 12, color: "var(--syn-red, #dc2626)" }}>
          {t("settings.security.apiTokens.loadError")}
        </p>
      )}
      {!loadError && tokens.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
          {t("settings.security.apiTokens.empty")}
        </p>
      )}
      {!loadError && tokens.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }} data-testid="api-tokens-table">
            <thead>
              <tr style={{ textAlign: "left", color: "var(--syn-text-muted)" }}>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnLabel")}
                </th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnScope")}
                </th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnAccess")}
                </th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnLastUsed")}
                </th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnCreated")}
                </th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>
                  {t("settings.security.apiTokens.columnActions")}
                </th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((tok) => (
                <tr key={tok.id} style={{ borderTop: "1px solid var(--syn-border)" }}>
                  <td style={{ padding: "6px 8px", color: "var(--syn-text)" }}>{tok.label}</td>
                  <td style={{ padding: "6px 8px", color: "var(--syn-text-dim)" }}>
                    {tok.vault_id ?? t("settings.security.apiTokens.scopeGlobal")}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--syn-text-dim)" }}>
                    {tok.read_only
                      ? t("settings.security.apiTokens.accessReadOnly")
                      : t("settings.security.apiTokens.accessReadWrite")}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--syn-text-dim)" }}>
                    {formatDate(tok.last_used_at) ?? t("settings.security.apiTokens.neverUsed")}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--syn-text-dim)" }}>
                    {formatDate(tok.created_at)}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => handleRevoke(tok.id)}
                      data-testid={`api-token-revoke-btn-${tok.id}`}
                    >
                      {t("settings.security.apiTokens.revoke")}
                    </Button>
                    {revokeErrorId === tok.id && (
                      <div style={{ fontSize: 11, color: "var(--syn-red, #dc2626)", marginTop: 2 }}>
                        {t("settings.security.apiTokens.revokeError")}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
