/**
 * ConvertPanel.tsx — dedicated "Convert PDFs with Marker" surface [F12][R11-1][R12-6][A1].
 *
 * Sprint v1.4 W0 — async marker contract:
 *   POST /ingest/convert-marker → 202 immediately (batch queued)
 *   GET  /ingest/convert-marker/status polled every 2.5 s while running
 *
 * Features:
 *   - File pick + drag-drop (fixed: onDragEnter prevents + highlights drop zone).
 *   - Client-side guard: rejects > 10 files and non-.pdf before sending (AC-R11-1-5, I7).
 *   - Marker health badge (GET /ingest/marker-health) — polled on mount; manual refresh.
 *   - "Converti e ingerisci" primary action: disabled when Marker offline (AC-R11-1-5).
 *   - Async conversion progress: percentage bar + ETA label + per-file status rows.
 *   - Conversion history: persisted to localStorage; "Apri" button navigates to Sources.
 *   - [R12-6] "Avvia Marker" button: visible only in Tauri + offline. Reads the start command
 *     from localStorage key `synapse.markerStartCommand` (per-machine; NOT app_config).
 *     When unset, reveals an inline config field before spawning. Spawns via tauri-plugin-shell
 *     as `sh -c "<cmd> >/dev/null 2>&1 &"` (detached). Polls health every 3 s up to 120 s.
 *   - Component-local state for ephemeral progress (I3 compliant).
 *     graphStore is used ONLY for navigation ("Apri" → setActiveSection("sources")).
 *
 * Design tokens: var(--syn-accent), var(--syn-border), var(--syn-bg-soft),
 * var(--syn-text-muted), var(--syn-text-dim), var(--syn-radius-md), var(--syn-surface-sunken).
 *
 * All API calls through convertClient → apiFetch (ADR-0052 §4.2).
 * No per-token heavy work; no layout algorithm (I2, I3).
 */

import { useRef, useState, useCallback, useEffect, type DragEvent, type ChangeEvent } from "react";
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
  Play,
  Settings,
  FolderOpen,
  History,
} from "lucide-react";
import {
  startConvert,
  getConvertStatus,
  getMarkerHealth,
  ConvertError,
  type MarkerHealthResponse,
  type ConvertStatusResponse,
  type ConvertFileStatus,
} from "../../api/convertClient";
import { isTauri } from "../../api/base";
import { useGraphStore, selectSetActiveSection } from "../../store/graphStore";
import { usePollChain } from "../../hooks/usePollChain";

// ─── Constants ─────────────────────────────────────────────────────────────────

const MAX_FILES = 10;
const ICON_SIZE = 16;
const POLL_INTERVAL_MS = 2_500;
const MAX_HISTORY = 50;

/** localStorage key for the per-machine Marker start command. [R12-6] */
const LS_MARKER_CMD = "synapse.markerStartCommand";

/** localStorage key for conversion history. */
const LS_HISTORY_KEY = "synapse.convertHistory";

/** Poll interval (ms) while waiting for Marker to come online after spawn. [R12-6] */
const MARKER_POLL_INTERVAL_MS = 3_000;

/** Maximum poll duration (ms) — Marker model load can take ~60 s on MPS. [R12-6] */
const MARKER_POLL_TIMEOUT_MS = 120_000;

// ─── Types ─────────────────────────────────────────────────────────────────────

/**
 * Pre-submit file row — a PDF the user has added to the queue
 * but not yet sent to the backend.
 */
interface FileRow {
  id: string;
  file: File;
}

/**
 * One entry in the session conversion history.
 * Persisted to localStorage so it survives refresh.
 */
interface HistoryEntry {
  id: string;
  filename: string;
  safe_stem: string;
  timestamp: number;
  status: "ok" | "failed";
  companion_path: string | null;
}

// ─── localStorage helpers ──────────────────────────────────────────────────────

function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(LS_HISTORY_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as HistoryEntry[];
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(LS_HISTORY_KEY, JSON.stringify(entries));
  } catch {
    // storage unavailable or quota exceeded — non-fatal
  }
}

function getMarkerCmd(): string {
  try {
    return localStorage.getItem(LS_MARKER_CMD) ?? "";
  } catch {
    return "";
  }
}

