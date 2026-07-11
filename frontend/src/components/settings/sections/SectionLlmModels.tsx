/**
 * SectionLlmModels.tsx — vendor catalog provider UI (F17, v1.4).
 *
 * Implements a one-row-per-vendor catalog matching LLM Wiki's UX:
 *   - 15 vendors from GET /provider/vendors; one toggle = one active provider.
 *   - Expanded row: API key (instant-save debounced), model chips, context
 *     window (linked to settingsStore), reasoning segmented control, test buttons.
 *   - Scope selector (Global / Vault) at the top.
 *   - SectionCliAuth preserved below the vendor catalog.
 *
 * Invariants:
 *   I3: Zustand selectors + shallow equality; no per-token heavy work.
 *   I6: no hardcoded model IDs; all values from /provider/vendors response.
 *   F17: all inference routing goes through InferenceProvider layer.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import { SectionHeader, INPUT_STYLE, BTN_PRIMARY, BTN_SECONDARY } from "../ui";
import { SectionCliAuth } from "./SectionCliAuth";
import {
  useProviderStore,
  selectProviderList,
  selectProviderLoading,
  selectProviderError,
  selectActiveProvider,
  selectFetchProviderList,
  selectAddProvider,
  selectVendors,
  selectVendorsLoading,
  selectVendorsError,
  selectFetchVendorCatalog,
  selectUpdateProvider,
} from "../../../store/providerStore";
import {
  useSettingsStore,
  selectContextWindow,
  selectSetContextWindow,
  CONTEXT_WINDOW_OPTIONS,
  formatTokenCount,
} from "../../../store/settingsStore";
import { useGraphStore, selectVaultId } from "../../../store/graphStore";
import { testProviderConnection, testProviderFunction } from "../../../api/providerClient";
import type { ProviderConfigItem, VendorInfo } from "../../../api/types";

// ─── Types ────────────────────────────────────────────────────────────────────

interface TestState {
  running: boolean;
  ok: boolean | null;
  latency: number | null;
  detail: string | null;
}

const FRESH_TEST: TestState = { running: false, ok: null, latency: null, detail: null };

/** Reasoning effort options shown as segmented buttons. */
const REASONING_OPTIONS = ["auto", "off", "low", "medium", "high", "max"] as const;
type ReasoningOption = (typeof REASONING_OPTIONS)[number];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Find a config row for a given vendor.
 * Primary match: operation === vendor.id (set when we create through the catalog).
 * Fallback: provider_type + base_url match (for configs created before v1.4).
 */
function findVendorConfig(
  vendor: VendorInfo,
  configs: ProviderConfigItem[],
): ProviderConfigItem | null {
  const byOp = configs.find((c) => c.operation === vendor.id);
  if (byOp) return byOp;
  return (
    configs.find(
      (c) =>
        c.provider_type === vendor.provider_type &&
        (vendor.default_base_url === null
          ? c.base_url === null || c.base_url === ""
          : c.base_url === vendor.default_base_url),
    ) ?? null
  );
}

/** Whether this vendor is the currently active provider. */
function isVendorActive(vendor: VendorInfo, activeItem: ProviderConfigItem | null): boolean {
  if (!activeItem) return false;
  if (activeItem.operation === vendor.id) return true;
  if (activeItem.provider_type !== vendor.provider_type) return false;
  if (vendor.default_base_url === null) {
    return activeItem.base_url === null || activeItem.base_url === "";
  }
  return activeItem.base_url === vendor.default_base_url;
}

/** Provider-type badge colours. */
function typeColor(ptype: "api" | "local" | "cli"): string {
  if (ptype === "api") return "var(--syn-accent)";
  if (ptype === "local") return "var(--syn-green)";
  return "var(--syn-amber)";
}

// ─── VendorRow ────────────────────────────────────────────────────────────────

interface VendorRowProps {
  vendor: VendorInfo;
  vendorConfig: ProviderConfigItem | null;
  active: boolean;
  scope: "global" | "vault";
  vaultId: string | null;
}

