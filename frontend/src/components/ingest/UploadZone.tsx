/**
 * UploadZone.tsx — drag-and-drop + browse upload area (ADR-0020 §3 / Feature U).
 *
 * F12 (ADR-0025 §4, AC-F12-2): now accepts PDF, DOCX, PPTX, XLSX in addition to
 * markdown/plain-text. Binary files are extracted server-side on upload.
 *
 * - Accepts .md / .txt / .markdown / .pdf / .docx / .pptx / .xlsx
 *   (client-side guard for UX; backend is authoritative — 415 for unknown types)
 * - Rejects others client-side with a friendly message (backs up the server 415)
 * - Size check: warn client-side if > 25 MB (backs up the server 413)
 * - On drop/select → POST /ingest/upload (multipart) → success toast + fetchFresh
 * - On error → error toast with backend detail
 * - Drag highlight: reduced-motion safe (background color only, no animation)
 *
 * INVARIANT I4: no heavy per-frame work here; plain DOM event handlers.
 * INVARIANT I3: reads ingestStore only via typed selectors.
 * INVARIANT I7: 25 MB client cap (ADR-0020 §2.4).
 */

import { useRef, useState, useCallback, type DragEvent, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { useIngestStore, selectFetchFresh } from "../../store/ingestStore";
import { selectVaultId, useAppStore } from "../../store/appStore";
import { uploadDocument } from "../../api/ingestClient";
import { showToast } from "../common/Toast";

// ─── Constants ────────────────────────────────────────────────────────────────

// F12: binary formats accepted on upload; backend extracts text synchronously (ADR-0025 §4.2).
// Text formats are ingested directly by the watcher; binary companion .extracted.md is ingested.
const ACCEPTED_EXTENSIONS = new Set([
  ".md",
  ".txt",
  ".markdown",
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
]);
const MAX_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB client-side cap (mirrors ADR-0020 §2.4)
const ACCEPT_ATTR = ".md,.txt,.markdown,.pdf,.docx,.pptx,.xlsx";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

function isAccepted(file: File): boolean {
  return ACCEPTED_EXTENSIONS.has(getExtension(file.name));
}

// ─── Component ───────────────────────────────────────────────────────────────

interface UploadZoneProps {
  /** Optional callback after a successful upload (runs list is already refreshed). */
  onSuccess?: () => void;
}

export function UploadZone({ onSuccess }: UploadZoneProps) {
  const { t } = useTranslation();
  const fetchFresh = useIngestStore(selectFetchFresh);
  const vaultId = useAppStore(selectVaultId);

  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    async (file: File) => {
      // Client-side guards (UX convenience, not security)
      if (!isAccepted(file)) {
        showToast(t("ingest.upload.badType"), "error");
        return;
      }
      if (file.size > MAX_SIZE_BYTES) {
        showToast(t("ingest.upload.tooLarge"), "error");
        return;
      }

      setUploading(true);
      try {
        await uploadDocument(file);
        showToast(t("ingest.upload.toastStarted", { file: file.name }), "success");
        void fetchFresh(vaultId);
        onSuccess?.();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : t("common.unknown");
        showToast(t("ingest.upload.toastError", { detail: msg }), "error");
      } finally {
        setUploading(false);
        // Reset file input so the same file can be re-selected
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [fetchFresh, vaultId, onSuccess, t],
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    // Only clear drag state if leaving the zone itself (not entering a child)
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragging(false);
    }
  }, []);

  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const isActive = dragging && !uploading;

  return (
    <div
      data-testid="upload-zone"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={() => !uploading && inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (!uploading) inputRef.current?.click();
        }
      }}
      aria-label={t("ingest.upload.drop")}
      aria-disabled={uploading}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        padding: "14px 16px",
        margin: "0 16px 0",
        border: `1px dashed ${isActive ? "var(--syn-accent)" : "var(--syn-border)"}`,
        borderRadius: 8,
        background: isActive ? "var(--syn-accent-soft)" : "var(--syn-surface-sunken)",
        cursor: uploading ? "wait" : "pointer",
        transition: "border-color 0.12s ease, background 0.12s ease",
        userSelect: "none",
        flexShrink: 0,
      }}
    >
      {/* Hidden file input */}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        onChange={handleInputChange}
        style={{ display: "none" }}
        tabIndex={-1}
        aria-hidden="true"
        disabled={uploading}
      />

      {/* Upload icon */}
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke={
          uploading
            ? "var(--syn-text-dim)"
            : isActive
              ? "var(--syn-accent)"
              : "var(--syn-text-muted)"
        }
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="17 8 12 3 7 8" />
        <line x1="12" y1="3" x2="12" y2="15" />
      </svg>

      {/* Primary label */}
      <span
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: uploading ? "var(--syn-text-dim)" : "var(--syn-text-muted)",
        }}
      >
        {uploading ? t("common.loading") : t("ingest.upload.drop")}
      </span>

      {/* Accepted types hint (F12: PDF/DOCX/PPTX/XLSX now accepted — ADR-0025 §4) */}
      <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }} data-testid="upload-hint">
        {t("ingest.upload.hint")}
      </span>
    </div>
  );
}