function saveMarkerCmd(cmd: string): void {
  try {
    if (cmd.trim().length > 0) {
      localStorage.setItem(LS_MARKER_CMD, cmd.trim());
    } else {
      localStorage.removeItem(LS_MARKER_CMD);
    }
  } catch {
    // ignore
  }
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function isPdf(file: File): boolean {
  return file.name.toLowerCase().endsWith(".pdf");
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatTimestamp(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

// ─── StatusIcon ────────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: ConvertFileStatus | "pending" }) {
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
    case "ok":
      return (
        <CheckCircle2
          size={ICON_SIZE}
          aria-hidden="true"
          style={{ color: "var(--syn-green)", flexShrink: 0 }}
        />
      );
    case "failed":
      return (
        <XCircle
          size={ICON_SIZE}
          aria-hidden="true"
          style={{ color: "var(--syn-red)", flexShrink: 0 }}
        />
      );
  }
}

// ─── ConvertPanel ──────────────────────────────────────────────────────────────

export function ConvertPanel() {
  const { t } = useTranslation();

  // Navigation action (for "Apri" button — not ephemeral progress, I3 compliant)
  const setActiveSection = useGraphStore(selectSetActiveSection);

  // ── Pre-submit queue (component-local — I3) ─────────────────────────────────
  const [rows, setRows] = useState<FileRow[]>([]);

  // ── Conversion state (component-local — I3) ─────────────────────────────────
  // submitting: POST /ingest/convert-marker is in-flight (short, < 1 s)
  const [submitting, setSubmitting] = useState(false);
  // converting: polling GET /ingest/convert-marker/status (background conversion running)
  const [converting, setConverting] = useState(false);
  const [pollStatus, setPollStatus] = useState<ConvertStatusResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ── History (localStorage-backed, component-local — I3) ─────────────────────
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);

  // ── Drag-over highlight ──────────────────────────────────────────────────────
  const [dragging, setDragging] = useState(false);

  // ── Validation message ───────────────────────────────────────────────────────
  const [validationMsg, setValidationMsg] = useState<string | null>(null);

  // ── Marker health ────────────────────────────────────────────────────────────
  const [health, setHealth] = useState<MarkerHealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  // ── "Avvia Marker" state [R12-6] ────────────────────────────────────────────
  const [markerStarting, setMarkerStarting] = useState(false);
  const [markerStartError, setMarkerStartError] = useState<string | null>(null);
  const [showCmdField, setShowCmdField] = useState(false);
  const [cmdFieldValue, setCmdFieldValue] = useState("");
  const pollDeadlineRef = useRef<number>(0);

  const inputRef = useRef<HTMLInputElement>(null);

  // Detect desktop context once (stable across renders)
  const isDesktop = isTauri();

  // ── Polling — runs while `converting` is true (FE-ARCH-2: shared poll chain) ─
  //
  // Immediate first fetch avoids the 2.5 s blank state after submit. Continues
  // on transient network errors (schedule next anyway). Stops as soon as the
  // backend reports running = false (also updates history at that point).

  const convertPoll = usePollChain<ConvertStatusResponse>({
    fetch: (signal) => getConvertStatus(signal),
    onResult: (status) => {
      setPollStatus(status);
      if (!status.running) {
        // Batch finished — update history and stop (do NOT schedule next)
        setConverting(false);
        const newEntries: HistoryEntry[] = status.files.map((f) => ({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}-${f.safe_stem}`,
          filename: f.file,
          safe_stem: f.safe_stem,
          timestamp: Date.now(),
          status: f.status === "ok" ? "ok" : "failed",
          companion_path: f.companion_path,
        }));
        setHistory((prev) => {
          const next = [...newEntries, ...prev].slice(0, MAX_HISTORY);
          saveHistory(next);
          return next;
        });
      }
    },
    intervalFor: (status) => (status.running ? POLL_INTERVAL_MS : null),
    // Network hiccup — keep retrying at the same cadence rather than giving up.
    errorIntervalFor: () => POLL_INTERVAL_MS,
    initialDelayMs: 0,
  });

  useEffect(() => {
    if (converting) {
      convertPoll.start();
    } else {
      convertPoll.stop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [converting]);

  // ── Marker health poll on mount ──────────────────────────────────────────────

  const fetchHealth = useCallback(async () => {
    setHealthLoading(true);
    const h = await getMarkerHealth();
    setHealth(h);
    setHealthLoading(false);
    return h;
  }, []);

  useEffect(() => {
    void fetchHealth();
  }, [fetchHealth]);

  // ── Marker spawn poll (FE-ARCH-2: shared setTimeout-chain primitive) [R12-6] ─
  // Polls health every MARKER_POLL_INTERVAL_MS up to MARKER_POLL_TIMEOUT_MS
  // (wall-clock deadline checked before every tick via `shouldContinue`).

  const markerPoll = usePollChain<MarkerHealthResponse>({
    fetch: (signal) => getMarkerHealth(signal),
    onResult: (h) => {
      if (h.status === "ok") {
        setHealth(h);
        setHealthLoading(false);
        setMarkerStarting(false);
        setMarkerStartError(null);
      }
    },
    intervalFor: (h) => (h.status === "ok" ? null : MARKER_POLL_INTERVAL_MS),
    shouldContinue: () => Date.now() <= pollDeadlineRef.current,
    onGiveUp: () => {
      setMarkerStarting(false);
      setMarkerStartError(t("convert.startMarkerError"));
    },
    initialDelayMs: MARKER_POLL_INTERVAL_MS,
  });

  // ── Spawn + poll logic [R12-6] ───────────────────────────────────────────────

  const spawnAndPoll = useCallback(
    async (cmd: string) => {
      setMarkerStarting(true);
      setMarkerStartError(null);

      try {
        const { Command } = await import("@tauri-apps/plugin-shell");
        const child = Command.create("sh", ["-c", `${cmd} >/dev/null 2>&1 &`]);
        await child.execute();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        setMarkerStarting(false);
        setMarkerStartError(msg);
        return;
      }

      pollDeadlineRef.current = Date.now() + MARKER_POLL_TIMEOUT_MS;
      markerPoll.start();
    },
    [markerPoll],
  );

  // ── "Avvia Marker" button handler [R12-6] ────────────────────────────────────

  const handleStartMarker = useCallback(async () => {
    if (markerStarting) return;
    const cmd = getMarkerCmd();
    if (!cmd) {
      setCmdFieldValue("");
      setShowCmdField(true);
      return;
    }
    await spawnAndPoll(cmd);
  }, [markerStarting, spawnAndPoll]);

  const handleCmdSaveAndStart = useCallback(async () => {
    const trimmed = cmdFieldValue.trim();
    if (!trimmed) return;
    saveMarkerCmd(trimmed);
    setShowCmdField(false);
    await spawnAndPoll(trimmed);
  }, [cmdFieldValue, spawnAndPoll]);

  // ── File validation helpers ──────────────────────────────────────────────────

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
      }));

      setRows((prev) => [...prev, ...newRows]);
      return true;
    },
    [rows.length, t],
  );

  // ── Drag-and-drop (fixed: onDragEnter prevents drop rejection) ──────────────

  const handleDragEnter = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(true);
  }, []);

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
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
      e.stopPropagation();
      setDragging(false);
      if (submitting || converting) return; // don't accept drops while a batch is in flight
      const files = Array.from(e.dataTransfer.files);
      validateAndSetFiles(files);
    },
    [submitting, converting, validateAndSetFiles],
  );

  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      validateAndSetFiles(files);
      if (inputRef.current) inputRef.current.value = "";
    },
    [validateAndSetFiles],
  );

  const openPicker = useCallback(() => {
    if (!submitting && !converting) inputRef.current?.click();
  }, [submitting, converting]);

  // ── Remove a pre-submit file row ─────────────────────────────────────────────

  const removeRow = useCallback((id: string) => {
    setRows((prev) => prev.filter((r) => r.id !== id));
    setValidationMsg(null);
  }, []);

  // ── Submit: POST → 202 first, then start polling ─────────────────────────────
  //
  // `converting` is set to true ONLY after startConvert resolves with 202.
  // This prevents the polling useEffect from firing before the batch is confirmed,
  // which avoids calling getConvertStatus on an un-mocked state in tests.

  const handleConvert = useCallback(async () => {
    if (submitting || converting || rows.length === 0) return;
    if (health?.status !== "ok") return;

    setSubmitError(null);
    setSubmitting(true);

    try {
      const files = rows.map((r) => r.file);
      await startConvert(files);
      // POST succeeded — clear queue and start background polling
      setRows([]);
      setConverting(true); // triggers the polling useEffect
    } catch (err: unknown) {
      // Synchronous error (400/409/413/415) — batch did not start; no polling
      if (err instanceof ConvertError) {
        switch (err.status) {
          case 409:
            setSubmitError(t("convert.error409"));
            break;
          case 413:
            setSubmitError(t("convert.error413"));
            break;
          case 415:
            setSubmitError(t("convert.error415"));
            break;
          default:
            setSubmitError(err.detail);
        }
      } else {
        setSubmitError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }, [submitting, converting, rows, health, t]);

  // ── Reset: clear queue, progress, errors (NOT history) ──────────────────────

  const handleReset = useCallback(() => {
    setRows([]);
    setValidationMsg(null);
    setSubmitError(null);
    setPollStatus(null);
    setSubmitting(false);
    setConverting(false);
  }, []);

  // ── "Apri" — navigate to Sources section ────────────────────────────────────

  const handleOpen = useCallback(() => {
    setActiveSection("sources");
  }, [setActiveSection]);

  // ── Derived state ────────────────────────────────────────────────────────────

  const isOffline = health === null || health.status !== "ok";
  const canConvert = !submitting && !converting && rows.length > 0 && !isOffline;
  const hasPending = rows.some(() => true);

  const showStartBtn = isDesktop && isOffline && !healthLoading;

  const pct =
    pollStatus && pollStatus.total > 0 ? Math.round((pollStatus.done / pollStatus.total) * 100) : 0;

  const batchDone = pollStatus !== null && !pollStatus.running;
  const anyOk = batchDone && pollStatus.files.some((f) => f.status === "ok");

  // ── Render ───────────────────────────────────────────────────────────────────

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
        style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
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
          <WifiOff size={14} aria-hidden="true" style={{ color: "var(--syn-red)" }} />
        ) : (
          <Wifi size={14} aria-hidden="true" style={{ color: "var(--syn-green)" }} />
        )}
        <span
          data-testid="marker-status-badge"
          style={{
            fontSize: 12,
            fontWeight: 500,
            color: healthLoading
              ? "var(--syn-text-dim)"
              : isOffline
                ? "var(--syn-red)"
                : "var(--syn-green)",
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

        {/* ── "Avvia Marker" — desktop-only, shown when offline [R12-6] ── */}
        {showStartBtn && (
          <button
            data-testid="start-marker-btn"
            className="syn-btn"
            aria-label={t("convert.startMarkerAriaLabel")}
            disabled={markerStarting}
            onClick={() => void handleStartMarker()}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "4px 10px",
              borderRadius: "var(--syn-radius-md)",
              border: "1px solid var(--syn-border)",
              background: "var(--syn-bg-soft)",
              color: "var(--syn-text-muted)",
              fontSize: 12,
              fontWeight: 500,
              cursor: markerStarting ? "default" : "pointer",
              transition: "background 0.1s ease",
            }}
          >
            {markerStarting ? (
              <Loader2
                size={12}
                aria-hidden="true"
                style={{ animation: "syn-spin 0.8s linear infinite" }}
              />
            ) : (
              <Play size={12} aria-hidden="true" />
            )}
            {markerStarting ? t("convert.startMarkerStarting") : t("convert.startMarker")}
          </button>
        )}
      </div>

      {/* ── Inline command config field [R12-6] ── */}
      {showCmdField && (
        <div
          data-testid="start-marker-cmd-field"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: "12px 14px",
            borderRadius: 8,
            border: "1px solid var(--syn-border)",
            background: "var(--syn-bg-soft)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
            }}
          >
            <Settings size={12} aria-hidden="true" />
            {t("convert.startMarkerConfigTitle")}
          </div>
          <p style={{ margin: 0, fontSize: 11, color: "var(--syn-text-dim)", lineHeight: 1.5 }}>
            {t("convert.startMarkerConfigHint")}
          </p>
          <input
            data-testid="start-marker-cmd-input"
            type="text"
            value={cmdFieldValue}
            onChange={(e) => setCmdFieldValue(e.target.value)}
            placeholder={t("convert.startMarkerConfigPlaceholder")}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCmdSaveAndStart();
              if (e.key === "Escape") setShowCmdField(false);
            }}
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid var(--syn-border)",
              background: "var(--syn-surface-sunken)",
              color: "var(--syn-text)",
              fontSize: 12,
              fontFamily: "var(--syn-font-mono)",
            }}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button
              data-testid="start-marker-cmd-save"
              className="syn-btn syn-btn--primary"
              disabled={!cmdFieldValue.trim()}
              onClick={() => void handleCmdSaveAndStart()}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "5px 12px",
                borderRadius: "var(--syn-radius-md)",
                border: "none",
                background: cmdFieldValue.trim() ? "var(--syn-accent)" : "var(--syn-border)",
                color: cmdFieldValue.trim() ? "#ffffff" : "var(--syn-text-dim)",
                fontSize: 12,
                fontWeight: 600,
                cursor: cmdFieldValue.trim() ? "pointer" : "not-allowed",
              }}
            >
              <Play size={11} aria-hidden="true" />
              {t("convert.startMarkerConfigSave")}
            </button>
            <button
              data-testid="start-marker-cmd-cancel"
              onClick={() => setShowCmdField(false)}
              style={{
                padding: "5px 10px",
                borderRadius: "var(--syn-radius-md)",
                border: "1px solid var(--syn-border)",
                background: "transparent",
                color: "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              {t("convert.startMarkerConfigCancel")}
            </button>
          </div>
        </div>
      )}

      {/* ── Marker start error message [R12-6] ── */}
      {markerStartError && (
        <p
          role="alert"
          data-testid="start-marker-error"
          style={{ margin: 0, fontSize: 12, color: "var(--syn-red)" }}
        >
          {markerStartError}
        </p>
      )}

      {/* ── Drop zone (drag-drop fixed: onDragEnter prevents rejection) ── */}
      <div
        data-testid="convert-drop-zone"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
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
          style={{ color: dragging ? "var(--syn-accent)" : "var(--syn-text-muted)" }}
        />
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--syn-text-muted)" }}>
          {t("convert.dropLabel")}
        </span>
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>{t("convert.dropHint")}</span>
      </div>

      {/* ── Validation message ── */}
      {validationMsg && (
        <p
          role="alert"
          style={{ margin: 0, fontSize: 12, color: "var(--syn-red)" }}
          data-testid="convert-validation-msg"
        >
          {validationMsg}
        </p>
      )}

      {/* ── Submit error (409/413/415 from the backend) ── */}
      {submitError && (
        <p
          role="alert"
          data-testid="convert-submit-error"
          style={{ margin: 0, fontSize: 12, color: "var(--syn-red)" }}
        >
          {submitError}
        </p>
      )}

      {/* ── Pre-submit file queue ── */}
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
          aria-label={t("convert.filesAriaLabel")}
        >
          {rows.map((row) => (
            <li
              key={row.id}
              data-testid="convert-file-row-pending"
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
              <StatusIcon status="pending" />
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
                  {t("convert.status.pending")}
                </div>
              </div>
              {!converting && (
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
              <FileText
                size={14}
                aria-hidden="true"
                style={{ color: "var(--syn-text-dim)", flexShrink: 0, marginTop: 1 }}
              />
            </li>
          ))}
        </ul>
      )}

      {/* ── Active conversion: progress + per-file status ── */}
      {converting && (
        <div
          data-testid="convert-progress-section"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            padding: "12px 14px",
            borderRadius: 8,
            border: "1px solid var(--syn-border)",
            background: "var(--syn-bg-soft)",
          }}
        >
          {/* Progress bar + label */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
              }}
            >
              <span
                data-testid="convert-progress-label"
                style={{ fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)" }}
              >
                {pollStatus
                  ? t("convert.progressLabel", {
                      done: pollStatus.done,
                      total: pollStatus.total,
                      pct,
                    })
                  : t("convert.status.converting")}
              </span>
              {pollStatus?.eta_seconds != null && (
                <span
                  data-testid="convert-eta-label"
                  style={{ fontSize: 11, color: "var(--syn-text-dim)" }}
                >
                  {t("convert.etaLabel", { eta: pollStatus.eta_seconds })}
                </span>
              )}
            </div>
            {/* Progress bar */}
            <div
              data-testid="convert-progress"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              style={{
                height: 4,
                borderRadius: 2,
                background: "var(--syn-border)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${pct}%`,
                  background: "var(--syn-accent)",
                  transition: "width 0.3s ease",
                  borderRadius: 2,
                }}
              />
            </div>
          </div>

          {/* Per-file status rows (from pollStatus) */}
          {pollStatus && pollStatus.files.length > 0 && (
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
              aria-label={t("convert.statusAriaLabel")}
            >
              {pollStatus.files.map((f) => (
                <li
                  key={f.safe_stem}
                  data-testid={`convert-file-row-${f.status}`}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    padding: "6px 8px",
                    borderRadius: 4,
                    background: "var(--syn-surface-sunken)",
                  }}
                >
                  <StatusIcon status={f.status} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        fontWeight: 500,
                        color: "var(--syn-text)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      data-testid="convert-file-name"
                    >
                      {f.file}
                    </div>
                    {f.status === "failed" && f.detail && (
                      <div
                        style={{ fontSize: 11, color: "var(--syn-red)", marginTop: 1 }}
                        data-testid="convert-file-error"
                      >
                        {f.detail}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ── Post-conversion: per-file status (frozen) + success hint ── */}
      {batchDone && pollStatus && (
        <div
          data-testid="convert-done-section"
          style={{ display: "flex", flexDirection: "column", gap: 10 }}
        >
          {/* Per-file final status */}
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}
            data-testid="convert-file-list"
            aria-label={t("convert.resultsAriaLabel")}
          >
            {pollStatus.files.map((f) => (
              <li
                key={f.safe_stem}
                data-testid={`convert-file-row-${f.status}`}
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
                <StatusIcon status={f.status} />
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
                    {f.file}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--syn-text-dim)", marginTop: 1 }}>
                    {f.status === "failed" && f.detail ? (
                      <span style={{ color: "var(--syn-red)" }} data-testid="convert-file-error">
                        {f.detail}
                      </span>
                    ) : (
                      <span>{t(`convert.status.${f.status === "ok" ? "done" : f.status}`)}</span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {/* Success hint */}
          {anyOk && (
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
        </div>
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
          {submitting || converting ? (
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

        {/* Reset / clear */}
        {(rows.length > 0 || batchDone) && !submitting && !converting && (
          <button
            data-testid="convert-reset-btn"
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

        {/* Offline note */}
        {isOffline && !healthLoading && hasPending && (
          <span role="note" style={{ fontSize: 11, color: "var(--syn-red)" }}>
            {t("convert.markerOfflineTooltip")}
          </span>
        )}
      </div>

      {/* ── Conversion history ── */}
      {history.length > 0 && (
        <div
          data-testid="convert-history"
          style={{ display: "flex", flexDirection: "column", gap: 10 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
            }}
          >
            <History size={13} aria-hidden="true" />
            {t("convert.historyTitle")}
          </div>
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
            aria-label={t("convert.historyTitle")}
          >
            {history.map((entry) => (
              <li
                key={entry.id}
                data-testid={`convert-history-entry-${entry.status}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 10px",
                  borderRadius: 6,
                  background: "var(--syn-bg-soft)",
                  border: "1px solid var(--syn-border)",
                }}
              >
                {entry.status === "ok" ? (
                  <CheckCircle2
                    size={13}
                    aria-hidden="true"
                    style={{ color: "var(--syn-green)", flexShrink: 0 }}
                  />
                ) : (
                  <XCircle
                    size={13}
                    aria-hidden="true"
                    style={{ color: "var(--syn-red)", flexShrink: 0 }}
                  />
                )}
                <span
                  style={{
                    flex: 1,
                    fontSize: 12,
                    color: "var(--syn-text)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={entry.filename}
                >
                  {entry.filename}
                </span>
                <span style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                  {formatTimestamp(entry.timestamp)}
                </span>
                {entry.status === "ok" && (
                  <button
                    data-testid="convert-history-open-btn"
                    aria-label={t("convert.openBtn")}
                    onClick={handleOpen}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "3px 8px",
                      borderRadius: "var(--syn-radius-md)",
                      border: "1px solid var(--syn-border)",
                      background: "var(--syn-surface-sunken)",
                      color: "var(--syn-text-muted)",
                      fontSize: 11,
                      fontWeight: 500,
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    <FolderOpen size={11} aria-hidden="true" />
                    {t("convert.openBtn")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* UXA-28: @keyframes syn-spin is declared globally in theme.css — no inline <style> needed */}
    </div>
  );
}
