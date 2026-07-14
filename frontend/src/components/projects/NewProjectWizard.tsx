/**
 * NewProjectWizard.tsx — multi-step modal for creating a new Synapse vault [F1 / WS-E].
 *
 * Steps:
 *   1. Name + Parent Directory (validates both non-empty)
 *   2. AI Output Language (required; "auto" excluded)
 *   3. Scenario Template (5 cards from GET /scenarios; "general" pre-selected)
 *      → Create → POST /projects + POST /projects/{id}/activate → reload
 *
 * Accessibility: role="dialog" aria-modal, focus-trap, Esc to close, aria-labelledby.
 * Tokens: all styling via var(--syn-*) CSS variables; no hardcoded colors.
 * I3: no heavy work on each render — discrete API calls only.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { createProject, activateProject } from "../../api/projectsClient";
import { fetchScenarios, type ScenarioItem } from "../../api/scenariosClient";

// ─── Language options ─────────────────────────────────────────────────────────
// "auto" is intentionally excluded (required at create time, per spec).

const LANGUAGE_OPTIONS: { value: string; label: string }[] = [
  { value: "en", label: "English" },
  { value: "it", label: "Italiano" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "ru", label: "Русский" },
];

const DEFAULT_SCENARIO = "general";

// ─── Slug helper ──────────────────────────────────────────────────────────────

function slugify(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9-]/g, "")
      .replace(/^-+|-+$/g, "") || "vault"
  );
}

// ─── Inline style constants (mirrors FirstRunWizard / SettingsPanel tokens) ──

const BTN_PRIMARY: CSSProperties = {
  padding: "7px 18px",
  border: "1px solid var(--syn-accent)",
  borderRadius: 6,
  background: "var(--syn-accent)",
  color: "#fff",
  fontSize: 13,
  cursor: "pointer",
  fontWeight: 600,
};

const BTN_SECONDARY: CSSProperties = {
  padding: "7px 18px",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  background: "transparent",
  color: "var(--syn-text-muted)",
  fontSize: 13,
  cursor: "pointer",
};

const INPUT_STYLE: CSSProperties = {
  width: "100%",
  padding: "7px 10px",
  background: "var(--syn-bg)",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  color: "var(--syn-text)",
  fontSize: 13,
  boxSizing: "border-box",
};

const LABEL_STYLE: CSSProperties = {
  display: "block",
  fontSize: 12,
  fontWeight: 600,
  color: "var(--syn-text-muted)",
  marginBottom: 4,
};

// ─── Step progress indicator ──────────────────────────────────────────────────

type WizardStep = 1 | 2 | 3;
const TOTAL_STEPS = 3;

function StepProgress({ current }: { current: WizardStep }) {
  const { t } = useTranslation();
  const labels = [
    t("wizard.stepNameTitle"),
    t("wizard.stepLanguageTitle"),
    t("wizard.stepTemplateTitle"),
  ];
  return (
    <ol
      data-testid="np-wizard-progress"
      aria-label={t("wizard.progress.label", { current, total: TOTAL_STEPS })}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${TOTAL_STEPS}, minmax(0, 1fr))`,
        gap: 8,
        listStyle: "none",
        margin: "24px 0 0",
        padding: 0,
      }}
    >
      {Array.from({ length: TOTAL_STEPS }, (_, i) => (
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

// ─── Step 1: Name + Parent Directory ─────────────────────────────────────────

function Step1Name({
  name,
  parentDir,
  onNameChange,
  onParentDirChange,
  onNext,
  onCancel,
}: {
  name: string;
  parentDir: string;
  onNameChange: (v: string) => void;
  onParentDirChange: (v: string) => void;
  onNext: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameInputRef.current?.focus();
  }, []);

  const canAdvance = name.trim().length > 0 && parentDir.trim().length > 0;

  return (
    <div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.stepNameTitle")}
      </h3>

      <div style={{ marginBottom: 14 }}>
        <label htmlFor="np-name" style={LABEL_STYLE}>
          {t("wizard.nameLabel")}
        </label>
        <input
          ref={nameInputRef}
          id="np-name"
          type="text"
          data-testid="np-name"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder={t("wizard.namePlaceholder")}
          style={INPUT_STYLE}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>

      <div style={{ marginBottom: 20 }}>
        <label htmlFor="np-parent-dir" style={LABEL_STYLE}>
          {t("wizard.parentDirLabel")}
        </label>
        <input
          id="np-parent-dir"
          type="text"
          data-testid="np-parent-dir"
          value={parentDir}
          onChange={(e) => onParentDirChange(e.target.value)}
          placeholder={t("wizard.parentDirPlaceholder")}
          style={{ ...INPUT_STYLE, fontFamily: "monospace" }}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
        {name.trim() && parentDir.trim() && (
          <p
            style={{
              margin: "4px 0 0",
              fontSize: 11,
              color: "var(--syn-text-dim)",
              fontFamily: "monospace",
            }}
          >
            {parentDir.trim().replace(/\/+$/, "")}/{slugify(name.trim())}
          </p>
        )}
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button data-testid="np-cancel" onClick={onCancel} style={BTN_SECONDARY} type="button">
          {t("wizard.cancel")}
        </button>
        <button
          data-testid="np-next"
          onClick={onNext}
          disabled={!canAdvance}
          style={{
            ...BTN_PRIMARY,
            opacity: canAdvance ? 1 : 0.4,
            cursor: canAdvance ? "pointer" : "not-allowed",
          }}
          type="button"
        >
          {t("wizard.next")}
        </button>
      </div>
    </div>
  );
}

// ─── Step 2: AI Output Language ───────────────────────────────────────────────

function Step2Language({
  language,
  onLanguageChange,
  onNext,
  onBack,
}: {
  language: string;
  onLanguageChange: (v: string) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const selectRef = useRef<HTMLSelectElement>(null);
  const canAdvance = language !== "";

  useEffect(() => {
    selectRef.current?.focus();
  }, []);

  return (
    <div>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.stepLanguageTitle")}
      </h3>
      <p
        style={{
          margin: "0 0 20px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.languageLabel")}
      </p>

      <div style={{ marginBottom: 20 }}>
        <select
          ref={selectRef}
          id="np-language"
          data-testid="np-language"
          value={language}
          onChange={(e) => onLanguageChange(e.target.value)}
          style={{ ...INPUT_STYLE }}
          aria-required="true"
        >
          <option value="" disabled>
            — {t("wizard.languageRequired")} —
          </option>
          {LANGUAGE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button data-testid="np-back" onClick={onBack} style={BTN_SECONDARY} type="button">
          {t("wizard.back")}
        </button>
        <button
          data-testid="np-next-lang"
          onClick={onNext}
          disabled={!canAdvance}
          style={{
            ...BTN_PRIMARY,
            opacity: canAdvance ? 1 : 0.4,
            cursor: canAdvance ? "pointer" : "not-allowed",
          }}
          type="button"
        >
          {t("wizard.next")}
        </button>
      </div>
    </div>
  );
}

// ─── Step 3: Scenario template + Create ──────────────────────────────────────

function Step3Template({
  selectedScenario,
  onScenarioChange,
  onCreate,
  onBack,
  creating,
  createError,
}: {
  selectedScenario: string;
  onScenarioChange: (id: string) => void;
  onCreate: () => void;
  onBack: () => void;
  creating: boolean;
  createError: string;
}) {
  const { t } = useTranslation();
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    fetchScenarios(ac.signal)
      .then((items) => {
        setScenarios(items);
        setLoadErr(false);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setLoadErr(true);
        setLoading(false);
      });
    return () => {
      ac.abort();
    };
  }, []);

  return (
    <div>
      <h3 style={{ margin: "0 0 4px", fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
        {t("wizard.stepTemplateTitle")}
      </h3>
      <p
        style={{
          margin: "0 0 16px",
          fontSize: 13,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {t("wizard.templateDesc")}
      </p>

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>{t("common.loading")}</p>
      )}
      {loadErr && (
        <p style={{ fontSize: 12, color: "var(--syn-red)" }}>{t("settings.scenarios.loadError")}</p>
      )}

      {!loading && !loadErr && (
        <div
          data-testid="np-scenario-list"
          style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}
          role="listbox"
          aria-label={t("wizard.stepTemplateTitle")}
        >
          {scenarios.slice(0, 5).map((sc) => {
            const isSelected = sc.id === selectedScenario;
            return (
              <div
                key={sc.id}
                data-testid={`np-scenario-card-${sc.id}`}
                role="option"
                aria-selected={isSelected}
                onClick={() => onScenarioChange(sc.id)}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "10px 12px",
                  border: `2px solid ${isSelected ? "var(--syn-accent)" : "var(--syn-border)"}`,
                  borderRadius: 8,
                  background: isSelected
                    ? "color-mix(in srgb, var(--syn-accent) 8%, var(--syn-surface) 92%)"
                    : "var(--syn-surface)",
                  cursor: "pointer",
                }}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onScenarioChange(sc.id);
                  }
                }}
              >
                <div
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: "50%",
                    border: `2px solid ${isSelected ? "var(--syn-accent)" : "var(--syn-border)"}`,
                    background: isSelected ? "var(--syn-accent)" : "transparent",
                    flexShrink: 0,
                    marginTop: 2,
                  }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p
                    style={{
                      margin: "0 0 2px",
                      fontSize: 13,
                      fontWeight: 600,
                      color: "var(--syn-text)",
                    }}
                  >
                    {sc.name}
                  </p>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 11,
                      color: "var(--syn-text-muted)",
                      lineHeight: 1.5,
                    }}
                  >
                    {sc.description}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {createError && (
        <p
          role="alert"
          data-testid="np-create-error"
          style={{ fontSize: 12, color: "var(--syn-red)", margin: "0 0 12px" }}
        >
          {createError}
        </p>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          data-testid="np-back-template"
          onClick={onBack}
          disabled={creating}
          style={{
            ...BTN_SECONDARY,
            opacity: creating ? 0.4 : 1,
            cursor: creating ? "not-allowed" : "pointer",
          }}
          type="button"
        >
          {t("wizard.back")}
        </button>
        <button
          data-testid="np-create"
          onClick={onCreate}
          disabled={creating}
          style={{
            ...BTN_PRIMARY,
            opacity: creating ? 0.5 : 1,
            cursor: creating ? "not-allowed" : "pointer",
          }}
          type="button"
        >
          {creating ? t("wizard.creating") : t("wizard.create")}
        </button>
      </div>
    </div>
  );
}

// ─── Wizard overlay ───────────────────────────────────────────────────────────

const DIALOG_TITLE_ID = "new-project-wizard-title";

export interface NewProjectWizardProps {
  onClose: () => void;
}

export function NewProjectWizard({ onClose }: NewProjectWizardProps): ReactNode {
  const { t } = useTranslation();
  const [step, setStep] = useState<WizardStep>(1);
  const [name, setName] = useState("");
  const [parentDir, setParentDir] = useState("");
  const [language, setLanguage] = useState("");
  const [scenario, setScenario] = useState(DEFAULT_SCENARIO);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  // Focus the title on each step change.
  useEffect(() => {
    titleRef.current?.focus();
  }, [step]);

  // Esc closes the wizard.
  const handleClose = useCallback(() => {
    if (!creating) onClose();
  }, [creating, onClose]);

  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleClose();
        return;
      }
      // Focus trap.
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
    [handleClose],
  );

  const handleCreate = useCallback(async () => {
    setCreateError("");
    setCreating(true);
    const vaultPath = parentDir.trim().replace(/\/+$/, "") + "/" + slugify(name.trim());
    try {
      const project = await createProject(name.trim(), vaultPath, {
        scenario,
        output_language: language,
      });
      await activateProject(project.id);
      window.location.reload();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("wizard.createError");
      setCreateError(msg);
      setCreating(false);
    }
  }, [name, parentDir, language, scenario, t]);

  return (
    <div
      data-testid="np-wizard-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={DIALOG_TITLE_ID}
        data-testid="np-wizard-dialog"
        onKeyDown={handleKeyDown}
        style={{
          background: "var(--syn-bg-card)",
          border: "1px solid var(--syn-border)",
          borderRadius: 10,
          boxShadow: "0 8px 40px rgba(0,0,0,0.35)",
          padding: "28px 32px",
          width: "min(560px, calc(100vw - 40px))",
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
              {t("launcher.newTitle")}
            </p>
            <h2
              ref={titleRef}
              id={DIALOG_TITLE_ID}
              tabIndex={-1}
              style={{ margin: 0, fontSize: 17, fontWeight: 800, color: "var(--syn-text)" }}
            >
              {t("wizard.newProjectTitle")}
            </h2>
          </div>
          <button
            data-testid="np-close-x"
            aria-label={t("wizard.cancel")}
            onClick={handleClose}
            disabled={creating}
            style={{
              background: "transparent",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              color: "var(--syn-text-dim)",
              fontSize: 16,
              cursor: creating ? "not-allowed" : "pointer",
              padding: "2px 8px",
              lineHeight: 1.4,
              flexShrink: 0,
              opacity: creating ? 0.4 : 1,
            }}
          >
            ×
          </button>
        </div>

        {/* Step content */}
        <div style={{ flex: 1 }}>
          {step === 1 && (
            <Step1Name
              name={name}
              parentDir={parentDir}
              onNameChange={setName}
              onParentDirChange={setParentDir}
              onNext={() => setStep(2)}
              onCancel={handleClose}
            />
          )}
          {step === 2 && (
            <Step2Language
              language={language}
              onLanguageChange={setLanguage}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}
          {step === 3 && (
            <Step3Template
              selectedScenario={scenario}
              onScenarioChange={setScenario}
              onCreate={() => {
                void handleCreate();
              }}
              onBack={() => setStep(2)}
              creating={creating}
              createError={createError}
            />
          )}
        </div>

        <StepProgress current={step} />
      </div>
    </div>
  );
}

// Export isDirty check for tests
export { slugify };
