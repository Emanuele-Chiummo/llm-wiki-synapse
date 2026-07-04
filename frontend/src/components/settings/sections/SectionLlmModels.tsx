/**
 * SectionLlmModels.tsx — provider CRUD (F17).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * I6: no hardcoded model IDs.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import { SectionHeader, Field, INPUT_STYLE, BTN_PRIMARY, BTN_SECONDARY } from "../ui";
import {
  useProviderStore,
  selectProviderList,
  selectProviderLoading,
  selectProviderError,
  selectFetchProviderList,
  selectAddProvider,
  selectDeleteProvider,
} from "../../../store/providerStore";
import { useGraphStore, selectVaultId } from "../../../store/graphStore";
import type { CreateProviderConfigBody } from "../../../api/types";

export function SectionLlmModels() {
  const { t } = useTranslation();
  const providerList = useProviderStore(useShallow(selectProviderList));
  const providerLoading = useProviderStore(selectProviderLoading);
  const providerError = useProviderStore(selectProviderError);
  const fetchProviders = useProviderStore(selectFetchProviderList);
  const addProvider = useProviderStore(selectAddProvider);
  const deleteProvider = useProviderStore(selectDeleteProvider);
  const vaultId = useGraphStore(selectVaultId);

  const [showForm, setShowForm] = useState(false);
  const [formType, setFormType] = useState<"local" | "api" | "cli">("api");
  const [formModelId, setFormModelId] = useState("");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formScope, setFormScope] = useState<"global" | "vault">("global");
  const [formLoading, setFormLoading] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (providerList.length === 0 && !providerLoading) {
      void fetchProviders();
    }
  }, [providerList.length, providerLoading, fetchProviders]);

  const handleAdd = async () => {
    setFormLoading(true);
    const body: CreateProviderConfigBody = {
      scope: formScope,
      vault_id: formScope === "vault" ? vaultId : null,
      provider_type: formType,
      model_id: formModelId.trim() || null,
      base_url: formBaseUrl.trim() || null,
    };
    await addProvider(body, vaultId ?? "");
    setFormLoading(false);
    setShowForm(false);
    setFormModelId("");
    setFormBaseUrl("");
    setSuccessMsg(t("settings.llmModels.added"));
    setTimeout(() => setSuccessMsg(null), 2500);
  };

  const handleDelete = async (id: string) => {
    const isLast = providerList.length === 1;
    const confirmMsg = isLast
      ? `${t("settings.llmModels.lastProviderWarning")}\n\n${t("settings.llmModels.confirmDelete")}`
      : t("settings.llmModels.confirmDelete");
    if (!window.confirm(confirmMsg)) return;
    await deleteProvider(id, vaultId ?? "");
    setSuccessMsg(t("settings.llmModels.deleted"));
    setTimeout(() => setSuccessMsg(null), 2500);
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.llmModels")} desc={t("settings.llmModels.desc")} />

      {successMsg && (
        <div style={{ marginBottom: 12, padding: "6px 12px", background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)", border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)", borderRadius: 6, fontSize: 12, color: "var(--syn-green)" }}>
          {successMsg}
        </div>
      )}
      {providerError && (
        <div style={{ marginBottom: 12, padding: "6px 12px", background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)", border: "1px solid color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)", borderRadius: 6, fontSize: 12, color: "var(--syn-red)" }}>
          {providerError}
        </div>
      )}

      {providerLoading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("common.loading")}</p>
      )}

      {!providerLoading && providerList.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("provider.noProviders")}</p>
      )}

      <div style={{ marginBottom: 16 }}>
        {providerList.map((item) => (
          <div
            key={item.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 12px",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              marginBottom: 6,
              background: "var(--syn-surface)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text)" }}>
                  {t(`provider.type.${item.provider_type}` as string) || item.provider_type}
                </span>
                <span style={{ padding: "1px 6px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-muted)", fontSize: 10 }}>
                  {t(`provider.scope.${item.scope}`)}
                </span>
                {item.is_fallback && (
                  <span style={{ padding: "1px 6px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-dim)", fontSize: 10 }}>
                    {t("settings.llmModels.fallback")}
                  </span>
                )}
              </div>
              {item.model_id && (
                <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "monospace" }}>
                  {item.model_id}
                </p>
              )}
              {item.base_url && (
                <p style={{ margin: "2px 0 0", fontSize: 10, color: "var(--syn-text-dim)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.base_url}
                </p>
              )}
            </div>
            <button
              onClick={() => void handleDelete(item.id)}
              title={t("settings.llmModels.delete")}
              style={{
                padding: "4px 8px",
                border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                borderRadius: 4,
                background: "transparent",
                color: "var(--syn-red)",
                fontSize: 11,
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              {t("settings.llmModels.delete")}
            </button>
          </div>
        ))}
      </div>

      {showForm ? (
        <div style={{ padding: 16, border: "1px solid var(--syn-border)", borderRadius: 8, background: "var(--syn-bg-soft)", marginBottom: 16 }}>
          <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text)" }}>
            {t("settings.llmModels.addProvider")}
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
            <Field label={t("settings.llmModels.providerType")} compact>
              <select value={formType} onChange={(e) => setFormType(e.target.value as typeof formType)} style={INPUT_STYLE}>
                <option value="api">API (Anthropic / OpenAI-compat)</option>
                <option value="local">Local (Ollama)</option>
                <option value="cli">CLI (claude-agent-sdk)</option>
              </select>
            </Field>
            <Field label={t("settings.llmModels.scope")} compact>
              <select value={formScope} onChange={(e) => setFormScope(e.target.value as typeof formScope)} style={INPUT_STYLE}>
                <option value="global">Global</option>
                <option value="vault">Vault</option>
              </select>
            </Field>
          </div>

          <Field label={t("settings.llmModels.modelId")} compact>
            <input
              type="text"
              value={formModelId}
              onChange={(e) => setFormModelId(e.target.value)}
              placeholder={
                formType === "local"
                  ? t("settings.llmModels.modelIdPlaceholderLocal")
                  : formType === "cli"
                  ? t("settings.llmModels.modelIdPlaceholderCli")
                  : t("settings.llmModels.modelIdPlaceholder")
              }
              style={INPUT_STYLE}
            />
          </Field>

          {(formType === "api" || formType === "local") && (
            <Field label={t("settings.llmModels.baseUrl")} compact>
              <input
                type="text"
                value={formBaseUrl}
                onChange={(e) => setFormBaseUrl(e.target.value)}
                placeholder={
                  formType === "local"
                    ? t("settings.llmModels.baseUrlPlaceholderLocal")
                    : t("settings.llmModels.baseUrlPlaceholder")
                }
                style={INPUT_STYLE}
              />
            </Field>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button
              onClick={() => void handleAdd()}
              disabled={formLoading || formModelId.trim() === ""}
              title={formModelId.trim() === "" ? t("settings.llmModels.modelIdRequired") : undefined}
              style={{
                ...BTN_PRIMARY,
                opacity: formLoading || formModelId.trim() === "" ? 0.4 : 1,
                cursor: formLoading || formModelId.trim() === "" ? "not-allowed" : "pointer",
              }}
            >
              {formLoading ? "…" : t("settings.llmModels.add")}
            </button>
            <button onClick={() => setShowForm(false)} style={BTN_SECONDARY}>
              {t("settings.llmModels.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)} style={BTN_PRIMARY}>
          + {t("settings.llmModels.addProvider")}
        </button>
      )}
    </div>
  );
}
