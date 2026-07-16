/**
 * IngestView.tsx — center pane of the Ingest section (ADR-0018 §3).
 *
 * Renders:
 *   - Header with title + "Run Ingest" button (toggles the form)
 *   - Inline form: file_path input → POST /ingest/trigger {file_path}
 *   - IngestRunList (TanStack Virtual, I4)
 *   - Toast on success / error (via showToast singleton)
 *
 * INVARIANT I7: does NOT add F9 actions (approve/reject/Skip/Create).
 * INVARIANT I4: run list is always virtualised (delegated to IngestRunList).
 * INVARIANT I3: subscribes to ingestStore only via typed selectors.
 *
 * Polling: starts when the section mounts; stops when no runs are in "running" state.
 */

import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  useIngestStore,
  selectFetchFresh,
  selectStartPolling,
  selectRunningCount,
  selectIngestError,
  selectIngestLoading,
} from "../../store/ingestStore";
import { selectVaultId, selectSetActiveSection, useAppStore } from "../../store/appStore";
import { triggerIngest } from "../../api/ingestClient";
import { IngestRunList } from "./IngestRunList";
import { UploadZone } from "./UploadZone";
import { showToast } from "../common/Toast";
import { EmptyState } from "../common/EmptyState";
import { useProviderConfigured } from "../../hooks/useProviderConfigured";

