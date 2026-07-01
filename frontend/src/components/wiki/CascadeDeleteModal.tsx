/**
 * CascadeDeleteModal.tsx — two-step cascade-delete confirmation modal (F13, ADR-0026).
 *
 * Step 1 (preview): calls POST /pages/{id}/cascade-delete/preview and renders the full
 *   plan: will_delete list, will_preserve_with_pruned_source list, wikilinks_to_rewrite
 *   count + list, index_entry_will_be_removed, raw_source_to_delete.
 *   PROMINENTLY surfaces shared_entity_warnings (AC-F13-6a) — shown above all other
 *   sections so they cannot be missed before the user confirms.
 *
 * Step 2 (confirm): destructive-action button → DELETE /pages/{id}. On success calls
 *   onDeleted(result) so the caller can refresh the tree and navigate away.
 *   On error shows the message inline; does NOT mutate local state (invariant).
 *
 * INVARIANT I3: no per-frame heavy work; preview is a single fetch.
 * INVARIANT: two-step only — preview THEN confirm; no one-click delete.
 * No hardcoded values.
 */

import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { previewCascadeDelete, cascadeDelete } from "../../api/cascadeDeleteClient";
import type { CascadePreviewResponse, CascadeDeleteResult } from "../../api/types";
import { ApiError } from "../../api/graphClient";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface CascadeDeleteModalProps {
  /** UUID of the page to delete */
  pageId: string;
  /** Display title of the page (used in heading) */
  pageTitle: string | null;
  /** Called when the DELETE succeeds so the caller can refresh the tree */
  onDeleted: (result: CascadeDeleteResult) => void;
  /** Called when the user cancels (no API call was made) */
  onCancel: () => void;
}

type Step = "preview" | "confirm";

// ─── Styles (inline — no CSS file dependency) ─────────────────────────────────

const S = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    background: "rgba(31, 35, 40, 0.55)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 8000,
    padding: 16,
  },
  dialog: {
    background: "var(--syn-surface)",
    border: "1px solid var(--syn-border)",
    borderRadius: "var(--syn-radius-md)",
    width: "100%",
    maxWidth: 540,
    maxHeight: "80vh",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
    boxShadow: "var(--syn-shadow-pop)",
  },
  header: {
    padding: "14px 16px 12px",
    borderBottom: "1px solid var(--syn-border)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexShrink: 0,
  },
  headerTitle: {
    margin: 0,
    fontSize: 15,
    fontWeight: 600,
    color: "var(--syn-text)",
  },
  closeBtn: {
    background: "none",
    border: "none",
    color: "var(--syn-text-muted)",
    cursor: "pointer",
    fontSize: 18,
    lineHeight: 1,
    padding: "0 2px",
  },
  body: {
    flex: 1,
    overflow: "auto",
    padding: "12px 16px",
    display: "flex",
    flexDirection: "column" as const,
    gap: 14,
  },
  footer: {
    padding: "10px 16px",
    borderTop: "1px solid var(--syn-border)",
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 8,
    flexShrink: 0,
  },
  warningBanner: {
    background: "color-mix(in srgb, var(--syn-amber) 8%, white 92%)",
    border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent 70%)",
    borderRadius: "var(--syn-radius-sm)",
    padding: "10px 12px",
    display: "flex",
    flexDirection: "column" as const,
    gap: 6,
  },
  warningBannerTitle: {
    margin: 0,
    fontSize: 12,
    fontWeight: 700,
    color: "var(--syn-amber)",
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  warningItem: {
    fontSize: 12,
    color: "var(--syn-amber)",
    margin: 0,
    padding: "2px 0",
    borderBottom: "1px solid color-mix(in srgb, var(--syn-amber) 20%, transparent 80%)",
  },
  warningHint: {
    fontSize: 11,
    color: "var(--syn-text-muted)",
    margin: "4px 0 0",
    fontStyle: "italic",
  },
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
    color: "var(--syn-text-dim)",
    margin: 0,
  },
  emptyHint: {
    fontSize: 12,
    color: "var(--syn-text-dim)",
    fontStyle: "italic",
    margin: 0,
  },
  itemList: {
    margin: 0,
    padding: "0 0 0 16px",
    listStyle: "disc",
    display: "flex",
    flexDirection: "column" as const,
    gap: 3,
  },
  itemText: {
    fontSize: 12,
    color: "var(--syn-text-muted)",
    fontFamily: "monospace",
    wordBreak: "break-all" as const,
  },
  badge: {
    display: "inline-block",
    background: "var(--syn-surface-hover)",
    border: "1px solid var(--syn-border)",
    borderRadius: "var(--syn-radius-sm)",
    padding: "2px 6px",
    fontSize: 12,
    color: "var(--syn-text)",
    fontFamily: "monospace",
  },
  destructiveBtn: {
    background: "var(--syn-red)",
    border: "1px solid color-mix(in srgb, var(--syn-red) 80%, black 20%)",
    borderRadius: "var(--syn-radius-sm)",
    color: "#fff",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 600,
    padding: "6px 16px",
  },
  secondaryBtn: {
    background: "transparent",
    border: "1px solid var(--syn-border)",
    borderRadius: "var(--syn-radius-sm)",
    color: "var(--syn-text-muted)",
    cursor: "pointer",
    fontSize: 13,
    padding: "6px 14px",
  },
  spinnerText: {
    fontSize: 13,
    color: "var(--syn-text-muted)",
    textAlign: "center" as const,
    padding: "20px 0",
  },
  errorText: {
    fontSize: 13,
    color: "var(--syn-red)",
    textAlign: "center" as const,
    padding: "8px 0",
  },
  inlineError: {
    fontSize: 13,
    color: "var(--syn-red)",
    margin: 0,
    padding: "6px 8px",
    background: "color-mix(in srgb, var(--syn-red) 6%, white 94%)",
    border: "1px solid color-mix(in srgb, var(--syn-red) 25%, transparent 75%)",
    borderRadius: "var(--syn-radius-sm)",
  },
};

