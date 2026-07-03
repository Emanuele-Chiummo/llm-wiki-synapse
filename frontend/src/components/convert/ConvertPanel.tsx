/**
 * ConvertPanel.tsx — dedicated "Convert PDFs with Marker" surface [F12][R11-1][A1].
 *
 * Sprint v1.1 Wave 2a — AC-R11-1-5, AC-R11-1-6 (dedicated component per §10 A1).
 *
 * Features:
 *   - File pick + drag-drop for 1..10 PDFs only.
 *   - Client-side guard: rejects > 10 files and non-.pdf before sending (AC-R11-1-5, I7).
 *   - Marker health badge (GET /ingest/marker-health) — polled on mount; manual refresh.
 *   - "Convert & ingest" primary action: disabled when Marker offline, with tooltip (AC-R11-1-5).
 *   - Per-file status rows: pending → converting → done (check) / failed (X + detail) (AC-R11-1-6).
 *   - Success hint pointing user to the Sources/tree panel once done.
 *   - Component-local state ONLY — no Zustand dispatch for ephemeral progress (I3).
 *
 * Design tokens used: var(--syn-accent), var(--syn-border), var(--syn-bg-soft),
 * var(--syn-text-muted), var(--syn-text-dim), var(--syn-radius-md), var(--syn-surface-sunken).
 * No hardcoded colors (dark-mode safe).
 *
 * All API calls through convertClient → apiFetch (ADR-0052 §4.2, never hand-rolled).
 * No per-token heavy work; no layout algorithm (I2, I3).
 */

import {
  useRef,
  useState,
  useCallback,
  useEffect,
  type DragEvent,
  type ChangeEvent,
} from "react";
import { useTranslation } from "react-i18next";
import {
  FileText,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Clock,
  Upload,
  WifiOff,
  Wifi,
} from "lucide-react";
import {
  convertFiles,
  getMarkerHealth,
  MarkerError,
  type MarkerHealthResponse,
} from "../../api/convertClient";

// ─── Constants ─────────────────────────────────────────────────────────────────

const MAX_FILES = 10;
const ICON_SIZE = 16;

// ─── Types ─────────────────────────────────────────────────────────────────────

type FileStatus = "pending" | "converting" | "done" | "failed";

interface FileRow {
  /** Stable key for React reconciliation. */
  id: string;
  file: File;
  status: FileStatus;
  /** Error detail from the 502 response (AC-R11-1-6). */
  errorDetail?: string;
}

// ─── Small helpers ─────────────────────────────────────────────────────────────

function isPdf(file: File): boolean {
  return file.name.toLowerCase().endsWith(".pdf");
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// ─── StatusIcon ────────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: FileStatus }) {
  switch (status) {
    case "pending":
      return (
        <Clock
          size={ICON_SIZE}
          aria-hidden="true"
          style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
        />
      );
    case "converting":
      return (
        <Loader2
          size={ICON_SIZE}
          aria-hidden="true"
          className="spin"
          style={{
            color: "var(--syn-accent)",
            flexShrink: 0,
            animation: "syn-spin 0.8s linear infinite",
          }}
        />
      );
    case "done":
      return (
        <CheckCircle2
          size={ICON_SIZE}
          aria-hidden="true"
          style={{ color: "var(--syn-success, #22c55e)", flexShrink: 0 }}
        />
      );
    case "failed":
      return (
        <XCircle
          size={ICON_SIZE}
          aria-hidden="true"
          style={{ color: "var(--syn-error, #ef4444)", flexShrink: 0 }}
        />
      );
  }
}

// ─── ConvertPanel ──────────────────────────────────────────────────────────────

