/**
 * SectionScenarios.tsx — scenario templates (R7-1 FE).
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { Button } from "../../ui/Button";
import { fetchScenarios, applyScenario, type ScenarioItem } from "../../../api/scenariosClient";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { showToast } from "../../common/Toast";

export function SectionScenarios() {
  const { t } = useTranslation();
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([]);
  const [loadErr, setLoadErr] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pendingScenario, setPendingScenario] = useState<ScenarioItem | null>(null);
  const [applying, setApplying] = useState(false);

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

  const handleApplyConfirm = async () => {
    if (!pendingScenario) return;
    const scenario = pendingScenario;
    setPendingScenario(null);
    setApplying(true);
    try {
      await applyScenario(scenario.id);
      showToast(t("settings.scenarios.applied"), "success");
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : t("settings.scenarios.loadError"), "error");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div>
      <SectionHeader title={t("settings.scenarios.title")} desc={t("settings.scenarios.desc")} />

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>{t("common.loading")}</p>
      )}
      {loadErr && (
        <p style={{ fontSize: 12, color: "var(--syn-red)" }}>{t("settings.scenarios.loadError")}</p>
      )}
      {!loading && !loadErr && scenarios.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
          {t("settings.scenarios.loadError")}
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {scenarios.slice(0, 5).map((sc) => (
          <div
            key={sc.id}
            data-testid="scenario-card"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              padding: "12px 14px",
              border: "1px solid var(--syn-border)",
              borderRadius: 8,
              background: "var(--syn-surface)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <p
                style={{
                  margin: "0 0 4px",
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--syn-text)",
                }}
              >
                {sc.name}
              </p>
              <p
                style={{ margin: 0, fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}
              >
                {sc.description}
              </p>
            </div>
            <Button
              variant="accent-ghost"
              data-testid="scenario-apply-btn"
              style={{ flexShrink: 0 }}
              disabled={applying}
              onClick={() => setPendingScenario(sc)}
            >
              {applying ? t("settings.scenarios.applying") : t("settings.scenarios.apply")}
            </Button>
          </div>
        ))}
      </div>

      {pendingScenario && (
        <ConfirmDialog
          title={t("settings.scenarios.applyConfirmTitle")}
          body={t("settings.scenarios.applyConfirmBody", { name: pendingScenario.name })}
          confirmLabel={t("settings.scenarios.applyConfirm")}
          cancelLabel={t("settings.scenarios.applyCancel")}
          onConfirm={() => {
            void handleApplyConfirm();
          }}
          onCancel={() => setPendingScenario(null)}
        />
      )}
    </div>
  );
}