// ─── Component ────────────────────────────────────────────────────────────────

export function CascadeDeleteModal({
  pageId,
  pageTitle,
  onDeleted,
  onCancel,
}: CascadeDeleteModalProps) {
  const { t } = useTranslation();

  const [step, setStep] = useState<Step>("preview");
  const [preview, setPreview] = useState<CascadePreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [deleteInFlight, setDeleteInFlight] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ── Load preview on mount ──────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);

    previewCascadeDelete(pageId)
      .then((data) => {
        if (!cancelled) {
          setPreview(data);
          setPreviewLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : t("cascadeDelete.previewError");
          setPreviewError(msg);
          setPreviewLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pageId, t]);

  // ── Delete handler ────────────────────────────────────────────────────────

  const handleDelete = useCallback(async () => {
    setDeleteInFlight(true);
    setDeleteError(null);

    try {
      const result = await cascadeDelete(pageId);
      onDeleted(result);
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : t("cascadeDelete.deleteError");
      setDeleteError(msg);
    } finally {
      setDeleteInFlight(false);
    }
  }, [pageId, onDeleted, t]);

  // ── Keyboard ESC to cancel ────────────────────────────────────────────────

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !deleteInFlight) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, deleteInFlight]);

  // ── Render ────────────────────────────────────────────────────────────────

  const title = pageTitle ?? t("common.unknown");

  return (
    <div
      style={S.overlay}
      data-testid="cascade-delete-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`${t("cascadeDelete.modalTitle")}: ${title}`}
      onClick={(e) => {
        if (e.target === e.currentTarget && !deleteInFlight) onCancel();
      }}
    >
      <div style={S.dialog} data-testid="cascade-delete-modal">
        {/* Header */}
        <div style={S.header}>
          <h2 style={S.headerTitle} data-testid="cascade-delete-modal-title">
            {step === "preview"
              ? t("cascadeDelete.step1Title")
              : t("cascadeDelete.step2Title")}
            {" — "}
            <span style={{ color: "var(--syn-text-muted)", fontWeight: 400 }}>{title}</span>
          </h2>
          <button
            style={S.closeBtn}
            onClick={onCancel}
            disabled={deleteInFlight}
            aria-label={t("common.close")}
            data-testid="cascade-delete-close"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={S.body}>
          {step === "preview" ? (
            <PreviewBody
              loading={previewLoading}
              error={previewError}
              preview={preview}
              t={t}
            />
          ) : (
            <ConfirmBody preview={preview} deleteError={deleteError} t={t} />
          )}
        </div>

        {/* Footer */}
        <div style={S.footer}>
          {step === "preview" ? (
            <>
              <button
                style={S.secondaryBtn}
                onClick={onCancel}
                data-testid="cascade-delete-cancel"
              >
                {t("cascadeDelete.cancelButton")}
              </button>
              <button
                style={{
                  ...S.destructiveBtn,
                  opacity: previewLoading || previewError !== null ? 0.5 : 1,
                  cursor: previewLoading || previewError !== null ? "not-allowed" : "pointer",
                }}
                onClick={() => setStep("confirm")}
                disabled={previewLoading || previewError !== null}
                data-testid="cascade-delete-next"
              >
                {t("cascadeDelete.confirmButton")} &rarr;
              </button>
            </>
          ) : (
            <>
              <button
                style={S.secondaryBtn}
                onClick={() => {
                  setDeleteError(null);
                  setStep("preview");
                }}
                disabled={deleteInFlight}
                data-testid="cascade-delete-back"
              >
                {t("cascadeDelete.backButton")}
              </button>
              <button
                style={{
                  ...S.destructiveBtn,
                  opacity: deleteInFlight ? 0.6 : 1,
                  cursor: deleteInFlight ? "not-allowed" : "pointer",
                }}
                onClick={() => void handleDelete()}
                disabled={deleteInFlight}
                data-testid="cascade-delete-confirm"
              >
                {deleteInFlight ? "…" : t("cascadeDelete.confirmButton")}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── PreviewBody ──────────────────────────────────────────────────────────────

interface PreviewBodyProps {
  loading: boolean;
  error: string | null;
  preview: CascadePreviewResponse | null;
  t: TFunction;
}

function PreviewBody({ loading, error, preview, t }: PreviewBodyProps) {
  if (loading) {
    return (
      <p style={S.spinnerText} data-testid="cascade-delete-preview-loading">
        {t("cascadeDelete.previewLoading")}
      </p>
    );
  }

  if (error !== null) {
    return (
      <p style={S.errorText} data-testid="cascade-delete-preview-error">
        {error}
      </p>
    );
  }

  if (!preview) return null;

  const otherPagesToDelete = preview.will_delete.filter(
    (id) => id !== preview.target_page_id,
  );

  return (
    <>
      {/* ── Shared-entity warnings — shown FIRST (AC-F13-6a) ─────────────── */}
      {preview.shared_entity_warnings.length > 0 && (
        <div style={S.warningBanner} data-testid="cascade-delete-shared-warnings">
          <p style={S.warningBannerTitle}>
            <span aria-hidden="true">⚠</span>
            {t("cascadeDelete.warningsBanner")}
          </p>
          <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
            {preview.shared_entity_warnings.map((w, i) => (
              <li key={i} style={S.warningItem}>
                {w}
              </li>
            ))}
          </ul>
          <p style={S.warningHint}>{t("cascadeDelete.warningsHint")}</p>
        </div>
      )}

      {/* ── Pages that will be deleted ──────────────────────────────────── */}
      <div style={S.section}>
        <p style={S.sectionLabel}>{t("cascadeDelete.willDelete")}</p>
        {otherPagesToDelete.length === 0 ? (
          <p style={S.emptyHint} data-testid="cascade-delete-no-extra-pages">
            {t("cascadeDelete.noPagesDeleted")}
          </p>
        ) : (
          <ul style={S.itemList} data-testid="cascade-delete-will-delete-list">
            {otherPagesToDelete.map((id) => (
              <li key={id}>
                <code style={S.itemText}>{id}</code>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Pages preserved with pruned source ──────────────────────────── */}
      {preview.will_preserve_with_pruned_source.length > 0 && (
        <div style={S.section}>
          <p style={S.sectionLabel}>{t("cascadeDelete.willPreserve")}</p>
          <ul style={S.itemList} data-testid="cascade-delete-will-preserve-list">
            {preview.will_preserve_with_pruned_source.map((id) => (
              <li key={id}>
                <code style={S.itemText}>{id}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Wikilinks to rewrite ──────────────────────────────────────────── */}
      <div style={S.section}>
        <p style={S.sectionLabel}>{t("cascadeDelete.wikilinksToRewrite")}</p>
        {preview.wikilinks_to_rewrite.length === 0 ? (
          <p style={S.emptyHint} data-testid="cascade-delete-no-wikilinks">
            {t("cascadeDelete.noWikilinksToRewrite")}
          </p>
        ) : (
          <>
            <p style={{ margin: 0, fontSize: 12, color: "var(--syn-text-muted)" }}>
              {t("cascadeDelete.wikilinksCount", {
                count: preview.wikilinks_to_rewrite.reduce((s, r) => s + r.occurrences, 0),
              })}
            </p>
            <ul style={S.itemList} data-testid="cascade-delete-wikilinks-list">
              {preview.wikilinks_to_rewrite.map((r) => (
                <li key={r.source_page_id}>
                  <code style={S.itemText}>{r.file_path}</code>
                  <span style={{ color: "var(--syn-text-dim)", fontSize: 11, marginLeft: 4 }}>
                    {t("cascadeDelete.occurrences", { n: r.occurrences })}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {/* ── Index entry ──────────────────────────────────────────────────── */}
      {preview.index_entry_will_be_removed && (
        <div style={S.section} data-testid="cascade-delete-index-removed">
          <p style={{ ...S.emptyHint, color: "var(--syn-text-muted)", fontStyle: "normal" }}>
            {t("cascadeDelete.indexEntryRemoved")}
          </p>
        </div>
      )}

      {/* ── Raw source file ───────────────────────────────────────────────── */}
      {preview.raw_source_to_delete !== null ? (
        <div style={S.section} data-testid="cascade-delete-raw-source">
          <p style={S.sectionLabel}>{t("cascadeDelete.rawSourceDeleted")}</p>
          <code style={S.itemText}>{preview.raw_source_to_delete}</code>
        </div>
      ) : null}
    </>
  );
}

// ─── ConfirmBody ──────────────────────────────────────────────────────────────

interface ConfirmBodyProps {
  preview: CascadePreviewResponse | null;
  deleteError: string | null;
  t: TFunction;
}

function ConfirmBody({ preview, deleteError, t }: ConfirmBodyProps) {
  return (
    <>
      {/* Repeat shared-entity warnings on the confirm step as well (AC-F13-6a) */}
      {preview && preview.shared_entity_warnings.length > 0 && (
        <div style={S.warningBanner} data-testid="cascade-delete-confirm-warnings">
          <p style={S.warningBannerTitle}>
            <span aria-hidden="true">⚠</span>
            {t("cascadeDelete.warningsBanner")}
          </p>
          <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
            {preview.shared_entity_warnings.map((w, i) => (
              <li key={i} style={S.warningItem}>
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Summary badges */}
      {preview && (
        <div
          style={{ display: "flex", flexWrap: "wrap" as const, gap: 8 }}
          data-testid="cascade-delete-confirm-summary"
        >
          <span style={S.badge}>
            {preview.will_delete.length} page(s) deleted
          </span>
          {preview.wikilinks_to_rewrite.length > 0 && (
            <span style={S.badge}>
              {preview.wikilinks_to_rewrite.reduce((s, r) => s + r.occurrences, 0)} wikilink(s) cleaned
            </span>
          )}
          {preview.index_entry_will_be_removed && (
            <span style={S.badge}>index.md updated</span>
          )}
        </div>
      )}

      {deleteError !== null && (
        <p style={S.inlineError} data-testid="cascade-delete-error">
          {deleteError}
        </p>
      )}
    </>
  );
}
