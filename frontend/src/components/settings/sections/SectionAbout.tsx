/**
 * SectionAbout.tsx — version + links.
 * Extracted from SettingsPanel monolith (ADR-0055).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { PRODUCT_IDENTITY } from "../../../config/productIdentity";
import { fetchUpdateStatus, triggerSystemUpdate, type UpdateStatus } from "../../../api/opsClient";

declare const __APP_VERSION__: string;

export function SectionAbout() {
  const { t } = useTranslation();

  // Deployment update state (R12-3, B1). Best-effort: the section stays hidden if the check fails.
  const [upd, setUpd] = useState<UpdateStatus | null>(null);
  const [action, setAction] = useState<"idle" | "triggering" | "triggered" | "error">("idle");
  const [actionMsg, setActionMsg] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    void fetchUpdateStatus(ctrl.signal)
      .then(setUpd)
      .catch(() => {
        /* update check is best-effort — leave the section hidden on failure */
      });
    return () => ctrl.abort();
  }, []);

  const onUpdate = () => {
    setAction("triggering");
    void triggerSystemUpdate()
      .then((r) => {
        setAction("triggered");
        setActionMsg(r.message);
      })
      .catch((e: unknown) => {
        // Watchtower recreates the backend, so a network drop right after the 202 is expected —
        // but a real error (e.g. 501/502) surfaces here.
        setAction("error");
        setActionMsg(e instanceof Error ? e.message : String(e));
      });
  };

  return (
    <div>
      <SectionHeader
        title={t("settings.nav.about")}
        desc={`${PRODUCT_IDENTITY.displayName} — ${PRODUCT_IDENTITY.descriptor}`}
      />

      <p
        style={{
          margin: "0 0 20px",
          color: "var(--syn-text-muted)",
          fontFamily: "var(--syn-font-wordmark)",
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        {PRODUCT_IDENTITY.tagline}
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "8px 16px",
          fontSize: 12,
          marginBottom: 24,
        }}
      >
        <span style={{ color: "var(--syn-text-dim)" }}>{t("settings.about.version")}</span>
        <span style={{ color: "var(--syn-text)", fontFamily: "var(--syn-font-mono)" }}>
          v{__APP_VERSION__}
        </span>
      </div>

      {upd?.update_available && (
        <div
          data-testid="settings-update-status"
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
            padding: "12px 14px",
            marginBottom: 24,
            borderRadius: 10,
            border: "1px solid var(--syn-accent)",
            background: "var(--syn-accent-subtle, rgba(37, 99, 235, 0.08))",
          }}
        >
          <span style={{ fontSize: 12.5, color: "var(--syn-text)" }}>
            {t("settings.about.updateAvailable", { version: upd.latest_version })}
          </span>
          {upd.update_supported ? (
            action === "triggered" ? (
              <span style={{ fontSize: 12, color: "var(--syn-success)" }}>
                {t("settings.about.updateStarted")}
              </span>
            ) : (
              <button
                data-testid="settings-update-button"
                type="button"
                onClick={onUpdate}
                disabled={action === "triggering"}
                style={{
                  padding: "6px 14px",
                  borderRadius: 8,
                  border: "none",
                  background: "var(--syn-accent)",
                  color: "#fff",
                  fontSize: 12.5,
                  fontWeight: 600,
                  cursor: action === "triggering" ? "default" : "pointer",
                  opacity: action === "triggering" ? 0.7 : 1,
                }}
              >
                {action === "triggering"
                  ? t("settings.about.updateStarting")
                  : t("settings.about.updateNow")}
              </button>
            )
          ) : (
            <span style={{ fontSize: 11.5, color: "var(--syn-text-muted)" }}>
              {t("settings.about.updateManual")}
            </span>
          )}
          {action === "error" && (
            <span style={{ fontSize: 11.5, color: "var(--syn-danger)", width: "100%" }}>
              {actionMsg}
            </span>
          )}
        </div>
      )}
      {upd && !upd.update_available && upd.latest_version !== null && (
        <p
          data-testid="settings-update-uptodate"
          style={{ margin: "0 0 24px", fontSize: 11.5, color: "var(--syn-text-dim)" }}
        >
          {t("settings.about.upToDate")}
        </p>
      )}

      <p
        style={{
          margin: "0 0 8px",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--syn-text-dim)",
        }}
      >
        {t("settings.about.links")}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <a
          href="https://github.com/nashsu/llm_wiki"
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 12, color: "var(--syn-accent)", textDecoration: "none" }}
        >
          {t("settings.about.github")} ↗
        </a>
      </div>
    </div>
  );
}
