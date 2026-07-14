/**
 * FirstRunWizard.tsx — guided first-run setup wizard (A2.2, AC-R11-2-13/14).
 *
 * Shows automatically until the versioned setup state is explicitly completed.
 * Seeded provider rows are not treated as readiness evidence.
 *
 * Can be re-opened from Settings "Getting started" via the onOpen callback
 * that SettingsPanel calls into AppShell.
 *
 * Steps (bounded and resumable):
 *   Step 1 — Connect & verify: confirm backend URL is reachable (reuses the
 *             /status health probe via apiFetch).
 *   Step 2 — Choose inference provider + model: reuses the SAME
 *             createProviderConfig / addProvider mechanism as SectionLlmModels
 *             in SettingsPanel (endpoint: POST /provider/config).
 *   Step 3 — Choose PDF extractor (pypdf vs Marker + URL): reuses putAppConfig
 *             (PUT /config/app/{key}) — same as SectionRuntimeConfig.
 *   Step 4 — truthful readiness summary. Incomplete checks remain deferred.
 *
 * PERSISTENCE CONTRACT (ADR-0053 §5):
 *   - Provider step → POST /provider/config (createProviderConfig)
 *   - PDF step → PUT /config/app/pdf_extractor, PUT /config/app/marker_service_url
 *   - Setup state → localStorage["synapse.setupState"] (versioned)
 *   - Legacy completed flags are migrated without breaking existing users.
 *   NO other persistence path. A Vitest spy confirms this (AC-R11-2-13).
 *
 * UI CONTRACT:
 *   - role="dialog" aria-modal="true", focus-trap, Esc = skip/dismiss.
 *   - prefers-reduced-motion: CSS transitions respected via CSS var.
 *   - All var(--syn-*) tokens, dark-mode-safe.
 *   - Uses .syn-btn / .syn-btn--primary / .syn-btn--secondary classes + inline overrides.
 *   - Never mounted over ConnectScreen (AppShell gates this after server is connected).
 *
 * I3: no per-token work; only local state + discrete API calls.
 * I6: no hardcoded provider/model IDs; all values from user input.
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import {
  apiBase,
  setServerUrl,
  clearServerUrl,
  clearAuthToken,
  getLastServerUrl,
  platformFetch,
} from "../../api/base";
import { putAppConfig } from "../../api/appConfigClient";
import { createProviderConfig, testProviderConnection } from "../../api/providerClient";
import type { CreateProviderConfigBody } from "../../api/types";
import { completeSetup, deferSetup, readSetupState, type SetupStep } from "./setupState";
import { providerVerificationFingerprint } from "./providerVerification";
import { useStatusStore } from "../../store/statusStore";
import { useProviderStore } from "../../store/providerStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";

// ─── Backwards-compatible setup helpers ─────────────────────────────────────

export function getSetupCompleted(): boolean {
  return readSetupState().status === "completed";
}

export function markSetupCompleted(): void {
  completeSetup();
}

// ─── Hook: first-run detection ────────────────────────────────────────────────

/**
 * useFirstRunSetup — determines whether the wizard should show automatically.
 *
 * Provider row count is retained in the signature for caller compatibility, but
 * readiness is explicit: seeded rows may still lack credentials or connectivity.
 */
export function useFirstRunSetup(providerListLength: number): {
  shouldShow: boolean;
  markDone: () => void;
  defer: (lastStep: WizardStep) => void;
} {
  const [flagChecked, setFlagChecked] = useState(false);
  const [flagSet, setFlagSet] = useState(false);
  const [dismissedForSession, setDismissedForSession] = useState(false);

  useEffect(() => {
    const done = getSetupCompleted();
    setFlagSet(done);
    setFlagChecked(true);
  }, []);

  const markDone = useCallback(() => {
    markSetupCompleted();
    setFlagSet(true);
  }, []);

  const defer = useCallback((lastStep: WizardStep) => {
    deferSetup(lastStep);
    setDismissedForSession(true);
  }, []);

  // Seed rows do not prove that the provider is reachable or credentialed.
  // Keep the argument for backwards-compatible callers while completion moves
  // to an explicit, versioned state.
  void providerListLength;
  const shouldShow = flagChecked && !flagSet && !dismissedForSession;

  return { shouldShow, markDone, defer };
}

// ─── Types ────────────────────────────────────────────────────────────────────

