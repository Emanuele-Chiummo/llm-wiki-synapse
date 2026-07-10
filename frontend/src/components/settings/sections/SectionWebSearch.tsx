/**
 * SectionWebSearch.tsx — SearXNG web search config (ADR-0041).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * ADR-0041: SearXNG is the ONLY web-search backend (I9). No provider field.
 * I3: single fetch on mount; PUT on each user action; local state only.
 */
import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, Field, INPUT_STYLE, BTN_PRIMARY } from "../ui";
import {
  fetchWebSearchConfig,
  setWebSearchConfig,
} from "../../../api/providerClient";
import type { WebSearchConfigResponse } from "../../../api/types";

// LLM Wiki card style — bordered surface card (brand colors only, never black).
const WS_CARD: CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "14px 16px",
};

export function SectionWebSearch() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<WebSearchConfigResponse | null>(null);
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);

  const [urlInput, setUrlInput] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [categoriesInput, setCategoriesInput] = useState("");
  const [maxQueriesInput, setMaxQueriesInput] = useState<number>(3);

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
    return () => { ac.abort(); };
  }, []);

  const applyResponse = (resp: WebSearchConfigResponse) => {
    setCfg(resp);
    setUrlInput(resp.url ?? "");
    setCategoriesInput(resp.categories.join(","));
    setMaxQueriesInput(resp.max_queries);
    setUrlError(null);
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

  return (
    <div>
      <SectionHeader title={t("settings.nav.webSearch")} desc={t("settings.webSearch.desc")} />

      <div style={{
        marginBottom: 20,
        padding: "8px 12px",
        background: "var(--syn-bg-soft)",
        border: "1px solid var(--syn-border)",
        borderRadius: 6,
        fontSize: 11,
        color: "var(--syn-text-muted)",
        lineHeight: 1.5,
      }}>
        {t("settings.webSearch.searxngOnly")}
      </div>

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.webSearch.error")}</p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.webSearch.loading")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              data-testid="web-search-configured-badge"
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: cfg.configured ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                border: `1px solid ${cfg.configured ? "color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)" : "color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)"}`,
                color: cfg.configured ? "var(--syn-green)" : "var(--syn-red)",
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              {cfg.configured ? t("settings.webSearch.configuredBadge") : t("settings.webSearch.notConfiguredBadge")}
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
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webSearch.urlHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="web-search-url-input"
                  value={urlInput}
                  onChange={(e) => { setUrlInput(e.target.value); setUrlError(null); }}
                  placeholder={t("settings.webSearch.urlPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="web-search-url-save"
                  onClick={() => { void handleSaveUrl(); }}
                  disabled={busy}
                  style={{ ...BTN_PRIMARY, opacity: busy ? 0.4 : 1, cursor: busy ? "not-allowed" : "pointer", flexShrink: 0 }}
                >
                  {busy ? "…" : t("settings.webSearch.urlSave")}
                </button>
              </div>
              {urlError && (
                <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}>{urlError}</p>
              )}
            </Field>
          </div>

          <div style={WS_CARD}>
            <Field label={t("settings.webSearch.categoriesLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webSearch.categoriesHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="web-search-categories-input"
                  value={categoriesInput}
                  onChange={(e) => setCategoriesInput(e.target.value)}
                  placeholder={t("settings.webSearch.categoriesPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="web-search-categories-save"
                  onClick={() => { void handleSaveCategories(); }}
                  disabled={busy}
                  style={{ ...BTN_PRIMARY, opacity: busy ? 0.4 : 1, cursor: busy ? "not-allowed" : "pointer", flexShrink: 0 }}
                >
                  {busy ? "…" : t("settings.webSearch.categoriesSave")}
                </button>
              </div>
            </Field>
          </div>

          <div style={WS_CARD}>
            <Field label={t("settings.webSearch.maxQueriesLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
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
                  style={{ ...INPUT_STYLE, width: 80 }}
                />
                <button
                  data-testid="web-search-max-queries-save"
                  onClick={() => { void handleSaveMaxQueries(); }}
                  disabled={busy}
                  style={{ ...BTN_PRIMARY, opacity: busy ? 0.4 : 1, cursor: busy ? "not-allowed" : "pointer" }}
                >
                  {busy ? "…" : t("settings.webSearch.maxQueriesSave")}
                </button>
              </div>
            </Field>
          </div>

          <div style={WS_CARD}>
            <p style={{ margin: "0 0 10px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.webSearch.clearHelp")}
            </p>
            <button
              data-testid="web-search-clear-btn"
              onClick={() => { void handleClear(); }}
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
        </div>
      )}
    </div>
  );
}
