/**
 * SectionEmbeddings.tsx — vector embeddings status (ADR-0030).
 * Extracted from SettingsPanel monolith (ADR-0055).
 * ADR-0030: embeddings_enabled is a read-only ENV flag — NOT an interactive toggle.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, EmbedRow } from "../ui";
import { fetchEmbeddingConfig, type EmbeddingConfig } from "../../../api/providerClient";

export function SectionEmbeddings() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<EmbeddingConfig | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchEmbeddingConfig(ac.signal)
      .then((data) => { setCfg(data); setErr(false); })
      .catch((e: unknown) => { if (!(e instanceof Error) || e.name !== "AbortError") setErr(true); });
    return () => { ac.abort(); };
  }, []);

  return (
    <div>
      <SectionHeader title={t("settings.nav.embeddings")} desc={t("settings.embeddings.desc")} />
      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.embeddings.error")}</p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.embeddings.loading")}</p>
      ) : cfg.embeddings_enabled ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div
            data-testid="embeddings-status-active"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
              borderRadius: 6,
              fontSize: 12,
              color: "var(--syn-green)",
              fontWeight: 600,
            }}
          >
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--syn-green)", flexShrink: 0, display: "inline-block" }} />
            {t("settings.embeddings.semanticActive")}
          </div>
          <EmbedRow label={t("settings.embeddings.urlLabel")} value={cfg.embedding_url} mono />
          <EmbedRow label={t("settings.embeddings.modelLabel")} value={cfg.embedding_model} mono />
          <EmbedRow label={t("settings.embeddings.dimLabel")} value={String(cfg.embedding_dim)} />
          <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: "4px 0 0", lineHeight: 1.5 }}>
            {t("settings.embeddings.envNote")}
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            data-testid="embeddings-status-lexical"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)",
              borderRadius: 6,
              fontSize: 12,
              color: "var(--syn-amber)",
              fontWeight: 600,
            }}
          >
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--syn-amber)", flexShrink: 0, display: "inline-block" }} />
            {t("settings.embeddings.lexicalOnly")}
          </div>
          <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: 0, lineHeight: 1.6 }}>
            {t("settings.embeddings.lexicalOnlyNote")}
          </p>
          <div style={{ opacity: 0.45 }}>
            <EmbedRow label={t("settings.embeddings.urlLabel")} value={cfg.embedding_url} mono />
            <div style={{ marginTop: 10 }}>
              <EmbedRow label={t("settings.embeddings.modelLabel")} value={cfg.embedding_model} mono />
            </div>
            <div style={{ marginTop: 10 }}>
              <EmbedRow label={t("settings.embeddings.dimLabel")} value={String(cfg.embedding_dim)} />
            </div>
          </div>
          <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: 0, lineHeight: 1.5 }}>
            {t("settings.embeddings.envNote")}
          </p>
        </div>
      )}
    </div>
  );
}
