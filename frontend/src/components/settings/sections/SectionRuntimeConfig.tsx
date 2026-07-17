/**
 * SectionRuntimeConfig.tsx — R11-2 migrated runtime config fields (ADR-0053).
 * Renders a subset of the allowed keys, determined by the `keys` prop.
 * Each field: effective value + source badge (Default / Custom) + Save + Reset.
 * PUT /config/app/{key} on save; DELETE /config/app/{key} on reset (ADR-0053 §3.3).
 * I3: local state only, no Zustand store. I6: sends strings, no embedding logic.
 * AC-R11-2-12: primary labels are plain language (never equal to env-var names).
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getAppConfig,
  putAppConfig,
  resetAppConfig,
  type AppConfigEntry,
  type AppConfigKey,
} from "../../../api/appConfigClient";
import { Button } from "../../ui/Button";

// ─── Types ─────────────────────────────────────────────────────────────────────

type RcEntry = AppConfigEntry & { localValue: string; saving: boolean; saved: boolean };

const EMPTY_ENTRY: Omit<RcEntry, "key"> = {
  value: "",
  source: "env",
  localValue: "",
  saving: false,
  saved: false,
};

// ─── SectionRuntimeConfig ──────────────────────────────────────────────────────

export function SectionRuntimeConfig({ keys }: { keys: AppConfigKey[] }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<Map<AppConfigKey, RcEntry>>(new Map());
  const [loading, setLoading] = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setFetchErr(null);
    getAppConfig(ac.signal)
      .then((resp) => {
        const map = new Map<AppConfigKey, RcEntry>();
        for (const key of keys) {
          const found = resp.settings.find((s) => s.key === key);
          map.set(key, {
            key,
            value: found?.value ?? "",
            source: found?.source ?? "env",
            localValue: found?.value ?? "",
            saving: false,
            saved: false,
          });
        }
        setEntries(map);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setFetchErr(t("config.error"));
        setLoading(false);
      });
    return () => { ac.abort(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys.join(",")]);

  const setLocal = (key: AppConfigKey, localValue: string) => {
    setEntries((prev) => {
      const next = new Map(prev);
      const entry = next.get(key) ?? { ...EMPTY_ENTRY, key };
      next.set(key, { ...entry, localValue });
      return next;
    });
  };

  const handleSave = async (key: AppConfigKey) => {
    const entry = entries.get(key);
    if (!entry) return;
    setEntries((prev) => {
      const next = new Map(prev);
      next.set(key, { ...entry, saving: true, saved: false });
      return next;
    });
    try {
      await putAppConfig(key, entry.localValue);
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, value: entry.localValue, source: "override", saving: false, saved: true });
        return next;
      });
      setTimeout(() => {
        setEntries((prev) => {
          const next = new Map(prev);
          const e = next.get(key);
          if (e) next.set(key, { ...e, saved: false });
          return next;
        });
      }, 2500);
    } catch {
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, saving: false });
        return next;
      });
    }
  };

  // Atomic save-with-value — used by toggles to auto-save on click (LLM Wiki: "saves instantly").
  // Persists the passed *value* directly (not the possibly-stale entries state), avoiding the
  // setLocal-then-save race.
  const handleSaveValue = async (key: AppConfigKey, value: string) => {
    setEntries((prev) => {
      const next = new Map(prev);
      const e = next.get(key) ?? { ...EMPTY_ENTRY, key };
      next.set(key, { ...e, localValue: value, saving: true, saved: false });
      return next;
    });
    try {
      await putAppConfig(key, value);
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, value, localValue: value, source: "override", saving: false, saved: true });
        return next;
      });
      setTimeout(() => {
        setEntries((prev) => {
          const next = new Map(prev);
          const e = next.get(key);
          if (e) next.set(key, { ...e, saved: false });
          return next;
        });
      }, 2500);
    } catch {
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, saving: false });
        return next;
      });
    }
  };

  const handleReset = async (key: AppConfigKey) => {
    const entry = entries.get(key);
    if (!entry) return;
    setEntries((prev) => {
      const next = new Map(prev);
      next.set(key, { ...entry, saving: true, saved: false });
      return next;
    });
    try {
      await resetAppConfig(key);
      const resp = await getAppConfig();
      const found = resp.settings.find((s) => s.key === key);
      setEntries((prev) => {
        const next = new Map(prev);
        next.set(key, {
          key,
          value: found?.value ?? "",
          source: found?.source ?? "env",
          localValue: found?.value ?? "",
          saving: false,
          saved: true,
        });
        return next;
      });
      setTimeout(() => {
        setEntries((prev) => {
          const next = new Map(prev);
          const e = next.get(key);
          if (e) next.set(key, { ...e, saved: false });
          return next;
        });
      }, 2500);
    } catch {
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, saving: false });
        return next;
      });
    }
  };

  if (loading) {
    return <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("config.loading")}</p>;
  }
  if (fetchErr) {
    return <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{fetchErr}</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 8 }}>
      {keys.map((key) => {
        const entry = entries.get(key) ?? { ...EMPTY_ENTRY, key };
        return (
          <RuntimeConfigField
            key={key}
            configKey={key}
            entry={entry}
            onLocalChange={setLocal}
            onSave={handleSave}
            onReset={handleReset}
            onToggle={handleSaveValue}
          />
        );
      })}
    </div>
  );
}

// ─── RuntimeConfigField ────────────────────────────────────────────────────────

// Card container matching LLM Wiki's settings cards (bordered, rounded, surface bg).
const RC_CARD = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: "14px 16px",
} as const;

const RC_BOOL_KEYS = new Set<AppConfigKey>([
  "embeddings_enabled",
  "wikilink_enrich_enabled",
  "vision_captions_enabled",
]);

function RuntimeConfigField({
  configKey,
  entry,
  onLocalChange,
  onSave,
  onReset,
  onToggle,
}: {
  configKey: AppConfigKey;
  entry: RcEntry;
  onLocalChange: (key: AppConfigKey, value: string) => void;
  onSave: (key: AppConfigKey) => Promise<void>;
  onReset: (key: AppConfigKey) => Promise<void>;
  onToggle: (key: AppConfigKey, value: string) => Promise<void>;
}) {
  const { t } = useTranslation();

  const i18nBase = `config.${configKeyToI18nSuffix(configKey)}`;
  const label = t(`${i18nBase}.label`);
  const help = t(`${i18nBase}.help`);
  const isOverride = entry.source === "override";
  const isDirty = entry.localValue !== entry.value;

  // ── Boolean toggle — LLM Wiki card: title+help LEFT, ON/OFF + switch RIGHT, auto-saves ──
  if (RC_BOOL_KEYS.has(configKey)) {
    const isOn = entry.localValue === "true" || entry.localValue === "1";
    return (
      <div data-testid={`rc-field-${configKey}`} style={RC_CARD}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <label style={{ display: "block", fontSize: 14, fontWeight: 600, color: "var(--syn-text)", marginBottom: 2 }}>
              {label}
            </label>
            <p style={{ fontSize: 12.5, color: "var(--syn-text-muted)", margin: 0, lineHeight: 1.5 }}>{help}</p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.03em", color: "var(--syn-text-dim)" }}>
              {isOn ? t(`${i18nBase}.on`) : t(`${i18nBase}.off`)}
            </span>
            <button
              data-testid={`rc-control-${configKey}`}
              role="switch"
              aria-checked={isOn}
              aria-label={label}
              disabled={entry.saving}
              onClick={() => { void onToggle(configKey, isOn ? "false" : "true"); }}
              style={{
                width: 40, height: 22, borderRadius: 11, border: "none",
                cursor: entry.saving ? "not-allowed" : "pointer", position: "relative",
                background: isOn ? "var(--syn-accent)" : "var(--syn-border)",
                transition: "background 0.15s", flexShrink: 0, padding: 0,
              }}
            >
              <span style={{
                position: "absolute", top: 3, left: isOn ? 21 : 3, width: 16, height: 16,
                borderRadius: "50%", background: "#fff", transition: "left 0.15s",
              }} />
            </button>
          </div>
        </div>
        {isOverride && (
          <button
            data-testid={`rc-reset-${configKey}`}
            onClick={() => { void onReset(configKey); }}
            disabled={entry.saving}
            style={{ marginTop: 10, background: "none", border: "none", padding: 0,
              color: "var(--syn-text-muted)", fontSize: 11, cursor: "pointer", textDecoration: "underline" }}
          >
            {t("config.resetToDefault")}
          </button>
        )}
      </div>
    );
  }

  // ── Non-boolean — card: title + badge + help on top, control below, Save when dirty ──
  return (
    <div data-testid={`rc-field-${configKey}`} style={RC_CARD}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
        <label style={{ fontSize: 14, fontWeight: 600, color: "var(--syn-text)" }}>{label}</label>
        <span
          data-testid={`rc-source-badge-${configKey}`}
          style={{
            padding: "1px 7px", borderRadius: 4, fontSize: 10, fontWeight: 600,
            background: isOverride
              ? "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-mix-base) 88%)"
              : "var(--syn-surface-hover)",
            color: isOverride ? "var(--syn-accent)" : "var(--syn-text-dim)",
            border: isOverride
              ? "1px solid color-mix(in srgb, var(--syn-accent) 30%, transparent 70%)"
              : "1px solid var(--syn-border)",
          }}
        >
          {isOverride ? t("config.sourceBadge.override") : t("config.sourceBadge.env")}
        </span>
      </div>

      <p style={{ fontSize: 12.5, color: "var(--syn-text-muted)", margin: "0 0 8px", lineHeight: 1.5 }}>{help}</p>

      <RcControl configKey={configKey} entry={entry} onLocalChange={onLocalChange} />

      {(isDirty || isOverride || entry.saved) && (
        <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
          {isDirty && (
            <Button
              variant="accent-ghost"
              data-testid={`rc-save-${configKey}`}
              onClick={() => { void onSave(configKey); }}
              disabled={entry.saving}
            >
              {entry.saving ? t("config.saving") : t("config.save")}
            </Button>
          )}
          {isOverride && (
            <Button
              variant="ghost"
              data-testid={`rc-reset-${configKey}`}
              onClick={() => { void onReset(configKey); }}
              disabled={entry.saving}
            >
              {t("config.resetToDefault")}
            </Button>
          )}
          {entry.saved && (
            <span style={{ fontSize: 11, color: "var(--syn-green)" }}>
              {isOverride ? t("config.saved") : t("config.resetDone")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ─── configKeyToI18nSuffix ─────────────────────────────────────────────────────

export function configKeyToI18nSuffix(key: AppConfigKey): string {
  const map: Record<AppConfigKey, string> = {
    pdf_extractor:              "pdfExtractor",
    marker_service_url:         "markerServiceUrl",
    marker_timeout_seconds:     "markerTimeoutSeconds",
    cost_alert_threshold_usd:   "costAlertThresholdUsd",
    embeddings_enabled:         "embeddingsEnabled",
    embedding_format:           "embeddingFormat",
    overview_language:          "overviewLanguage",
    wikilink_enrich_enabled:    "wikilinkEnrichEnabled",
    domain_vocabulary:          "domainVocabulary",
    lint_schedule:              "lintSchedule",
    backfill_schedule:          "backfillSchedule",
    schema_review_schedule:     "schemaReviewSchedule",
    reclassify_schedule:        "reclassifySchedule",
    // S14–S18: new loop-limit keys
    deep_research_max_iter:     "deepResearchMaxIter",
    deep_research_token_budget: "deepResearchTokenBudget",
    deep_research_max_queries:  "deepResearchMaxQueries",
    lint_max_iter:              "lintMaxIter",
    lint_token_budget:          "lintTokenBudget",
    // S19/S20: Image Captioning (v1.5 P3-a)
    vision_captions_enabled:    "visionCaptionsEnabled",
    vision_max_images_per_run:  "visionMaxImagesPerRun",
    // S21/S22: MinerU cloud PDF (v1.5 P3-d)
    mineru_api_url:             "mineruApiUrl",
    mineru_timeout_seconds:     "mineruTimeoutSeconds",
    // S23: web-search provider selector (v1.5 P3-e) — rendered by SectionWebSearch, not here.
    web_search_provider:        "webSearchProvider",
  };
  return map[key];
}

// ─── RcControl ─────────────────────────────────────────────────────────────────
/** Per-key control: select for enum keys, text input for free-form, toggle for booleans. */