type WizardStep = SetupStep;
export type WizardOutcome = "completed" | "deferred";

interface WizardProps {
  /** Reports a truthful outcome rather than treating every dismissal as done. */
  onClose: (outcome: WizardOutcome, lastStep: WizardStep) => void;
  /** Explicit recovery entry point; omitted to resume the persisted setup step. */
  initialStep?: SetupStep | undefined;
}

// ─── Inline style constants ───────────────────────────────────────────────────
// Button definitions consolidated into CSS classes (F1 slice):
//   BTN_PRIMARY  → .syn-btn.syn-btn--accent-ghost (accent-soft bg, accent border/text)
//   BTN_SECONDARY → .syn-btn.syn-btn--ghost (borderless, dim text)
// Both accept an optional style override for layout (marginLeft, opacity, etc.).

const INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  padding: "7px 10px",
  background: "var(--syn-bg)",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  color: "var(--syn-text)",
  fontSize: 13,
  boxSizing: "border-box",
};

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepProgress({ current, total }: { current: WizardStep; total: number }) {
  const { t } = useTranslation();
  const labels = [
    t("wizard.progress.connection"),
    t("wizard.progress.provider"),
    t("wizard.progress.pdf"),
    t("wizard.progress.finish"),
  ];
  return (
    <ol
      data-testid="wizard-progress"
      aria-label={t("wizard.progress.label", { current, total })}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${total}, minmax(0, 1fr))`,
        gap: 8,
        listStyle: "none",
        margin: "24px 0 0",
        padding: 0,
      }}
    >
      {Array.from({ length: total }, (_, i) => (
        <li
          key={i}
          aria-current={i + 1 === current ? "step" : undefined}
          style={{
            borderTop: `3px solid ${i + 1 <= current ? "var(--syn-accent)" : "var(--syn-border)"}`,
            paddingTop: 6,
            color: i + 1 === current ? "var(--syn-text)" : "var(--syn-text-dim)",
            fontSize: 10,
            fontWeight: i + 1 === current ? 650 : 500,
            lineHeight: 1.25,
          }}
        >
          {labels[i]}
        </li>
      ))}
    </ol>
  );
}

// ─── Step 1: Connect & verify ─────────────────────────────────────────────────

function Step1Connect({
  onNext,
  onSkip,
  onVerified,
}: {
  onNext: () => void;
  onSkip: () => void;
  onVerified: () => void;
}) {
  const { t } = useTranslation();
  // Editable backend URL — prefilled with the currently-resolved base (or the
  // last successfully-connected URL). Blank = same-origin / relative (web/PWA).
  const [serverUrl, setServerUrlInput] = useState<string>(
    () => apiBase() || getLastServerUrl() || "",
  );
  const [status, setStatus] = useState<"idle" | "checking" | "ok" | "error">("idle");
  const [errMsg, setErrMsg] = useState("");

  // Same-origin placeholder hint (runtime, not i18n).
  const originHint =
    typeof window !== "undefined" && window.location?.origin
      ? window.location.origin
      : "http://truenas:8000";

  const handleCheck = async () => {
    setErrMsg("");
    const trimmed = serverUrl.trim().replace(/\/+$/, "");

    // Validate scheme when a URL is provided; blank means same-origin (relative).
    let probeBase = "";
    if (trimmed.length > 0) {
      let parsed: URL;
      try {
        parsed = new URL(trimmed);
      } catch {
        setStatus("error");
        setErrMsg(t("connect.errors.invalidUrl"));
        return;
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        setStatus("error");
        setErrMsg(t("connect.errors.scheme"));
        return;
      }
      probeBase = trimmed;
    }

    setStatus("checking");
    try {
      // Never inherit the current server's bearer token when probing an
      // editable candidate host.
      const res = await platformFetch(`${probeBase}/status`);
      if (res.ok) {
        // Persist the entered URL so every later call (provider/config, app config,
        // and the rest of the app) targets this backend. Blank reverts to same-origin.
        if (trimmed !== apiBase()) {
          clearAuthToken();
        }
        if (trimmed.length > 0) {
          setServerUrl(trimmed);
        } else {
          clearServerUrl();
        }
        setStatus("ok");
        onVerified();
      } else {
        setStatus("error");
        setErrMsg(t("wizard.step1.errNotOk", { status: res.status }));
      }
    } catch (e: unknown) {
      setStatus("error");
      setErrMsg(e instanceof Error ? e.message : t("wizard.step1.errUnknown"));
    }
  };

  return (
    <div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.step1.title")}
      </h3>
      <p
        style={{
          margin: "0 0 20px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.step1.desc")}
      </p>

      {/* Editable backend URL */}
      <label
        htmlFor="wizard-step1-url"
        style={{
          display: "block",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
          marginBottom: 4,
        }}
      >
        {t("connect.urlLabel")}
      </label>
      <input
        id="wizard-step1-url"
        type="text"
        data-testid="wizard-step1-url"
        value={serverUrl}
        onChange={(e) => {
          setServerUrlInput(e.target.value);
          // Editing invalidates a prior OK/error result.
          if (status !== "idle") setStatus("idle");
          setErrMsg("");
        }}
        placeholder={originHint}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        style={{ ...INPUT_STYLE, fontFamily: "monospace", marginBottom: 4 }}
      />
      <p
        style={{
          margin: "0 0 16px",
          fontSize: 11,
          color: "var(--syn-text-dim)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.step1.urlHelp")}
      </p>

      {status === "ok" && (
        <div
          data-testid="wizard-step1-ok"
          style={{
            marginBottom: 16,
            padding: "6px 12px",
            background:
              "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base, transparent) 92%)",
            border: "1px solid color-mix(in srgb, var(--syn-green) 30%, transparent 70%)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--syn-green)",
          }}
        >
          {t("wizard.step1.ok")}
        </div>
      )}

      {status === "error" && (
        <div
          data-testid="wizard-step1-error"
          style={{
            marginBottom: 16,
            padding: "6px 12px",
            background:
              "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base, transparent) 92%)",
            border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--syn-red)",
          }}
        >
          {errMsg}
        </div>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          data-testid="wizard-step1-check"
          onClick={() => {
            void handleCheck();
          }}
          disabled={status === "checking"}
          className="syn-btn syn-btn--accent-ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {status === "checking" ? t("wizard.step1.checking") : t("wizard.step1.check")}
        </button>

        {status === "ok" && (
          <button
            data-testid="wizard-step1-next"
            onClick={onNext}
            className="syn-btn syn-btn--accent-ghost"
            style={{ fontSize: 13, padding: "7px 18px" }}
          >
            {t("wizard.next")}
          </button>
        )}

        {/* Allow advancing even if not checked — backend may be same-origin */}
        {status !== "ok" && status !== "checking" && (
          <button
            data-testid="wizard-step1-skip-check"
            onClick={onNext}
            className="syn-btn syn-btn--ghost"
            style={{ fontSize: 13, padding: "7px 18px" }}
          >
            {t("wizard.skipStep")}
          </button>
        )}

        <button
          data-testid="wizard-skip"
          onClick={onSkip}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px", marginLeft: "auto" }}
        >
          {t("wizard.skipAll")}
        </button>
      </div>
    </div>
  );
}

// ─── Step 2: Choose inference provider + model ────────────────────────────────
// Reuses the SAME POST /provider/config endpoint as SectionLlmModels (providerClient.createProviderConfig).

function Step2Provider({
  onNext,
  onBack,
  onSkip,
  onVerified,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  onVerified: (fingerprint: string) => void;
}) {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);
  const [providerType, setProviderType] = useState<"api" | "local" | "cli">("api");
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    const body: CreateProviderConfigBody = {
      scope: "global",
      vault_id: null,
      provider_type: providerType,
      model_id: modelId.trim() || null,
      base_url: baseUrl.trim() || null,
      ...(providerType === "api" && apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    };
    try {
      const probe = await testProviderConnection({
        provider_type: providerType,
        model: modelId.trim(),
        base_url: baseUrl.trim() || null,
        ...(providerType === "api" && apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      if (!probe.ok) {
        throw new Error(probe.detail || t("wizard.step2.probeFailed"));
      }
      const createdProvider = await createProviderConfig(body);
      await useProviderStore.getState().fetchList();
      useProviderStore.getState().deriveActive(vaultId);
      setSaved(true);
      onVerified(providerVerificationFingerprint(createdProvider));
      // Advance after a brief moment to let user see success
      setTimeout(onNext, 400);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("wizard.step2.err"));
      setSaving(false);
    }
  };

  const modelIdPlaceholder =
    providerType === "local"
      ? t("settings.llmModels.modelIdPlaceholderLocal")
      : providerType === "cli"
        ? t("settings.llmModels.modelIdPlaceholderCli")
        : t("settings.llmModels.modelIdPlaceholder");

  const baseUrlPlaceholder =
    providerType === "local"
      ? t("settings.llmModels.baseUrlPlaceholderLocal")
      : t("settings.llmModels.baseUrlPlaceholder");

  return (
    <div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.step2.title")}
      </h3>
      <p
        style={{
          margin: "0 0 20px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.step2.desc")}
      </p>

      {/* Provider type */}
      <label
        style={{
          display: "block",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
          marginBottom: 4,
        }}
      >
        {t("settings.llmModels.providerType")}
      </label>
      <select
        data-testid="wizard-step2-type"
        value={providerType}
        onChange={(e) => setProviderType(e.target.value as typeof providerType)}
        style={{ ...INPUT_STYLE, marginBottom: 14 }}
      >
        <option value="api">API (Anthropic / OpenAI-compat)</option>
        <option value="local">Local (Ollama)</option>
        <option value="cli">CLI (claude-agent-sdk)</option>
      </select>

      {/* Model ID */}
      <label
        style={{
          display: "block",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
          marginBottom: 4,
        }}
      >
        {t("settings.llmModels.modelId")}
      </label>
      <input
        type="text"
        data-testid="wizard-step2-model"
        value={modelId}
        onChange={(e) => setModelId(e.target.value)}
        placeholder={modelIdPlaceholder}
        style={{ ...INPUT_STYLE, marginBottom: 14 }}
      />

      {/* Base URL (api + local only) */}
      {(providerType === "api" || providerType === "local") && (
        <>
          <label
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("settings.llmModels.baseUrl")}
          </label>
          <input
            type="text"
            data-testid="wizard-step2-baseurl"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={baseUrlPlaceholder}
            style={{ ...INPUT_STYLE, marginBottom: 14 }}
          />
        </>
      )}

      {providerType === "api" && (
        <>
          <label
            htmlFor="wizard-step2-api-key"
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("settings.llmModels.apiKeyLabel")}
          </label>
          <input
            id="wizard-step2-api-key"
            type="password"
            data-testid="wizard-step2-api-key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={t("settings.llmModels.apiKeyPlaceholder")}
            autoComplete="new-password"
            style={{ ...INPUT_STYLE, marginBottom: 14 }}
          />
        </>
      )}

      {err && (
        <p
          role="alert"
          style={{
            fontSize: 12,
            color: "var(--syn-red)",
            margin: "0 0 12px",
          }}
        >
          {err}
        </p>
      )}

      {saved && (
        <p
          data-testid="wizard-step2-saved"
          style={{ fontSize: 12, color: "var(--syn-green)", margin: "0 0 12px" }}
        >
          {t("wizard.step2.saved")}
        </p>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          data-testid="wizard-back"
          onClick={onBack}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {t("wizard.back")}
        </button>
        <button
          data-testid="wizard-step2-save"
          onClick={() => {
            void handleSave();
          }}
          disabled={saving || modelId.trim() === ""}
          title={modelId.trim() === "" ? t("settings.llmModels.modelIdRequired") : undefined}
          className="syn-btn syn-btn--accent-ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {saving ? t("wizard.saving") : t("wizard.step2.save")}
        </button>
        <button
          data-testid="wizard-step2-skip"
          onClick={onNext}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {t("wizard.skipStep")}
        </button>
        <button
          data-testid="wizard-skip"
          onClick={onSkip}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px", marginLeft: "auto" }}
        >
          {t("wizard.skipAll")}
        </button>
      </div>
    </div>
  );
}

// ─── Step 3: PDF extractor ────────────────────────────────────────────────────
// Reuses putAppConfig (PUT /config/app/{key}) — same as SectionRuntimeConfig.

function Step3Pdf({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  const { t } = useTranslation();
  const [extractor, setExtractor] = useState<"pypdf" | "marker">("pypdf");
  const [markerUrl, setMarkerUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    try {
      await putAppConfig("pdf_extractor", extractor);
      if (extractor === "marker" && markerUrl.trim()) {
        await putAppConfig("marker_service_url", markerUrl.trim());
      }
      setSaved(true);
      setTimeout(onNext, 400);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : t("wizard.step3.err"));
      setSaving(false);
    }
  };

  return (
    <div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.step3.title")}
      </h3>
      <p
        style={{
          margin: "0 0 20px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.step3.desc")}
      </p>

      <label
        style={{
          display: "block",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
          marginBottom: 4,
        }}
      >
        {t("config.pdfExtractor.label")}
      </label>
      <select
        data-testid="wizard-step3-extractor"
        value={extractor}
        onChange={(e) => setExtractor(e.target.value as typeof extractor)}
        style={{ ...INPUT_STYLE, marginBottom: 14 }}
      >
        <option value="pypdf">{t("config.pdfExtractor.optionPypdf")}</option>
        <option value="marker">{t("config.pdfExtractor.optionMarker")}</option>
      </select>

      {extractor === "marker" && (
        <>
          <label
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("config.markerServiceUrl.label")}
          </label>
          <input
            type="text"
            data-testid="wizard-step3-markerurl"
            value={markerUrl}
            onChange={(e) => setMarkerUrl(e.target.value)}
            placeholder={t("config.markerServiceUrl.placeholder")}
            style={{ ...INPUT_STYLE, marginBottom: 14 }}
          />
          <p
            style={{
              fontSize: 11,
              color: "var(--syn-text-dim)",
              margin: "0 0 14px",
              lineHeight: 1.5,
            }}
          >
            {t("config.markerServiceUrl.help")}
          </p>
        </>
      )}

      {err && <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "0 0 12px" }}>{err}</p>}
      {saved && (
        <p
          data-testid="wizard-step3-saved"
          style={{ fontSize: 12, color: "var(--syn-green)", margin: "0 0 12px" }}
        >
          {t("wizard.step3.saved")}
        </p>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          data-testid="wizard-back"
          onClick={onBack}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {t("wizard.back")}
        </button>
        <button
          data-testid="wizard-step3-save"
          onClick={() => {
            void handleSave();
          }}
          disabled={saving}
          className="syn-btn syn-btn--accent-ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {saving ? t("wizard.saving") : t("wizard.step3.save")}
        </button>
        <button
          data-testid="wizard-step3-skip"
          onClick={onNext}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px" }}
        >
          {t("wizard.skipStep")}
        </button>
        <button
          data-testid="wizard-skip"
          onClick={onSkip}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 13, padding: "7px 18px", marginLeft: "auto" }}
        >
          {t("wizard.skipAll")}
        </button>
      </div>
    </div>
  );
}

// ─── Step 4: Done ─────────────────────────────────────────────────────────────

function Step4Done({ onClose, ready }: { onClose: () => void; ready: boolean }) {
  const { t } = useTranslation();
  return (
    <div style={{ textAlign: "center" }}>
      <div
        aria-hidden="true"
        style={{
          width: 48,
          height: 48,
          borderRadius: "50%",
          background:
            "color-mix(in srgb, var(--syn-green) 12%, var(--syn-mix-base, transparent) 88%)",
          border: "2px solid color-mix(in srgb, var(--syn-green) 40%, transparent 60%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 16px",
          fontSize: 24,
        }}
      >
        ✓
      </div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {ready ? t("wizard.step4.title") : t("wizard.step4.deferredTitle")}
      </h3>
      <p
        style={{
          margin: "0 0 24px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {ready ? t("wizard.step4.desc") : t("wizard.step4.deferredDesc")}
      </p>
      <button
        data-testid="wizard-done"
        onClick={onClose}
        className="syn-btn syn-btn--accent-ghost"
        style={{ fontSize: 13, padding: "7px 18px" }}
      >
        {ready ? t("wizard.step4.cta") : t("wizard.step4.deferredCta")}
      </button>
    </div>
  );
}

// ─── Wizard overlay ────────────────────────────────────────────────────────────

const TOTAL_STEPS = 4;
const DIALOG_TITLE_ID = "first-run-wizard-title";

export function FirstRunWizard({ onClose, initialStep }: WizardProps): ReactNode {
  const { t } = useTranslation();
  const [initialSetup] = useState(readSetupState);
  const [step, setStep] = useState<WizardStep>(
    () => initialStep ?? (initialSetup.status === "deferred" ? initialSetup.lastStep : 1),
  );
  const [connectionVerified, setConnectionVerified] = useState(initialSetup.connectionVerified);
  const [providerVerified, setProviderVerified] = useState(initialSetup.providerVerified);
  const [providerFingerprint, setProviderFingerprint] = useState(initialSetup.providerFingerprint);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const ready = connectionVerified && providerVerified;
  const resumeStep: WizardStep = !connectionVerified ? 1 : !providerVerified ? 2 : 3;

  const deferAndClose = useCallback(() => {
    const deferredStep = step === 4 && !ready ? resumeStep : step;
    deferSetup(deferredStep, { connectionVerified, providerVerified, providerFingerprint });
    onClose("deferred", deferredStep);
  }, [connectionVerified, onClose, providerFingerprint, providerVerified, ready, resumeStep, step]);

  // Focus management: move focus into the dialog on mount.
  useEffect(() => {
    titleRef.current?.focus();
  }, [step]); // re-run on each step change

  // Esc closes the wizard (skip/dismiss).
  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        deferAndClose();
      }
      // Focus trap inside dialog.
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ),
        );
        if (focusable.length === 0) return;
        const first = focusable[0] as HTMLElement;
        const last = focusable[focusable.length - 1] as HTMLElement;
        const active = document.activeElement as HTMLElement;
        if (active === titleRef.current) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
          return;
        }
        if (e.shiftKey) {
          if (active === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (active === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    },
    [deferAndClose],
  );

  const goNext = useCallback(() => {
    if (step < TOTAL_STEPS) {
      setStep((s) => (s + 1) as WizardStep);
    } else {
      onClose(ready ? "completed" : "deferred", step);
    }
  }, [step, onClose, ready]);

  const goBack = useCallback(() => {
    if (step > 1) {
      setStep((s) => (s - 1) as WizardStep);
    }
  }, [step]);

  return (
    /* Backdrop — click outside closes (skip) */
    <div
      data-testid="wizard-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) deferAndClose();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        // prefers-reduced-motion: no transform animation needed here; only transitions
        // inside child elements should be guarded by the CSS media query.
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={DIALOG_TITLE_ID}
        data-testid="wizard-dialog"
        onKeyDown={handleKeyDown}
        style={{
          background: "var(--syn-bg-card)",
          border: "1px solid var(--syn-border)",
          borderRadius: 10,
          boxShadow: "0 8px 40px rgba(0,0,0,0.35)",
          padding: "28px 32px",
          width: "min(520px, calc(100vw - 40px))",
          maxHeight: "calc(100vh - 80px)",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 0,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            marginBottom: 24,
          }}
        >
          <div>
            <p
              style={{
                margin: "0 0 4px",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: "var(--syn-text-dim)",
              }}
            >
              {t("wizard.label")}
            </p>
            <h2
              ref={titleRef}
              id={DIALOG_TITLE_ID}
              tabIndex={-1}
              style={{ margin: 0, fontSize: 17, fontWeight: 800, color: "var(--syn-text)" }}
            >
              {t("wizard.title")}
            </h2>
          </div>
          <button
            data-testid="wizard-close-x"
            aria-label={t("wizard.skipAll")}
            onClick={deferAndClose}
            style={{
              background: "transparent",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              color: "var(--syn-text-dim)",
              fontSize: 16,
              cursor: "pointer",
              padding: "2px 8px",
              lineHeight: 1.4,
              flexShrink: 0,
            }}
          >
            ×
          </button>
        </div>

        {/* Step content */}
        <div style={{ flex: 1 }}>
          {step === 1 && (
            <Step1Connect
              onNext={goNext}
              onSkip={deferAndClose}
              onVerified={() => {
                setConnectionVerified(true);
                useStatusStore.getState().setConnectionState("online");
              }}
            />
          )}
          {step === 2 && (
            <Step2Provider
              onNext={goNext}
              onBack={goBack}
              onSkip={deferAndClose}
              onVerified={(fingerprint) => {
                setProviderVerified(true);
                setProviderFingerprint(fingerprint);
              }}
            />
          )}
          {step === 3 && <Step3Pdf onNext={goNext} onBack={goBack} onSkip={deferAndClose} />}
          {step === 4 && (
            <Step4Done
              ready={ready}
              onClose={() => {
                if (!ready) {
                  deferSetup(resumeStep, {
                    connectionVerified,
                    providerVerified,
                    providerFingerprint,
                  });
                } else {
                  completeSetup({
                    connectionVerified,
                    providerVerified,
                    providerFingerprint,
                  });
                }
                onClose(ready ? "completed" : "deferred", ready ? 4 : resumeStep);
              }}
            />
          )}
        </div>

        <StepProgress current={step} total={TOTAL_STEPS} />
      </div>
    </div>
  );
}