export function IngestView() {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);
  const setActiveSection = useAppStore(selectSetActiveSection);
  const fetchFresh = useIngestStore(selectFetchFresh);
  const startPolling = useIngestStore(selectStartPolling);
  const runningCount = useIngestStore(selectRunningCount);
  const storeError = useIngestStore(selectIngestError);
  const loading = useIngestStore(selectIngestLoading);

  // Provider gate (P0): check once on mount.
  const { configured, loading: providerLoading } = useProviderConfigured();

  const [formOpen, setFormOpen] = useState(false);
  const [filePath, setFilePath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Initial fetch + start polling
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchFresh(vaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [vaultId, fetchFresh]);

  // Start polling whenever there are running runs
  const stopPollRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (runningCount > 0 && !stopPollRef.current) {
      stopPollRef.current = startPolling(vaultId);
    }
    if (runningCount === 0 && stopPollRef.current) {
      stopPollRef.current();
      stopPollRef.current = null;
    }
  }, [runningCount, startPolling, vaultId]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (stopPollRef.current) {
        stopPollRef.current();
        stopPollRef.current = null;
      }
    };
  }, []);

  // Focus the input when form opens
  useEffect(() => {
    if (formOpen) {
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [formOpen]);

  const handleToggleForm = useCallback(() => {
    setFormOpen((v) => !v);
    if (formOpen) setFilePath("");
  }, [formOpen]);

  const handleSubmit = useCallback(async () => {
    const path = filePath.trim();
    if (!path) return;
    setSubmitting(true);
    try {
      await triggerIngest(path);
      showToast(t("ingest.toastStarted", { file: path }), "success");
      setFilePath("");
      setFormOpen(false);
      // Refresh list + start polling for new run
      void fetchFresh(vaultId);
      stopPollRef.current?.();
      stopPollRef.current = startPolling(vaultId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("common.unknown");
      showToast(t("ingest.toastError", { detail: msg }), "error");
    } finally {
      setSubmitting(false);
    }
  }, [filePath, fetchFresh, startPolling, vaultId, t]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") void handleSubmit();
      if (e.key === "Escape") {
        setFormOpen(false);
        setFilePath("");
      }
    },
    [handleSubmit],
  );

  // While checking configuration, render nothing to avoid flicker (I3).
  if (providerLoading || configured === null) {
    return <div data-testid="ingest-view" style={{ flex: 1, background: "var(--syn-bg)" }} />;
  }

  // Gate: no provider configured → block with CTA. Actions are disabled below.
  if (!configured) {
    return (
      <div
        data-testid="ingest-view"
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          overflow: "hidden",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--syn-bg)",
        }}
      >
        <EmptyState
          title={t("providerGate.title")}
          body={t("providerGate.body")}
          testId="provider-gate-ingest"
          actions={[
            {
              label: t("providerGate.cta"),
              variant: "primary",
              onClick: () => setActiveSection("settings"),
            },
          ]}
        />
      </div>
    );
  }

  return (
    <div
      data-testid="ingest-view"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            flex: 1,
          }}
        >
          {t("ingest.title")}
          {runningCount > 0 && (
            <span
              aria-label={`${runningCount} running`}
              style={{
                marginLeft: 8,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 18,
                height: 18,
                padding: "0 5px",
                borderRadius: 9,
                background: "var(--syn-accent)",
                color: "#ffffff",
                fontSize: 10,
                fontWeight: 700,
              }}
            >
              {runningCount}
            </span>
          )}
        </h2>

        <button
          onClick={handleToggleForm}
          aria-expanded={formOpen}
          aria-label={formOpen ? t("ingest.cancel") : t("ingest.runIngest")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "5px 12px",
            border: "1px solid var(--syn-border)",
            borderRadius: 6,
            background: formOpen ? "var(--syn-surface-hover)" : "transparent",
            color: formOpen ? "var(--syn-text-muted)" : "var(--syn-accent)",
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          {formOpen ? t("ingest.cancel") : t("ingest.runIngest")}
        </button>
      </div>

      {/* ── Upload zone (ADR-0020 Feature U) ──────────────────────────────── */}
      <div style={{ paddingTop: 12, paddingBottom: 4 }}>
        <UploadZone />
      </div>

      {/* ── Inline run form ─────────────────────────────────────────────────── */}
      {formOpen && (
        <div
          data-testid="ingest-form"
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            background: "var(--syn-surface-sunken)",
          }}
        >
          <label
            htmlFor="ingest-file-path"
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 500,
              color: "var(--syn-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("ingest.filePathLabel")}
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              ref={inputRef}
              id="ingest-file-path"
              type="text"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("ingest.filePathPlaceholder")}
              aria-describedby="ingest-file-path-help"
              disabled={submitting}
              style={{
                flex: 1,
                padding: "6px 10px",
                background: "var(--syn-bg)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                color: "var(--syn-text)",
                fontSize: 12,
                fontFamily: "var(--syn-font-mono)",
                outline: "none",
              }}
            />
            <button
              onClick={() => void handleSubmit()}
              disabled={!filePath.trim() || submitting}
              aria-label={t("ingest.submit")}
              style={{
                padding: "6px 16px",
                border: "none",
                borderRadius: 6,
                background:
                  !filePath.trim() || submitting ? "var(--syn-surface-hover)" : "var(--syn-accent)",
                color: !filePath.trim() || submitting ? "var(--syn-text-dim)" : "#ffffff",
                fontSize: 12,
                fontWeight: 600,
                cursor: !filePath.trim() || submitting ? "not-allowed" : "pointer",
              }}
            >
              {submitting ? t("common.loading") : t("ingest.submit")}
            </button>
          </div>
          <p
            id="ingest-file-path-help"
            style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}
          >
            {t("ingest.filePathHelp")}
          </p>
        </div>
      )}

      {/* ── Store error ─────────────────────────────────────────────────────── */}
      {storeError && !loading && (
        <div
          role="alert"
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            fontSize: 12,
            color: "var(--syn-red)",
            background: "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)",
          }}
        >
          {storeError}
          <button
            onClick={() => void fetchFresh(vaultId)}
            style={{
              marginLeft: 8,
              fontSize: 12,
              color: "var(--syn-text-muted)",
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              padding: 0,
            }}
          >
            {t("common.retry")}
          </button>
        </div>
      )}

      {/* ── Run list (TanStack Virtual — I4) ───────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <IngestRunList vaultId={vaultId} />
      </div>
    </div>
  );
}