function VendorRow({ vendor, vendorConfig, active, scope, vaultId }: VendorRowProps) {
  const { t } = useTranslation();
  const addProvider = useProviderStore(selectAddProvider);
  const updateProvider = useProviderStore(selectUpdateProvider);
  const contextWindow = useSettingsStore(selectContextWindow);
  const setContextWindow = useSettingsStore(selectSetContextWindow);

  const [expanded, setExpanded] = useState(false);

  // API key
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const [apiKeyMsg, setApiKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const apiKeyDebounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Model chip selection
  const currentModelId = vendorConfig?.model_id ?? null;
  const isCustomModel =
    currentModelId !== null &&
    !vendor.model_presets.includes(currentModelId);
  const [customModelInput, setCustomModelInput] = useState(isCustomModel ? (currentModelId ?? "") : "");
  const [showCustomModel, setShowCustomModel] = useState(isCustomModel);

  // Reasoning effort
  const currentReasoning = (vendorConfig?.reasoning_effort ?? "auto") as ReasoningOption | "custom";

  // Test states
  const [testConn, setTestConn] = useState<TestState>(FRESH_TEST);
  const [testFunc, setTestFunc] = useState<TestState>(FRESH_TEST);

  // ─── Activate vendor ─────────────────────────────────────────────────────

  const handleActivate = useCallback(async () => {
    if (active) return; // already active — no-op (radio button semantics)
    const firstModel = vendor.model_presets[0] ?? null;
    await addProvider(
      {
        scope,
        vault_id: scope === "vault" ? vaultId : null,
        provider_type: vendor.provider_type,
        model_id: currentModelId ?? firstModel,
        base_url: vendor.default_base_url,
        operation: vendor.id,
      },
      vaultId ?? "",
    );
    setExpanded(true);
  }, [active, scope, vaultId, vendor, currentModelId, addProvider]);

  // ─── API key ─────────────────────────────────────────────────────────────

  const saveApiKey = useCallback(
    async (value: string) => {
      if (!vendorConfig) {
        // Must activate first → create config with api_key
        setApiKeySaving(true);
        setApiKeyMsg(null);
        try {
          await addProvider(
            {
              scope,
              vault_id: scope === "vault" ? vaultId : null,
              provider_type: vendor.provider_type,
              model_id: vendor.model_presets[0] ?? null,
              base_url: vendor.default_base_url,
              operation: vendor.id,
              ...(value !== "" ? { api_key: value } : {}),
            },
            vaultId ?? "",
          );
          setApiKeyMsg({ ok: true, text: t("settings.llmModels.apiKeySaved") });
          setApiKeyInput("");
        } catch (err: unknown) {
          const msg = (err as Error).message ?? "";
          const hint = msg.includes("400") || msg.toLowerCase().includes("key storage")
            ? t("settings.llmModels.apiKeyNoStorage")
            : t("settings.llmModels.apiKeySaveError");
          setApiKeyMsg({ ok: false, text: hint });
        } finally {
          setApiKeySaving(false);
        }
        return;
      }
      setApiKeySaving(true);
      setApiKeyMsg(null);
      try {
        await updateProvider(vendorConfig.id, { api_key: value }, vaultId ?? "");
        setApiKeyMsg({ ok: true, text: t("settings.llmModels.apiKeySaved") });
        setApiKeyInput("");
      } catch (err: unknown) {
        const msg = (err as Error).message ?? "";
        const hint = msg.includes("400") || msg.toLowerCase().includes("key storage")
          ? t("settings.llmModels.apiKeyNoStorage")
          : t("settings.llmModels.apiKeySaveError");
        setApiKeyMsg({ ok: false, text: hint });
      } finally {
        setApiKeySaving(false);
      }
    },
    [vendorConfig, scope, vaultId, vendor, addProvider, updateProvider, t],
  );

  const handleApiKeyChange = (value: string) => {
    setApiKeyInput(value);
    setApiKeyMsg(null);
    if (apiKeyDebounce.current) clearTimeout(apiKeyDebounce.current);
    if (value !== "") {
      apiKeyDebounce.current = setTimeout(() => {
        void saveApiKey(value);
      }, 800);
    }
  };

  const handleApiKeyClear = () => {
    if (!vendorConfig) return;
    if (apiKeyDebounce.current) clearTimeout(apiKeyDebounce.current);
    void saveApiKey("");
    setApiKeyInput("");
  };

  // ─── Model selection ─────────────────────────────────────────────────────

  const handleModelSelect = useCallback(
    async (modelId: string | null) => {
      if (!vendorConfig) return;
      await updateProvider(vendorConfig.id, { model_id: modelId }, vaultId ?? "");
    },
    [vendorConfig, updateProvider, vaultId],
  );

  const handleChipClick = (preset: string) => {
    setShowCustomModel(false);
    setCustomModelInput("");
    void handleModelSelect(preset);
  };

  const handleCustomChipClick = () => {
    setShowCustomModel(true);
    if (!isCustomModel) setCustomModelInput("");
  };

  const handleCustomModelSave = () => {
    const trimmed = customModelInput.trim();
    if (trimmed) void handleModelSelect(trimmed);
  };

  // ─── Reasoning effort ────────────────────────────────────────────────────

  const handleReasoningSelect = useCallback(
    async (effort: string) => {
      if (!vendorConfig) return;
      await updateProvider(vendorConfig.id, { reasoning_effort: effort }, vaultId ?? "");
    },
    [vendorConfig, updateProvider, vaultId],
  );

  // ─── Provider tests ──────────────────────────────────────────────────────

  // Inline probe target for a not-yet-activated vendor: the backend requires a model, so pass
  // the vendor's first preset (or the typed custom model). Without it the probe 422s.
  const inlineProbeModel = currentModelId ?? vendor.model_presets[0] ?? undefined;

  const handleTestConnection = async () => {
    setTestConn({ running: true, ok: null, latency: null, detail: null });
    try {
      const req = vendorConfig
        ? { config_id: vendorConfig.id }
        : {
            provider_type: vendor.provider_type,
            base_url: vendor.default_base_url,
            ...(inlineProbeModel ? { model: inlineProbeModel } : {}),
          };
      const res = await testProviderConnection(req);
      setTestConn({ running: false, ok: res.ok, latency: res.latency_ms, detail: res.detail });
    } catch (err: unknown) {
      setTestConn({ running: false, ok: false, latency: null, detail: (err as Error).message });
    }
  };

  const handleTestFunction = async () => {
    setTestFunc({ running: true, ok: null, latency: null, detail: null });
    try {
      const req = vendorConfig
        ? { config_id: vendorConfig.id }
        : {
            provider_type: vendor.provider_type,
            base_url: vendor.default_base_url,
            ...(inlineProbeModel ? { model: inlineProbeModel } : {}),
          };
      const res = await testProviderFunction(req);
      setTestFunc({ running: false, ok: res.ok, latency: res.latency_ms, detail: res.detail });
    } catch (err: unknown) {
      setTestFunc({ running: false, ok: false, latency: null, detail: (err as Error).message });
    }
  };

  // ─── Render helpers ──────────────────────────────────────────────────────

  const typeBadge = (
    <span
      style={{
        padding: "1px 6px",
        borderRadius: 4,
        border: `1px solid ${typeColor(vendor.provider_type)}`,
        color: typeColor(vendor.provider_type),
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.03em",
      }}
    >
      {vendor.provider_type}
    </span>
  );

  const activeBadge = active ? (
    <span
      style={{
        padding: "1px 6px",
        borderRadius: 4,
        background: "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-mix-base) 88%)",
        border: "1px solid color-mix(in srgb, var(--syn-accent) 35%, var(--syn-mix-base) 65%)",
        color: "var(--syn-accent)",
        fontSize: 10,
        fontWeight: 700,
      }}
    >
      {t("provider.active")}
    </span>
  ) : null;

  const keyBadge = (
    <span
      style={{
        padding: "1px 6px",
        borderRadius: 4,
        background: vendorConfig?.api_key_configured
          ? "color-mix(in srgb, var(--syn-green) 10%, var(--syn-mix-base) 90%)"
          : "var(--syn-surface-hover)",
        border: vendorConfig?.api_key_configured
          ? "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)"
          : "1px solid var(--syn-border)",
        color: vendorConfig?.api_key_configured ? "var(--syn-green)" : "var(--syn-text-dim)",
        fontSize: 10,
      }}
    >
      {vendorConfig?.api_key_configured
        ? t("settings.llmModels.configured")
        : t("settings.llmModels.apiKeyNotSet")}
    </span>
  );

  const rowStyle: CSSProperties = {
    border: "1px solid var(--syn-border)",
    borderRadius: 10,
    marginBottom: 8,
    background: active ? "color-mix(in srgb, var(--syn-accent) 5%, var(--syn-surface) 95%)" : "var(--syn-surface)",
    overflow: "hidden",
  };

  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "12px 16px",
    cursor: "pointer",
    userSelect: "none",
  };

  // ─── Expanded content ────────────────────────────────────────────────────

  const expandedContent = expanded ? (
    <div
      style={{
        padding: "12px 14px 14px",
        borderTop: "1px solid var(--syn-border)",
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
    >
      {/* Claude Code CLI subscription auth (sk-ant-oat OAuth) — co-located inside its own
          vendor row (v1.4) instead of a standalone section; it's specific to this provider. */}
      {vendor.id === "claude-cli" && <SectionCliAuth embedded />}

      {/* Codex CLI auth note — the codex binary authenticates itself (codex login / OPENAI_API_KEY);
          Synapse stores no pasteable Codex token, so the row shows guidance inline (v1.4). */}
      {vendor.id === "codex-cli" && (
        <div
          data-testid="codex-auth-note"
          style={{
            padding: "10px 14px",
            background: "var(--syn-bg-soft)",
            border: "1px solid var(--syn-border)",
            borderRadius: 8,
            fontSize: 11,
            color: "var(--syn-text-muted)",
            lineHeight: 1.6,
          }}
        >
          <p style={{ margin: "0 0 4px", fontWeight: 600 }}>{t("settings.llmModels.codexAuthTitle")}</p>
          <p style={{ margin: 0, whiteSpace: "pre-line" }}>{t("settings.llmModels.codexAuthNote")}</p>
        </div>
      )}

      {/* API Key (only for vendors that need one) */}
      {vendor.needs_api_key && (
        <div>
          <label
            style={{
              display: "block",
              marginBottom: 5,
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
            }}
          >
            {t("settings.llmModels.apiKey")}
          </label>
          {vendorConfig?.api_key_masked && (
            <p style={{ margin: "0 0 5px", fontSize: 11, color: "var(--syn-text-dim)", fontFamily: "monospace" }}>
              {vendorConfig.api_key_masked}
            </p>
          )}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => handleApiKeyChange(e.target.value)}
              placeholder={
                vendorConfig?.api_key_configured
                  ? "••••••••••••"
                  : t("settings.llmModels.apiKeyPlaceholder")
              }
              autoComplete="off"
              style={{ ...INPUT_STYLE, flex: 1, minWidth: 180 }}
              data-testid={`api-key-input-${vendor.id}`}
            />
            {apiKeySaving && (
              <span style={{ fontSize: 11, color: "var(--syn-text-dim)", alignSelf: "center" }}>…</span>
            )}
            {vendorConfig?.api_key_configured && (
              <button
                onClick={handleApiKeyClear}
                disabled={apiKeySaving}
                style={{
                  ...BTN_SECONDARY,
                  fontSize: 11,
                  padding: "4px 10px",
                  opacity: apiKeySaving ? 0.4 : 1,
                }}
                data-testid={`api-key-clear-${vendor.id}`}
              >
                {t("settings.llmModels.apiKeyClearBtn")}
              </button>
            )}
          </div>
          {apiKeyMsg && (
            <p
              style={{
                margin: "4px 0 0",
                fontSize: 11,
                color: apiKeyMsg.ok ? "var(--syn-green)" : "var(--syn-red)",
              }}
            >
              {apiKeyMsg.text}
            </p>
          )}
        </div>
      )}

      {/* Model selection */}
      {vendor.model_presets.length > 0 && (
        <div>
          <label
            style={{
              display: "block",
              marginBottom: 6,
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
            }}
          >
            {t("settings.llmModels.modelLabel")}
          </label>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {vendor.model_presets.map((preset) => {
              const sel = !showCustomModel && currentModelId === preset;
              return (
                <button
                  key={preset}
                  onClick={() => handleChipClick(preset)}
                  data-testid={`model-chip-${vendor.id}-${preset}`}
                  style={{
                    padding: "3px 9px",
                    border: sel
                      ? "1px solid var(--syn-accent)"
                      : "1px solid var(--syn-border)",
                    borderRadius: 12,
                    background: sel ? "var(--syn-accent-soft)" : "transparent",
                    color: sel ? "var(--syn-accent)" : "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                    fontFamily: "monospace",
                    whiteSpace: "nowrap",
                  }}
                >
                  {preset}
                </button>
              );
            })}
            <button
              onClick={handleCustomChipClick}
              data-testid={`model-chip-${vendor.id}-custom`}
              style={{
                padding: "3px 9px",
                border: showCustomModel
                  ? "1px solid var(--syn-accent)"
                  : "1px solid var(--syn-border)",
                borderRadius: 12,
                background: showCustomModel ? "var(--syn-accent-soft)" : "transparent",
                color: showCustomModel ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              {t("settings.llmModels.modelCustom")}
            </button>
          </div>
          {showCustomModel && (
            <div style={{ display: "flex", gap: 6, marginTop: 7 }}>
              <input
                type="text"
                value={customModelInput}
                onChange={(e) => setCustomModelInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCustomModelSave();
                }}
                placeholder={t("settings.llmModels.modelCustomPlaceholder")}
                style={{ ...INPUT_STYLE, flex: 1 }}
                data-testid={`model-custom-input-${vendor.id}`}
              />
              <button
                onClick={handleCustomModelSave}
                disabled={!customModelInput.trim()}
                style={{
                  ...BTN_PRIMARY,
                  fontSize: 11,
                  padding: "4px 10px",
                  opacity: customModelInput.trim() ? 1 : 0.4,
                }}
              >
                {t("settings.llmModels.add")}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Context window */}
      <div>
        <label
          style={{
            display: "block",
            marginBottom: 5,
            fontSize: 11,
            fontWeight: 600,
            color: "var(--syn-text-muted)",
          }}
        >
          {t("settings.contextWindow")}
        </label>
        <select
          value={contextWindow}
          onChange={(e) => setContextWindow(Number(e.target.value) as typeof contextWindow)}
          style={{ ...INPUT_STYLE, width: "auto", minWidth: 100 }}
          data-testid={`ctx-select-${vendor.id}`}
        >
          {CONTEXT_WINDOW_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {formatTokenCount(opt)}
            </option>
          ))}
        </select>
      </div>

      {/* Reasoning effort (API vendors only) */}
      {vendor.provider_type === "api" && (
        <div>
          <label
            style={{
              display: "block",
              marginBottom: 6,
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
            }}
          >
            {t("settings.llmModels.reasoningLabel")}
          </label>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {REASONING_OPTIONS.map((effort) => {
              const sel = currentReasoning === effort;
              const labelKey = `settings.llmModels.reasoning${effort.charAt(0).toUpperCase()}${effort.slice(1)}` as const;
              return (
                <button
                  key={effort}
                  onClick={() => void handleReasoningSelect(effort)}
                  data-testid={`reasoning-${vendor.id}-${effort}`}
                  style={{
                    padding: "3px 9px",
                    border: sel
                      ? "1px solid var(--syn-accent)"
                      : "1px solid var(--syn-border)",
                    borderRadius: 4,
                    background: sel ? "var(--syn-accent-soft)" : "transparent",
                    color: sel ? "var(--syn-accent)" : "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {t(labelKey)}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Provider tests */}
      <div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            onClick={() => void handleTestConnection()}
            disabled={testConn.running}
            style={{
              ...BTN_SECONDARY,
              fontSize: 11,
              padding: "4px 10px",
              opacity: testConn.running ? 0.5 : 1,
            }}
            data-testid={`test-conn-${vendor.id}`}
          >
            {testConn.running ? t("settings.llmModels.testRunning") : t("settings.llmModels.testConnectionBtn")}
          </button>
          <button
            onClick={() => void handleTestFunction()}
            disabled={testFunc.running}
            style={{
              ...BTN_SECONDARY,
              fontSize: 11,
              padding: "4px 10px",
              opacity: testFunc.running ? 0.5 : 1,
            }}
            data-testid={`test-func-${vendor.id}`}
          >
            {testFunc.running ? t("settings.llmModels.testRunning") : t("settings.llmModels.testFunctionBtn")}
          </button>
        </div>
        {testConn.ok !== null && (
          <p style={{ margin: "4px 0 0", fontSize: 11, color: testConn.ok ? "var(--syn-green)" : "var(--syn-red)" }}>
            Connection: {testConn.ok ? t("settings.llmModels.testOk") : t("settings.llmModels.testFailed")}
            {testConn.latency !== null && ` — ${testConn.latency}ms`}
            {testConn.detail && ` (${testConn.detail})`}
          </p>
        )}
        {testFunc.ok !== null && (
          <p style={{ margin: "2px 0 0", fontSize: 11, color: testFunc.ok ? "var(--syn-green)" : "var(--syn-red)" }}>
            Function: {testFunc.ok ? t("settings.llmModels.testOk") : t("settings.llmModels.testFailed")}
            {testFunc.latency !== null && ` — ${testFunc.latency}ms`}
            {testFunc.detail && ` (${testFunc.detail})`}
          </p>
        )}
      </div>
    </div>
  ) : null;

  return (
    <div style={rowStyle} data-testid={`vendor-row-${vendor.id}`}>
      {/* Row header */}
      <div
        style={headerStyle}
        onClick={() => setExpanded((v) => !v)}
        role="button"
        aria-expanded={expanded}
        aria-label={vendor.display_name}
      >
        {/* Expand chevron — LEFT (LLM Wiki: › that rotates down when open) */}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            color: "var(--syn-text-dim)",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 0.15s",
            flexShrink: 0,
          }}
          aria-hidden="true"
        >
          <polyline points="9 6 15 12 9 18" />
        </svg>

        {/* Name block: name + badges on top, sublabel below (LLM Wiki row) */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--syn-text)" }}>
              {vendor.display_name}
            </span>
            {typeBadge}
            {activeBadge}
            {vendor.needs_api_key && keyBadge}
          </div>
          {vendor.notes && (
            <div
              style={{
                fontSize: 12.5,
                color: "var(--syn-text-muted)",
                marginTop: 2,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {vendor.notes}
            </div>
          )}
        </div>

        {/* Toggle (radio-style) — RIGHT */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            void handleActivate();
          }}
          data-testid={`vendor-toggle-${vendor.id}`}
          aria-pressed={active}
          title={active ? vendor.display_name : t("settings.llmModels.activate")}
          style={{
            width: 40,
            height: 22,
            borderRadius: 11,
            border: "none",
            background: active ? "var(--syn-accent)" : "var(--syn-border)",
            cursor: active ? "default" : "pointer",
            position: "relative",
            flexShrink: 0,
            transition: "background 0.15s",
          }}
        >
          <span
            style={{
              position: "absolute",
              top: 3,
              left: active ? 21 : 3,
              width: 16,
              height: 16,
              borderRadius: "50%",
              background: "white",
              transition: "left 0.15s",
              boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            }}
          />
        </button>
      </div>

      {/* Vendor notes (visible when expanded) */}
      {/* notes are now shown as the always-visible sublabel in the row header (LLM Wiki style) */}

      {expandedContent}
    </div>
  );
}

// ─── SectionLlmModels ─────────────────────────────────────────────────────────

export function SectionLlmModels() {
  const { t } = useTranslation();

  // Provider config state
  const providerList = useProviderStore(useShallow(selectProviderList));
  const providerLoading = useProviderStore(selectProviderLoading);
  const providerError = useProviderStore(selectProviderError);
  const activeItem = useProviderStore(selectActiveProvider);
  const fetchProviders = useProviderStore(selectFetchProviderList);

  // Vendor catalog state
  const vendors = useProviderStore(useShallow(selectVendors));
  const vendorsLoading = useProviderStore(selectVendorsLoading);
  const vendorsError = useProviderStore(selectVendorsError);
  const fetchVendorCatalog = useProviderStore(selectFetchVendorCatalog);

  const vaultId = useGraphStore(selectVaultId);

  // Scope selector
  const [scope, setScope] = useState<"global" | "vault">("global");

  // Load the vendor catalog on mount. Deps are ONLY [fetchVendorCatalog] (a stable Zustand action):
  // putting vendors/vendorsLoading in the deps made the effect re-run when the fetch flipped
  // vendorsLoading, and an AbortController cleanup then aborted the very request it started —
  // an ERR_ABORTED loop that left the catalog stuck on "loading" (caught by live preview). No
  // AbortController here: under StrictMode a cleanup-abort + fetch-once guard would abort the only
  // request and never retry; letting the fetch settle into the global store is harmless (an extra
  // StrictMode-dev fetch at worst). No useProviderStore.getState() so the mocked-store tests pass.
  useEffect(() => {
    void fetchVendorCatalog();
  }, [fetchVendorCatalog]);

  useEffect(() => {
    const ac = new AbortController();
    if (providerList.length === 0 && !providerLoading) {
      void fetchProviders(ac.signal);
    }
    return () => ac.abort();
  }, [providerList.length, providerLoading, fetchProviders]);

  // Derive which vendor is active
  const activeVendorId = vendors.find((v) => isVendorActive(v, activeItem))?.id ?? null;

  // Filter configs to the current scope
  const scopedConfigs = providerList.filter(
    (c) =>
      c.scope === scope &&
      (scope === "vault" ? c.vault_id === vaultId : true),
  );

  return (
    <div>
      <SectionHeader
        title={t("settings.nav.providers")}
        desc={t("settings.llmModels.catalogDesc")}
      />

      {/* Scope selector */}
      <div style={{ display: "flex", gap: 0, marginBottom: 20, border: "1px solid var(--syn-border)", borderRadius: 6, overflow: "hidden", width: "fit-content" }}>
        {(["global", "vault"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setScope(s)}
            data-testid={`scope-btn-${s}`}
            style={{
              padding: "5px 14px",
              border: "none",
              borderRight: s === "global" ? "1px solid var(--syn-border)" : "none",
              background: scope === s ? "var(--syn-accent-soft)" : "transparent",
              color: scope === s ? "var(--syn-accent)" : "var(--syn-text-muted)",
              fontSize: 12,
              fontWeight: scope === s ? 600 : 400,
              cursor: "pointer",
            }}
          >
            {s === "global" ? t("settings.llmModels.globalScoped") : t("settings.llmModels.vaultScoped")}
          </button>
        ))}
      </div>

      {/* Errors */}
      {(providerError ?? vendorsError) && (
        <div
          style={{
            marginBottom: 12,
            padding: "6px 12px",
            background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
            border: "1px solid color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--syn-red)",
          }}
        >
          {vendorsError ?? providerError}
        </div>
      )}

      {/* Loading */}
      {(providerLoading || vendorsLoading) && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
          {t("settings.llmModels.loadingVendors")}
        </p>
      )}

      {/* Vendor catalog */}
      {!vendorsLoading && vendors.length > 0 && (
        <div data-testid="vendor-catalog">
          {vendors.map((vendor) => {
            const vendorConfig = findVendorConfig(vendor, scopedConfigs);
            const active = activeVendorId === vendor.id;
            return (
              <VendorRow
                key={vendor.id}
                vendor={vendor}
                vendorConfig={vendorConfig}
                active={active}
                scope={scope}
                vaultId={vaultId}
              />
            );
          })}
        </div>
      )}

      {!vendorsLoading && vendors.length === 0 && !vendorsError && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
          {t("settings.llmModels.vendorLoadError")}
        </p>
      )}
    </div>
  );
}
