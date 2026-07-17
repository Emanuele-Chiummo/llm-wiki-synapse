/**
 * SectionWebSearch.tsx — web-search backend config (ADR-0041 + ADR-0070).
 * ADR-0066 amends I9: SearXNG stays the DEFAULT, bundled, privacy-preserving backend; the
 * alternatives (Tavily · SerpApi · Firecrawl · Brave · Ollama-Web) are OPT-IN, off by default.
 * The provider selector persists the `web_search_provider` config-override key (S23) via
 * /config/app; the SearXNG URL/categories/max-queries fields (shown only when SearXNG is active)
 * persist to /web-search/config as before.
 * BRANDING: never black — selected/active uses var(--syn-accent) + white; cloud warnings use
 * var(--syn-amber). I3: local state only, single fetch on mount, PUT on each user action.
 */
import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Eye, EyeOff } from "lucide-react";
import { SectionHeader, Field } from "../ui";
import { Button } from "../../ui/Button";
import {
  fetchWebSearchConfig,
  setWebSearchConfig,
  fetchWebSearchProviderKeys,
  setWebSearchProviderKey,
  type WebSearchProviderKeysResponse,
} from "../../../api/providerClient";
import { getAppConfig, putAppConfig } from "../../../api/appConfigClient";
import type { WebSearchConfigResponse } from "../../../api/types";

// LLM Wiki card style — bordered surface card (brand colors only, never black).
const WS_CARD: CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "14px 16px",
};

// Provider catalog — the single source of truth mirrors backend ops.web_search.PROVIDERS.
// isCloud drives the amber opt-in warning (queries leave the local network — I9).
interface ProviderMeta {
  id: string;
  isCloud: boolean;
}
const PROVIDERS: ProviderMeta[] = [
  { id: "searxng", isCloud: false },
  { id: "tavily", isCloud: true },
  { id: "serpapi", isCloud: true },
  { id: "firecrawl", isCloud: true },
  { id: "brave", isCloud: true },
  { id: "ollama_web", isCloud: false },
];
const DEFAULT_PROVIDER = "searxng";