function RcControl({
  configKey,
  entry,
  onLocalChange,
}: {
  configKey: AppConfigKey;
  entry: RcEntry;
  onLocalChange: (key: AppConfigKey, value: string) => void;
}) {
  const { t } = useTranslation();

  if (configKey === "pdf_extractor") {
    const isMineru = entry.localValue === "mineru";
    return (
      <div>
        <select
          data-testid="rc-control-pdf_extractor"
          value={entry.localValue}
          onChange={(e) => onLocalChange(configKey, e.target.value)}
          className="syn-input"
        >
          <option value="pypdf">{t("config.pdfExtractor.optionPypdf")}</option>
          <option value="marker">{t("config.pdfExtractor.optionMarker")}</option>
          <option value="mineru">{t("config.pdfExtractor.optionMineru")}</option>
        </select>
        {isMineru && (
          <p
            data-testid="rc-mineru-cloud-warning"
            style={{
              margin: "8px 0 0",
              padding: "8px 10px",
              borderRadius: 8,
              border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)",
              background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
              color: "var(--syn-amber)",
              fontSize: 11.5,
              lineHeight: 1.5,
            }}
          >
            {t("config.pdfExtractor.mineruCloudWarning")}
          </p>
        )}
      </div>
    );
  }

  if (configKey === "embedding_format") {
    return (
      <select
        data-testid="rc-control-embedding_format"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        className="syn-input"
      >
        <option value="ollama">{t("config.embeddingFormat.optionOllama")}</option>
        <option value="openai">{t("config.embeddingFormat.optionOpenai")}</option>
      </select>
    );
  }

  if (
    configKey === "embeddings_enabled" ||
    configKey === "wikilink_enrich_enabled" ||
    configKey === "vision_captions_enabled"
  ) {
    const i18nBase = `config.${configKeyToI18nSuffix(configKey)}`;
    const isOn = entry.localValue === "true" || entry.localValue === "1";
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button
          data-testid={`rc-control-${configKey}`}
          role="switch"
          aria-checked={isOn}
          onClick={() => onLocalChange(configKey, isOn ? "false" : "true")}
          style={{
            width: 40,
            height: 22,
            borderRadius: 11,
            border: "none",
            cursor: "pointer",
            position: "relative",
            background: isOn ? "var(--syn-accent)" : "var(--syn-border)",
            transition: "background 0.15s",
            flexShrink: 0,
            padding: 0,
          }}
        >
          <span
            style={{
              position: "absolute",
              top: 3,
              left: isOn ? 21 : 3,
              width: 16,
              height: 16,
              borderRadius: "50%",
              background: "white",
              transition: "left 0.15s",
            }}
          />
        </button>
        <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
          {isOn ? t(`${i18nBase}.on`) : t(`${i18nBase}.off`)}
        </span>
      </div>
    );
  }

  if (configKey === "overview_language") {
    return (
      <input
        type="text"
        data-testid="rc-control-overview_language"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        placeholder={t("config.overviewLanguage.placeholder")}
        className="syn-input"
      />
    );
  }

  if (configKey === "marker_service_url") {
    return (
      <input
        type="text"
        data-testid="rc-control-marker_service_url"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        placeholder={t("config.markerServiceUrl.placeholder")}
        className="syn-input"
      />
    );
  }

  if (configKey === "domain_vocabulary") {
    let displayValue = entry.localValue;
    if (displayValue.trim().startsWith("[")) {
      try {
        const parsed = JSON.parse(displayValue) as string[];
        displayValue = parsed.join(", ");
      } catch {
        // leave as-is if malformed
      }
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <input
          type="text"
          data-testid="rc-control-domain_vocabulary"
          value={displayValue}
          onChange={(e) => {
            const raw = e.target.value;
            const names = raw.split(",").map((s) => s.trim()).filter(Boolean);
            const jsonValue = JSON.stringify(names);
            onLocalChange(configKey, jsonValue);
          }}
          placeholder={t("config.domainVocabulary.placeholder")}
          className="syn-input"
        />
        <p style={{ margin: 0, fontSize: 10, color: "var(--syn-text-dim)", lineHeight: 1.4 }}>
          {t("config.domainVocabulary.chipHint")}
        </p>
      </div>
    );
  }

  // Numeric keys: marker_timeout_seconds, cost_alert_threshold_usd,
  // and the 5 new loop-limit keys (S14–S18)
  const numericKeys: AppConfigKey[] = [
    "marker_timeout_seconds",
    "cost_alert_threshold_usd",
    "deep_research_max_iter",
    "deep_research_token_budget",
    "deep_research_max_queries",
    "lint_max_iter",
    "lint_token_budget",
  ];
  if (numericKeys.includes(configKey)) {
    const placeholderMap: Partial<Record<AppConfigKey, string>> = {
      marker_timeout_seconds:     t("config.markerTimeoutSeconds.placeholder"),
      cost_alert_threshold_usd:   t("config.costAlertThresholdUsd.placeholder"),
      deep_research_max_iter:     t("config.deepResearchMaxIter.placeholder"),
      deep_research_token_budget: t("config.deepResearchTokenBudget.placeholder"),
      deep_research_max_queries:  t("config.deepResearchMaxQueries.placeholder"),
      lint_max_iter:              t("config.lintMaxIter.placeholder"),
      lint_token_budget:          t("config.lintTokenBudget.placeholder"),
    };
    return (
      <input
        type="text"
        inputMode="decimal"
        data-testid={`rc-control-${configKey}`}
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        placeholder={placeholderMap[configKey] ?? ""}
        className="syn-input"
      />
    );
  }

  // Fallback: plain text input
  return (
    <input
      type="text"
      data-testid={`rc-control-${configKey}`}
      value={entry.localValue}
      onChange={(e) => onLocalChange(configKey, e.target.value)}
      className="syn-input"
    />
  );
}