export function ConvertPanel() {
  const { t } = useTranslation();

  // Per-file rows (component-local — I3: no Zustand dispatch)
  const [rows, setRows] = useState<FileRow[]>([]);
  // Drag-over highlight
  const [dragging, setDragging] = useState(false);
  // Whether a conversion is in progress
  const [converting, setConverting] = useState(false);
  // Inline validation message
  const [validationMsg, setValidationMsg] = useState<string | null>(null);
  // Whether at least one row is done successfully
  const [anyDone, setAnyDone] = useState(false);

  // Marker health
  const [health, setHealth] = useState<MarkerHealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  // ── Marker health poll on mount ─────────────────────────────────────────────

  const fetchHealth = useCallback(async () => {
    setHealthLoading(true);
    const h = await getMarkerHealth();
    setHealth(h);
    setHealthLoading(false);
  }, []);

  useEffect(() => {
    void fetchHealth();
    // no interval — manual refresh only (avoids unbounded polling on a slow Marker service)
  }, [fetchHealth]);

  // ── File validation helpers ─────────────────────────────────────────────────

  const validateAndSetFiles = useCallback(
    (incoming: File[]): boolean => {
      setValidationMsg(null);

      const pdfs = incoming.filter(isPdf);
      const nonPdfs = incoming.filter((f) => !isPdf(f));

      if (nonPdfs.length > 0) {
        setValidationMsg(t("convert.badFileType"));
        return false;
      }

      const combined = rows.length + pdfs.length;
      if (combined > MAX_FILES) {
        setValidationMsg(t("convert.tooManyFiles"));
        return false;
      }

      const newRows: FileRow[] = pdfs.map((file) => ({
        id: makeId(),
        file,
        status: "pending",
      }));

      setRows((prev) => [...prev, ...newRows]);
      setAnyDone(false);
      return true;
    },
    [rows.length, t],
  );

  // ── Drag-and-drop ───────────────────────────────────────────────────────────

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragging(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      const files = Array.from(e.dataTransfer.files);
      validateAndSetFiles(files);
    },
    [validateAndSetFiles],
  );

  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      validateAndSetFiles(files);
      // Reset so the same files can be re-selected if removed
      if (inputRef.current) inputRef.current.value = "";
    },
    [validateAndSetFiles],
  );

  const openPicker = useCallback(() => {
    if (!converting) inputRef.current?.click();
  }, [converting]);

  // ── Remove a single file row ────────────────────────────────────────────────

  const removeRow = useCallback((id: string) => {
    setRows((prev) => prev.filter((r) => r.id !== id));
    setValidationMsg(null);
  }, []);

  // ── Convert action ──────────────────────────────────────────────────────────

  const handleConvert = useCallback(async () => {
    if (converting || rows.length === 0) return;
    if (health?.status !== "ok") return;

    // Mark all pending rows as converting
    setRows((prev) => prev.map((r) => ({ ...r, status: "converting" as FileStatus })));
    setConverting(true);
    setAnyDone(false);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      // We send all files in a single multipart call (backend handles per-file)
      const files = rows.map((r) => r.file);
      await convertFiles(files, ctrl.signal);

      // All files done
      setRows((prev) => prev.map((r) => ({ ...r, status: "done" as FileStatus })));
      setAnyDone(true);
    } catch (err: unknown) {
      let detail = "Unknown error";
      if (err instanceof MarkerError) {
        detail = err.detail;
      } else if (err instanceof Error) {
        detail = err.message;
      }
      // Mark all converting rows as failed with the same detail
      // (backend returns a single error body when Marker is unavailable for the whole batch)
      setRows((prev) =>
        prev.map((r) =>
          r.status === "converting"
            ? { ...r, status: "failed" as FileStatus, errorDetail: detail }
            : r,
        ),
      );
    } finally {
      setConverting(false);
      abortRef.current = null;
    }
  }, [converting, rows, health]);

  // ── Reset panel ─────────────────────────────────────────────────────────────

  const handleReset = useCallback(() => {
    setRows([]);
    setValidationMsg(null);
    setAnyDone(false);
    setConverting(false);
  }, []);

  // ── Derived state ───────────────────────────────────────────────────────────

  const isOffline = health === null || health.status !== "ok";
  const canConvert = !converting && rows.length > 0 && !isOffline;
  const hasPending = rows.some((r) => r.status === "pending");

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="convert-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 24,
        maxWidth: 640,
        margin: "0 auto",
        padding: "24px 24px 40px",
        width: "100%",
        boxSizing: "border-box",
      }}
    >
      {/* ── Header ── */}
      <div>
        <h1
          style={{
            margin: 0,
            fontSize: 18,
            fontWeight: 600,
            color: "var(--syn-text)",
          }}
        >
          {t("convert.title")}
        </h1>
        <p
          style={{
            margin: "6px 0 0",
            fontSize: 13,
            color: "var(--syn-text-muted)",
            lineHeight: 1.5,
          }}
        >
          {t("convert.desc")}
        </p>
      </div>

      {/* ── Marker health badge ── */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 8 }}
        data-testid="marker-health-row"
      >
        {healthLoading ? (
          <Loader2
            size={14}
            aria-hidden="true"
            style={{
              color: "var(--syn-text-dim)",
              animation: "syn-spin 0.8s linear infinite",
            }}
          />
        ) : isOffline ? (
          <WifiOff size={14} aria-hidden="true" style={{ color: "var(--syn-error, #ef4444)" }} />
        ) : (
          <Wifi size={14} aria-hidden="true" style={{ color: "var(--syn-success, #22c55e)" }} />
        )}
        <span
          data-testid="marker-status-badge"
          style={{
            fontSize: 12,
            fontWeight: 500,
            color: healthLoading
              ? "var(--syn-text-dim)"
              : isOffline
                ? "var(--syn-error, #ef4444)"
                : "var(--syn-success, #22c55e)",
          }}
        >
          {healthLoading
            ? t("common.loading")
            : isOffline
              ? t("convert.markerOfflineBadge")
              : t("convert.markerOnlineBadge")}
        </span>
        <button
          aria-label={t("convert.markerHealthRefresh")}
          title={t("convert.markerHealthRefresh")}
          onClick={() => void fetchHealth()}
          disabled={healthLoading}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 24,
            height: 24,
            padding: 0,
            border: "none",
            borderRadius: "var(--syn-radius-md)",
            background: "transparent",
            color: "var(--syn-text-dim)",
            cursor: healthLoading ? "default" : "pointer",
          }}
        >
          <RefreshCw size={12} aria-hidden="true" />
        </button>
      </div>

      {/* ── Drop zone ── */}
      <div
        data-testid="convert-drop-zone"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={openPicker}
        role="button"
        tabIndex={0}
        aria-label={t("convert.dropLabel")}
        aria-disabled={converting}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          padding: "28px 20px",
          border: `1px dashed ${dragging ? "var(--syn-accent)" : "var(--syn-border)"}`,
          borderRadius: 8,
          background: dragging ? "var(--syn-accent-soft)" : "var(--syn-surface-sunken)",
          cursor: converting ? "wait" : "pointer",
          transition: "border-color 0.12s ease, background 0.12s ease",
          userSelect: "none",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf"
          multiple
          onChange={handleInputChange}
          style={{ display: "none" }}
          tabIndex={-1}
          aria-hidden="true"
          disabled={converting}
        />
        <Upload
          size={22}
          aria-hidden="true"
          style={{
            color: dragging ? "var(--syn-accent)" : "var(--syn-text-muted)",
          }}
        />
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--syn-text-muted)" }}>
          {t("convert.dropLabel")}
        </span>
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("convert.dropHint")}
        </span>
      </div>

      {/* ── Validation message ── */}
      {validationMsg && (
        <p
          role="alert"
          style={{
            margin: 0,
            fontSize: 12,
            color: "var(--syn-error, #ef4444)",
          }}
          data-testid="convert-validation-msg"
        >
          {validationMsg}
        </p>
      )}

      {/* ── Per-file status rows (AC-R11-1-6) ── */}
      {rows.length > 0 && (
        <ul
          data-testid="convert-file-list"
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
          aria-label="Files to convert"
        >
          {rows.map((row) => (
            <li
              key={row.id}
              data-testid={`convert-file-row-${row.status}`}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                padding: "8px 10px",
                borderRadius: 6,
                background: "var(--syn-bg-soft)",
                border: "1px solid var(--syn-border)",
              }}
            >
              {/* Status icon */}
              <StatusIcon status={row.status} />

              {/* File info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: "var(--syn-text)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  data-testid="convert-file-name"
                >
                  {row.file.name}
                </div>
                <div style={{ fontSize: 11, color: "var(--syn-text-dim)", marginTop: 1 }}>
                  {row.status === "failed" && row.errorDetail ? (
                    <span
                      style={{ color: "var(--syn-error, #ef4444)" }}
                      data-testid="convert-file-error"
                    >
                      {row.errorDetail}
                    </span>
                  ) : (
                    <span>
                      {row.status === "pending" && t("convert.status.pending")}
                      {row.status === "converting" && t("convert.status.converting")}
                      {row.status === "done" && t("convert.status.done")}
                      {row.status === "failed" && t("convert.status.failed")}
                    </span>
                  )}
                </div>
              </div>

              {/* Remove button (only for pending rows before conversion starts) */}
              {row.status === "pending" && !converting && (
                <button
                  aria-label={`Remove ${row.file.name}`}
                  onClick={() => removeRow(row.id)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 20,
                    height: 20,
                    padding: 0,
                    border: "none",
                    borderRadius: 4,
                    background: "transparent",
                    color: "var(--syn-text-dim)",
                    cursor: "pointer",
                    flexShrink: 0,
                  }}
                >
                  <XCircle size={14} aria-hidden="true" />
                </button>
              )}

              {/* File type indicator */}
              <FileText
                size={14}
                aria-hidden="true"
                style={{ color: "var(--syn-text-dim)", flexShrink: 0, marginTop: 1 }}
              />
            </li>
          ))}
        </ul>
      )}

      {/* ── Success hint ── */}
      {anyDone && (
        <p
          role="status"
          data-testid="convert-success-hint"
          style={{
            margin: 0,
            fontSize: 12,
            color: "var(--syn-text-muted)",
            padding: "8px 12px",
            background: "var(--syn-accent-soft)",
            borderRadius: 6,
            border: "1px solid color-mix(in srgb, var(--syn-accent) 30%, transparent 70%)",
          }}
        >
          {t("convert.successHint")}
        </p>
      )}

      {/* ── Primary action row ── */}
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          data-testid="convert-submit-btn"
          className="syn-btn syn-btn--primary"
          disabled={!canConvert}
          title={isOffline ? t("convert.markerOfflineTooltip") : undefined}
          aria-disabled={!canConvert}
          onClick={() => void handleConvert()}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 16px",
            borderRadius: "var(--syn-radius-md)",
            border: "none",
            background: canConvert ? "var(--syn-accent)" : "var(--syn-border)",
            color: canConvert ? "#ffffff" : "var(--syn-text-dim)",
            fontWeight: 600,
            fontSize: 13,
            cursor: canConvert ? "pointer" : "not-allowed",
            transition: "background 0.1s ease",
          }}
        >
          {converting ? (
            <Loader2
              size={14}
              aria-hidden="true"
              style={{ animation: "syn-spin 0.8s linear infinite" }}
            />
          ) : (
            <Upload size={14} aria-hidden="true" />
          )}
          {t("convert.primaryAction")}
        </button>

        {/* Reset / clear files */}
        {rows.length > 0 && !converting && (
          <button
            onClick={handleReset}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "8px 12px",
              borderRadius: "var(--syn-radius-md)",
              border: "1px solid var(--syn-border)",
              background: "transparent",
              color: "var(--syn-text-muted)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            {t("common.close")}
          </button>
        )}

        {/* Offline tooltip area — visible text for screen readers */}
        {isOffline && !healthLoading && hasPending && (
          <span
            role="note"
            style={{ fontSize: 11, color: "var(--syn-error, #ef4444)" }}
          >
            {t("convert.markerOfflineTooltip")}
          </span>
        )}
      </div>

      {/* Keyframe for spinner (inline, scoped) */}
      <style>{`@keyframes syn-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