export function SectionWebSearch() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<WebSearchConfigResponse | null>(null);
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);

  const [provider, setProvider] = useState<string>(DEFAULT_PROVIDER);
  const [providerBusy, setProviderBusy] = useState(false);

  const [urlInput, setUrlInput] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [categoriesInput, setCategoriesInput] = useState("");
  const [maxQueriesInput, setMaxQueriesInput] = useState<number>(3);

  // P3-e (ADR-0071): per-cloud-provider API-key posture + entry field.
  const [keyPosture, setKeyPosture] = useState<WebSearchProviderKeysResponse | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchWebSearchConfig(ac.signal)
      .then((data) => {
        setCfg(data);
        setErr(false);
        setUrlInput(data.url ?? "");
        setCategoriesInput(data.categories.join(","));
        setMaxQueriesInput(data.max_queries);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    // Provider selector value from the app-config override layer (S23). Best-effort: on any
    // failure the selector stays at the SearXNG default (never blocks the SearXNG fields).
    getAppConfig(ac.signal)
      .then((resp) => {
        const found = resp.settings.find((s) => s.key === "web_search_provider");
        if (found?.value) setProvider(found.value);
      })
      .catch(() => {
        /* keep default */
      });
    fetchWebSearchProviderKeys(ac.signal)
      .then(setKeyPosture)
      .catch(() => {
        /* best-effort */
      });
    return () => {
      ac.abort();
    };
  }, []);

  const handleSaveKey = async () => {
    if (keyBusy || !keyInput.trim()) return;
    setKeyBusy(true);
    try {
      const resp = await setWebSearchProviderKey({ provider, key: keyInput.trim() });
      setKeyPosture(resp);
      setKeyInput("");
    } catch {
      /* checkResponse surfaces the 400 (e.g. no SYNAPSE_SECRET_KEY) via console */
    } finally {
      setKeyBusy(false);
    }
  };

  const handleClearKey = async () => {
    if (keyBusy) return;
    setKeyBusy(true);
    try {
      const resp = await setWebSearchProviderKey({ provider, clear: true });
      setKeyPosture(resp);
      setKeyInput("");
    } catch {
      /* ignore */
    } finally {
      setKeyBusy(false);
    }
  };

  const applyResponse = (resp: WebSearchConfigResponse) => {
    setCfg(resp);
    setUrlInput(resp.url ?? "");
    setCategoriesInput(resp.categories.join(","));
    setMaxQueriesInput(resp.max_queries);
    setUrlError(null);
  };

  const handleSelectProvider = async (id: string) => {
    if (providerBusy || id === provider) return;
    const prev = provider;
    setProvider(id); // optimistic
    setProviderBusy(true);
    try {
      await putAppConfig("web_search_provider", id);
    } catch {
      setProvider(prev); // revert on failure
    } finally {
      setProviderBusy(false);
    }
  };

  const validateUrl = (raw: string): boolean => {
    if (raw.trim() === "") return true;
    try {
      const u = new URL(raw.trim());
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  };

  const handleSaveUrl = async () => {
    if (busy) return;
    const raw = urlInput.trim();
    if (raw !== "" && !validateUrl(raw)) {
      setUrlError(t("settings.webSearch.urlValidationError"));
      return;
    }
    setUrlError(null);
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_url: raw === "" ? null : raw });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleSaveCategories = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_categories: categoriesInput });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleSaveMaxQueries = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_max_queries: maxQueriesInput });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ clear: true });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const selectedMeta = PROVIDERS.find((p) => p.id === provider);
  const showCloudWarning = selectedMeta?.isCloud === true;
  const isSearxng = provider === "searxng";

  return (
    <div>
      <SectionHeader title={t("settings.nav.webSearch")} desc={t("settings.webSearch.desc")} />

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>
          {t("settings.webSearch.error")}
        </p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>
          {t("settings.webSearch.loading")}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* ── Provider selector (S23, ADR-0070) ── */}
          <div style={WS_CARD} data-testid="web-search-provider-card">
            <Field label={t("settings.webSearch.providerLabel")}>
              <p
                style={{
                  margin: "0 0 10px",
                  fontSize: 11,
                  color: "var(--syn-text-muted)",
                  lineHeight: 1.5,
                }}
              >
                {t("settings.webSearch.providerHelp")}
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {PROVIDERS.map((p) => {
                  const active = p.id === provider;
                  return (
                    <button
                      key={p.id}
                      data-testid={`web-search-provider-${p.id}`}
                      role="radio"
                      aria-checked={active}
                      disabled={providerBusy}
                      onClick={() => {
                        void handleSelectProvider(p.id);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 10,
                        textAlign: "left",
                        padding: "10px 12px",
                        borderRadius: 8,
                        cursor: providerBusy ? "not-allowed" : "pointer",
                        border: active
                          ? "1px solid var(--syn-accent)"
                          : "1px solid var(--syn-border)",
                        background: active ? "var(--syn-accent)" : "var(--syn-surface)",
                        color: active ? "#fff" : "var(--syn-text)",
                        transition: "background 0.12s, border-color 0.12s",
                      }}
                    >
                      <span
                        style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
                      >
                        <span style={{ fontSize: 13, fontWeight: 600 }}>
                          {t(`settings.webSearch.provider.${p.id}`)}
                        </span>
                        <span
                          style={{
                            fontSize: 11,
                            color: active ? "rgba(255,255,255,0.85)" : "var(--syn-text-muted)",
                          }}
                        >
                          {p.id === DEFAULT_PROVIDER
                            ? t("settings.webSearch.providerDefaultBadge")
                            : p.isCloud
                              ? t("settings.webSearch.providerCloudBadge")
                              : t("settings.webSearch.providerLocalBadge")}
                        </span>
                      </span>
                      {active && (
                        <span aria-hidden style={{ fontSize: 13, fontWeight: 700, flexShrink: 0 }}>
                          ✓
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>

              {showCloudWarning && (
                <p
                  data-testid="web-search-provider-cloud-warning"
                  style={{
                    margin: "10px 0 0",
                    padding: "8px 10px",
                    borderRadius: 8,
                    border:
                      "1px solid color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)",
                    background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
                    color: "var(--syn-amber)",
                    fontSize: 11.5,
                    lineHeight: 1.5,
                  }}
                >
                  {t("settings.webSearch.cloudWarning")}
                </p>
              )}

              {/* P3-e: API-key entry for the selected cloud provider (ADR-0071) */}
              {showCloudWarning &&
                (() => {
                  const posture = keyPosture?.providers?.[provider];
                  const secretsOk = keyPosture?.secrets_available !== false;
                  return (
                    <div data-testid="web-search-provider-key" style={{ marginTop: 12 }}>
                      <div
                        style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}
                      >
                        <label
                          style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}
                        >
                          {t("settings.webSearch.apiKeyLabel")}
                        </label>
                        <span
                          data-testid="web-search-key-badge"
                          style={{
                            padding: "1px 8px",
                            borderRadius: 4,
                            fontSize: 10.5,
                            fontWeight: 600,
                            background: posture?.configured
                              ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                              : "var(--syn-surface-hover)",
                            color: posture?.configured
                              ? "var(--syn-green)"
                              : "var(--syn-text-muted)",
                          }}
                        >
                          {posture?.configured
                            ? t("settings.webSearch.apiKeyConfigured", { source: posture.source })
                            : t("settings.webSearch.apiKeyNotConfigured")}
                        </span>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <div style={{ position: "relative", flex: 1 }}>
                          <input
                            type={showKey ? "text" : "password"}
                            data-testid="web-search-key-input"
                            value={keyInput}
                            onChange={(e) => setKeyInput(e.target.value)}
                            placeholder={t("settings.webSearch.apiKeyPlaceholder")}
                            autoComplete="new-password"
                            disabled={keyBusy || !secretsOk}
                            className="syn-input"
                            style={{
                              width: "100%",
                              paddingRight: 34,
                              fontFamily: "ui-monospace, Menlo, monospace",
                              fontSize: 12,
                            }}
                          />
                          <button
                            type="button"
                            onClick={() => setShowKey((v) => !v)}
                            aria-label={showKey ? t("connect.hideToken") : t("connect.showToken")}
                            style={{
                              position: "absolute",
                              right: 8,
                              top: "50%",
                              transform: "translateY(-50%)",
                              background: "none",
                              border: "none",
                              cursor: "pointer",
                              color: "var(--syn-text-dim)",
                              padding: 2,
                              display: "flex",
                              alignItems: "center",
                            }}
                          >
                            {showKey ? (
                              <EyeOff size={13} aria-hidden="true" />
                            ) : (
                              <Eye size={13} aria-hidden="true" />
                            )}
                          </button>
                        </div>
                        <Button
                          variant="accent-ghost"
                          data-testid="web-search-key-save"
                          onClick={() => {
                            void handleSaveKey();
                          }}
                          disabled={keyBusy || !keyInput.trim() || !secretsOk}
                          style={{ flexShrink: 0 }}
                        >
                          {t("settings.webSearch.apiKeySave")}
                        </Button>
                        {posture?.source === "db" && (
                          <button
                            data-testid="web-search-key-clear"
                            onClick={() => {
                              void handleClearKey();
                            }}
                            disabled={keyBusy}
                            style={{
                              padding: "6px 12px",
                              border:
                                "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                              borderRadius: 6,
                              background: "transparent",
                              color: "var(--syn-red)",
                              fontSize: 12,
                              cursor: keyBusy ? "not-allowed" : "pointer",
                              flexShrink: 0,
                            }}
                          >
                            {t("settings.webSearch.apiKeyClear")}
                          </button>
                        )}
                      </div>
                      <p
                        style={{
                          margin: "6px 0 0",
                          fontSize: 11,
                          color: secretsOk ? "var(--syn-text-dim)" : "var(--syn-amber)",
                          lineHeight: 1.5,
                        }}
                      >
                        {secretsOk
                          ? t("settings.webSearch.apiKeyHint")
                          : t("settings.webSearch.apiKeyNoSecret")}
                      </p>
                    </div>
                  );
                })()}
            </Field>
          </div>

          {/* SearXNG-specific configuration — shown only when SearXNG is the active backend. */}
          {isSearxng && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  data-testid="web-search-configured-badge"
                  style={{
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: cfg.configured
                      ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)"
                      : "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                    border: `1px solid ${cfg.configured ? "color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)" : "color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)"}`,
                    color: cfg.configured ? "var(--syn-green)" : "var(--syn-red)",
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                >
                  {cfg.configured
                    ? t("settings.webSearch.configuredBadge")
                    : t("settings.webSearch.notConfiguredBadge")}
                </span>
                <span
                  data-testid="web-search-source-badge"
                  style={{
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: "var(--syn-surface-hover)",
                    color: "var(--syn-text-muted)",
                    fontSize: 11,
                  }}
                >
                  {t("settings.webSearch.sourceBadge", { source: cfg.source })}
                </span>
              </div>

              <div style={WS_CARD}>
                <Field label={t("settings.webSearch.urlLabel")}>
                  <p
                    style={{
                      margin: "0 0 6px",
                      fontSize: 11,
                      color: "var(--syn-text-muted)",
                      lineHeight: 1.5,
                    }}
                  >
                    {t("settings.webSearch.urlHelp")}
                  </p>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="text"
                      data-testid="web-search-url-input"
                      value={urlInput}
                      onChange={(e) => {
                        setUrlInput(e.target.value);
                        setUrlError(null);
                      }}
                      placeholder={t("settings.webSearch.urlPlaceholder")}
                      className="syn-input"
                      style={{ flex: 1 }}
                    />
                    <Button
                      variant="accent-ghost"
                      data-testid="web-search-url-save"
                      onClick={() => {
                        void handleSaveUrl();
                      }}
                      disabled={busy}
                      style={{ flexShrink: 0 }}
                    >
                      {busy ? "…" : t("settings.webSearch.urlSave")}
                    </Button>
                  </div>
                  {urlError && (
                    <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}>
                      {urlError}
                    </p>
                  )}
                </Field>
              </div>

              <div style={WS_CARD}>
                <Field label={t("settings.webSearch.categoriesLabel")}>
                  <p
                    style={{
                      margin: "0 0 6px",
                      fontSize: 11,
                      color: "var(--syn-text-muted)",
                      lineHeight: 1.5,
                    }}
                  >
                    {t("settings.webSearch.categoriesHelp")}
                  </p>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="text"
                      data-testid="web-search-categories-input"
                      value={categoriesInput}
                      onChange={(e) => setCategoriesInput(e.target.value)}
                      placeholder={t("settings.webSearch.categoriesPlaceholder")}
                      className="syn-input"
                      style={{ flex: 1 }}
                    />
                    <Button
                      variant="accent-ghost"
                      data-testid="web-search-categories-save"
                      onClick={() => {
                        void handleSaveCategories();
                      }}
                      disabled={busy}
                      style={{ flexShrink: 0 }}
                    >
                      {busy ? "…" : t("settings.webSearch.categoriesSave")}
                    </Button>
                  </div>
                </Field>
              </div>

              <div style={WS_CARD}>
                <Field label={t("settings.webSearch.maxQueriesLabel")}>
                  <p
                    style={{
                      margin: "0 0 6px",
                      fontSize: 11,
                      color: "var(--syn-text-muted)",
                      lineHeight: 1.5,
                    }}
                  >
                    {t("settings.webSearch.maxQueriesHelp")}
                  </p>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="number"
                      data-testid="web-search-max-queries-input"
                      value={maxQueriesInput}
                      min={1}
                      max={50}
                      onChange={(e) => setMaxQueriesInput(Number(e.target.value))}
                      className="syn-input"
                      style={{ width: 80 }}
                    />
                    <Button
                      variant="accent-ghost"
                      data-testid="web-search-max-queries-save"
                      onClick={() => {
                        void handleSaveMaxQueries();
                      }}
                      disabled={busy}
                    >
                      {busy ? "…" : t("settings.webSearch.maxQueriesSave")}
                    </Button>
                  </div>
                </Field>
              </div>

              <div style={WS_CARD}>
                <p
                  style={{
                    margin: "0 0 10px",
                    fontSize: 11,
                    color: "var(--syn-text-muted)",
                    lineHeight: 1.5,
                  }}
                >
                  {t("settings.webSearch.clearHelp")}
                </p>
                <button
                  data-testid="web-search-clear-btn"
                  onClick={() => {
                    void handleClear();
                  }}
                  disabled={busy}
                  style={{
                    padding: "6px 14px",
                    border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                    borderRadius: 6,
                    background: "transparent",
                    color: "var(--syn-red)",
                    fontSize: 12,
                    cursor: busy ? "not-allowed" : "pointer",
                    fontWeight: 500,
                    opacity: busy ? 0.4 : 1,
                  }}
                >
                  {t("settings.webSearch.clearButton")}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
