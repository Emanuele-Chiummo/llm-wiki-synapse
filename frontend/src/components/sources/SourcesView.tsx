/**
 * SourcesView.tsx — raw-source file browser for Synapse [F11 / v0.6].
 *
 * Layout:
 *   - Header: title + "Index all" button + Import button (UploadZone) + Refresh button
 *   - Split: source tree (left, virtualised I4) + SourcePreview (right)
 *
 * File tree behaviour:
 *   - Folders: collapsible, child count badge.
 *   - Files: Lucide icon by category, name, size, mtime.
 *   - Row click → select file + show preview.
 *   - Hover/selected row actions: Ingest + Delete (two-stage confirm).
 *
 * Two-stage delete:
 *   - First click arms the row (red "Confirm" state; 5s auto-disarm).
 *   - Only one row can be armed at a time (armedPath lifted to this view).
 *   - Second click fires DELETE /sources; success → refresh + clear preview.
 *
 * Index All button:
 *   - Calls POST /sources/ingest-all; toasts result or "already running".
 *   - While running: polls GET /sources/ingest-all/status (~1.5s chain, I3).
 *   - Label changes to "Indexing… done/total"; button is disabled + shows spinner.
 *   - Polling stops when running=false; tree is refreshed.
 *   - Polling also starts on mount so button reflects any in-progress scan.
 *
 * R7-11 — Bulk multi-select:
 *   - Checkbox per file row + select-all in header (AC-R7-11-1).
 *   - Bulk actions bar when >0 selected: "Ingest selected" + "Delete selected".
 *   - Delete selected uses ConfirmDialog (danger).
 *   - Execute sequentially with per-item progress indicator n/total (AC-R7-11-2/3/4).
 *   - Errors collected and shown as summary toast (AC-R7-11-2).
 *
 * INVARIANT I4: virtualised with @tanstack/react-virtual (mirrors NavTree).
 * INVARIANT I3: single setTimeout poll chain; no heavy per-render work.
 *
 * All strings: sources.* i18n keys.
 * All testids: sources-view, sources-tree, source-row, source-ingest,
 *              source-delete, source-refresh, sources-ingest-all,
 *              sources-ingest-all-progress, sources-bulk-bar, sources-bulk-ingest,
 *              sources-bulk-delete.
 */

import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type CSSProperties,
  type ChangeEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  RefreshCw,
  Upload,
  Folder,
  FolderOpen,
  File,
  FileText,
  Image,
  FileVideo,
  FileAudio,
  BookOpen,
  Trash2,
  ChevronRight,
  ChevronDown,
  Layers,
  Loader2,
} from "lucide-react";
import {
  listSources,
  deleteSource,
  deleteFolderSource,
  triggerIngest,
  ingestAllSources,
  getIngestAllStatus,
  IngestAllRunningError,
} from "../../api/sourcesClient";
import type { SourceEntry, SourceRoot } from "../../api/sourcesClient";
import { uploadDocument } from "../../api/ingestClient";
import { SourcePreview } from "./SourcePreview";
import { UploadZone } from "../ingest/UploadZone";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { showToast } from "../common/Toast";

// ─── Category icon helper ─────────────────────────────────────────────────────

function fileIcon(ext: string | undefined, size = 15) {
  const e = (ext ?? "").toLowerCase();
  if (/^\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$/.test(e))
    return <Image size={size} aria-hidden="true" />;
  if (/^\.(mp4|mkv|mov|avi|webm)$/.test(e))
    return <FileVideo size={size} aria-hidden="true" />;
  if (/^\.(mp3|wav|ogg|flac|aac|m4a)$/.test(e))
    return <FileAudio size={size} aria-hidden="true" />;
  if (/^\.(md|markdown|txt)$/.test(e))
    return <FileText size={size} aria-hidden="true" />;
  return <File size={size} aria-hidden="true" />;
}

function formatBytes(n: number): string {
  if (n < 1024)         return `${n} B`;
  if (n < 1024 * 1024)  return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMtime(mtime: string): string {
  // Backend returns an ISO-8601 string (e.g. "2026-06-28T07:27:37+00:00"), not an epoch.
  const d = new Date(mtime);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ─── Tree-row types ───────────────────────────────────────────────────────────

interface FolderRow {
  kind: "folder";
  path: string;
  name: string;
  depth: number;
  childCount: number;
  collapsed: boolean;
}

interface FileRow {
  kind: "file";
  path: string;
  name: string;
  ext: string | undefined;
  size_bytes: number | undefined;
  mtime: string | undefined;
  depth: number;
}

type TreeRow = FolderRow | FileRow;

// ─── Build flat virtualizer-ready rows from SourceEntry[] ─────────────────────

function buildRows(
  entries: SourceEntry[],
  collapsedFolders: Set<string>,
): TreeRow[] {
  // Group entries by parent directory
  const byParent = new Map<string, SourceEntry[]>();
  for (const e of entries) {
    const parent = e.path.includes("/")
      ? e.path.slice(0, e.path.lastIndexOf("/"))
      : "";
    if (!byParent.has(parent)) byParent.set(parent, []);
    const bucket = byParent.get(parent);
    if (bucket) bucket.push(e);
  }

  const rows: TreeRow[] = [];

  function visit(parentPath: string, depth: number) {
    const children = byParent.get(parentPath) ?? [];
    // Sort: folders first, then files alphabetically
    const sorted = [...children].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

    for (const entry of sorted) {
      if (entry.is_dir) {
        const childEntries = byParent.get(entry.path) ?? [];
        rows.push({
          kind: "folder",
          path: entry.path,
          name: entry.name,
          depth,
          childCount: childEntries.length,
          collapsed: collapsedFolders.has(entry.path),
        });
        if (!collapsedFolders.has(entry.path)) {
          visit(entry.path, depth + 1);
        }
      } else {
        rows.push({
          kind: "file",
          path: entry.path,
          name: entry.name,
          ext: entry.ext,
          size_bytes: entry.size_bytes,
          mtime: entry.mtime,
          depth,
        });
      }
    }
  }

  visit("", 0);
  return rows;
}

// ─── Row heights ──────────────────────────────────────────────────────────────

const FOLDER_ROW_H = 30;
const FILE_ROW_H   = 30;
const DISARM_DELAY = 5000; // ms
const INGEST_ALL_POLL_MS = 1500; // I3: single setTimeout chain interval

// ─── Accepted extensions (mirrors UploadZone) ─────────────────────────────────

const ACCEPTED_EXTS = new Set([".md", ".txt", ".markdown", ".pdf", ".docx", ".pptx", ".xlsx"]);

function getExt(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

// ─── Module-level reduced-motion detection (mirrors ActivityBar/GraphViewer) ──

const reducedMotion: boolean =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── IngestAllProgress — local state type ────────────────────────────────────

interface IngestAllProgress {
  running: boolean;
  done: number;
  total: number;
}

// ─── BulkProgress — per-item progress for sequential bulk ops ────────────────

interface BulkProgress {
  current: number;
  total: number;
  currentPath: string;
}

// ─── FolderUploadProgress — progress for "+ Folder" sequential upload (S1) ───

interface FolderUploadProgress {
  current: number;
  total: number;
  currentName: string;
}

// ─── SourcesView ──────────────────────────────────────────────────────────────

export function SourcesView() {
  const { t } = useTranslation();

  // ── Tab / root state ─────────────────────────────────────────────────────────
  // Session-only (not persisted). "sources" = raw/sources/ (default, read-write).
  // "wiki" = vault's wiki/ folder (read-only tree + preview).
  const [root, setRoot] = useState<SourceRoot>("sources");
  const isWiki = root === "wiki";

  const [entries, setEntries]               = useState<SourceEntry[]>([]);
  const [total, setTotal]                   = useState<number>(0);
  const [loading, setLoading]               = useState(false);
  const [error, setError]                   = useState<string | null>(null);
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath]     = useState<string | null>(null);
  const [showImport, setShowImport]         = useState(false);
  // Two-stage delete (files and folders share the same armed/deleting state)
  const [armedPath, setArmedPath]           = useState<string | null>(null);
  const [deletingPath, setDeletingPath]     = useState<string | null>(null);
  // Per-file ingest in-flight
  const [ingestingPath, setIngestingPath]   = useState<string | null>(null);
  // Disarm timer ref
  const disarmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Ingest-all progress state (null = not running / idle)
  const [ingestAllProgress, setIngestAllProgress] = useState<IngestAllProgress | null>(null);
  // Single setTimeout poll chain ref (I3)
  const ingestAllPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // S1: folder upload (hidden directory input + sequential progress)
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [folderUploadProgress, setFolderUploadProgress] = useState<FolderUploadProgress | null>(null);

  // R7-11: multi-select state
  const [selectedPaths, setSelectedPaths]   = useState<Set<string>>(new Set());
  const [showBulkDeleteDialog, setShowBulkDeleteDialog] = useState(false);
  const [bulkProgress, setBulkProgress]     = useState<BulkProgress | null>(null);

  // ── Fetch ────────────────────────────────────────────────────────────────────

  const fetchSources = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSources(signal, root);
      setEntries(res.entries);
      setTotal(res.total);
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [root]);

  // Re-fetch whenever root changes; also reset tree state (selection, collapse).
  useEffect(() => {
    setSelectedPath(null);
    setCollapsedFolders(new Set());
    setSelectedPaths(new Set());
    const ctrl = new AbortController();
    void fetchSources(ctrl.signal);
    return () => ctrl.abort();
  }, [fetchSources]);

  // ── Ingest-all polling ───────────────────────────────────────────────────────

  const stopIngestAllPoll = useCallback(() => {
    if (ingestAllPollRef.current) {
      clearTimeout(ingestAllPollRef.current);
      ingestAllPollRef.current = null;
    }
  }, []);

  const scheduleIngestAllPoll = useCallback(() => {
    stopIngestAllPoll();
    ingestAllPollRef.current = setTimeout(() => {
      void (async () => {
        try {
          const status = await getIngestAllStatus();
          if (status.running) {
            setIngestAllProgress({ running: true, done: status.done, total: status.total });
            scheduleIngestAllPoll();
          } else {
            setIngestAllProgress(null);
            // Refresh the tree once the scan finishes
            void fetchSources();
          }
        } catch {
          // Network error while polling — clear state, let user retry manually
          setIngestAllProgress(null);
        }
      })();
    }, INGEST_ALL_POLL_MS);
  }, [stopIngestAllPoll, fetchSources]);

  // On mount: check if a scan is already running and start polling if so
  useEffect(() => {
    void (async () => {
      try {
        const status = await getIngestAllStatus();
        if (status.running) {
          setIngestAllProgress({ running: true, done: status.done, total: status.total });
          scheduleIngestAllPoll();
        }
      } catch {
        // Backend unreachable — ignore, button will be in idle state
      }
    })();
    return () => { stopIngestAllPoll(); };
  }, [scheduleIngestAllPoll, stopIngestAllPoll]);

  const handleIngestAll = useCallback(async () => {
    try {
      const res = await ingestAllSources();
      if (!res.started) {
        showToast(t("sources.ingestAllNone"), "success");
        return;
      }
      showToast(t("sources.ingestAllStarted", { count: res.candidate_files }), "success");
      // Immediately mark running and start polling
      setIngestAllProgress({ running: true, done: 0, total: res.candidate_files });
      scheduleIngestAllPoll();
    } catch (err: unknown) {
      if (err instanceof IngestAllRunningError) {
        showToast(t("sources.ingestAllAlready"), "success");
        // Start polling since it's already running
        setIngestAllProgress({ running: true, done: 0, total: 0 });
        scheduleIngestAllPoll();
        return;
      }
      showToast(err instanceof Error ? err.message : String(err), "error");
    }
  }, [t, scheduleIngestAllPoll]);

  // ── Two-stage delete helpers ─────────────────────────────────────────────────

  const armDelete = useCallback(
    (path: string) => {
      if (disarmTimerRef.current) clearTimeout(disarmTimerRef.current);
      setArmedPath(path);
      disarmTimerRef.current = setTimeout(() => setArmedPath(null), DISARM_DELAY);
    },
    [],
  );

  const handleDeleteClick = useCallback(
    async (path: string) => {
      if (armedPath !== path) {
        armDelete(path);
        return;
      }
      // Second click — fire delete
      if (disarmTimerRef.current) clearTimeout(disarmTimerRef.current);
      setArmedPath(null);
      setDeletingPath(path);
      try {
        const res = await deleteSource(path);
        showToast(
          t("sources.deletedToast", { pages: res.pages_deleted }),
          "success",
        );
        if (selectedPath === path) setSelectedPath(null);
        // Also remove from selection if selected
        setSelectedPaths((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
        await fetchSources();
      } catch (err: unknown) {
        showToast(err instanceof Error ? err.message : String(err), "error");
      } finally {
        setDeletingPath(null);
      }
    },
    [armedPath, armDelete, fetchSources, selectedPath, t],
  );

  // ── Ingest ───────────────────────────────────────────────────────────────────

  const handleIngest = useCallback(
    async (path: string) => {
      setIngestingPath(path);
      try {
        await triggerIngest(`raw/sources/${path}`);
        showToast(t("sources.ingestedToast"), "success");
      } catch (err: unknown) {
        showToast(err instanceof Error ? err.message : String(err), "error");
      } finally {
        setIngestingPath(null);
      }
    },
    [t],
  );

  // ── S1: Folder upload ────────────────────────────────────────────────────────
  // Sequential per-file upload preserving relative directory structure.
  // Reuses the existing bulk-progress bar pattern (I3: event-driven, not per-token).

  const handleFolderInputChange = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      // Filter to accepted extensions; skip unsupported silently (count for toast).
      const accepted: File[] = [];
      const skipped: string[] = [];
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (!file) continue;
        if (ACCEPTED_EXTS.has(getExt(file.name))) {
          accepted.push(file);
        } else {
          skipped.push(file.name);
        }
      }

      if (skipped.length > 0) {
        showToast(t("sources.folderUploadSkipped", { count: skipped.length }), "error");
      }
      if (accepted.length === 0) {
        // Reset so the same folder can be re-selected
        if (folderInputRef.current) folderInputRef.current.value = "";
        return;
      }

      const total = accepted.length;
      for (let i = 0; i < accepted.length; i++) {
        const file = accepted[i];
        if (!file) continue;
        setFolderUploadProgress({ current: i + 1, total, currentName: file.name });
        // Derive rel_dir from webkitRelativePath: strip the filename from the end.
        // webkitRelativePath = "folderName/sub/file.txt" → rel_dir = "folderName/sub"
        const relPath = (file as File & { webkitRelativePath?: string }).webkitRelativePath ?? "";
        const lastSlash = relPath.lastIndexOf("/");
        const relDir = lastSlash > 0 ? relPath.slice(0, lastSlash) : undefined;
        try {
          await uploadDocument(file, undefined, relDir);
        } catch (err: unknown) {
          console.error("[sources] folder upload error for", file.name, err);
          // Continue uploading remaining files on per-file error.
        }
      }

      setFolderUploadProgress(null);
      if (folderInputRef.current) folderInputRef.current.value = "";
      void fetchSources();
    },
    [t, fetchSources],
  );

  // ── S2: Folder delete (two-stage armed-confirm pattern) ──────────────────────

  const handleFolderDeleteClick = useCallback(
    async (path: string) => {
      if (armedPath !== path) {
        armDelete(path);
        return;
      }
      // Second click — fire delete
      if (disarmTimerRef.current) clearTimeout(disarmTimerRef.current);
      setArmedPath(null);
      setDeletingPath(path);
      try {
        const res = await deleteFolderSource(path);
        showToast(
          t("sources.deletedFolderToast", { files: res.files_deleted, pages: res.pages_cascaded }),
          "success",
        );
        if (selectedPath?.startsWith(path + "/") || selectedPath === path) setSelectedPath(null);
        await fetchSources();
      } catch (err: unknown) {
        // 409 = directory too large; surface the dedicated message.
        const isTooBig =
          err instanceof Error && "status" in err && (err as { status?: number }).status === 409;
        showToast(
          isTooBig
            ? t("sources.deletedFolderTooMany")
            : err instanceof Error ? err.message : String(err),
          "error",
        );
      } finally {
        setDeletingPath(null);
      }
    },
    [armedPath, armDelete, fetchSources, selectedPath, t],
  );

  // ── R7-11: Bulk selection helpers ────────────────────────────────────────────

  // All file paths in current rows (for select-all)
  const rows = buildRows(entries, collapsedFolders);
  const allFilePaths = rows
    .filter((r): r is FileRow => r.kind === "file")
    .map((r) => r.path);

  const allSelected = allFilePaths.length > 0 && allFilePaths.every((p) => selectedPaths.has(p));
  const someSelected = selectedPaths.size > 0;

  const handleSelectAll = useCallback(() => {
    if (allSelected) {
      setSelectedPaths(new Set());
    } else {
      setSelectedPaths(new Set(allFilePaths));
    }
  }, [allSelected, allFilePaths]);

  const handleToggleSelect = useCallback((path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // R7-11 AC-R7-11-2: "Ingest selected" — sequential, per-item progress
  const handleBulkIngest = useCallback(async () => {
    const paths = [...selectedPaths];
    const total = paths.length;
    const errors: string[] = [];
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      if (!path) continue;
      setBulkProgress({ current: i + 1, total, currentPath: path });
      try {
        await triggerIngest(`raw/sources/${path}`);
      } catch (err: unknown) {
        errors.push(path);
        console.error("[sources] bulk ingest error for", path, err);
      }
    }
    setBulkProgress(null);
    setSelectedPaths(new Set());
    if (errors.length === 0) {
      showToast(t("sources.bulk.ingestDone", { count: total }), "success");
    } else {
      showToast(
        t("sources.bulk.ingestPartial", { done: total - errors.length, total, failed: errors.length }),
        "error",
      );
    }
  }, [selectedPaths, t]);

  // R7-11 AC-R7-11-3: "Delete selected" — sequential after confirmation
  const handleBulkDeleteConfirm = useCallback(async () => {
    setShowBulkDeleteDialog(false);
    const paths = [...selectedPaths];
    const total = paths.length;
    const errors: string[] = [];
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      if (!path) continue;
      setBulkProgress({ current: i + 1, total, currentPath: path });
      try {
        await deleteSource(path);
        if (selectedPath === path) setSelectedPath(null);
      } catch (err: unknown) {
        errors.push(path);
        console.error("[sources] bulk delete error for", path, err);
      }
    }
    setBulkProgress(null);
    setSelectedPaths(new Set());
    await fetchSources();
    if (errors.length === 0) {
      showToast(t("sources.bulk.deleteDone", { count: total }), "success");
    } else {
      showToast(
        t("sources.bulk.deletePartial", { done: total - errors.length, total, failed: errors.length }),
        "error",
      );
    }
  }, [selectedPaths, selectedPath, fetchSources, t]);

  // ── Tree rows ────────────────────────────────────────────────────────────────

  const toggleFolder = useCallback((path: string) => {
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // ── Virtualizer ──────────────────────────────────────────────────────────────

  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => {
      const r = rows[i];
      return r?.kind === "folder" ? FOLDER_ROW_H : FILE_ROW_H;
    },
    overscan: 8,
  });

  // ── Empty / Error states ──────────────────────────────────────────────────────

  const isEmpty = !loading && !error && entries.length === 0;

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="sources-view"
      style={OUTER_STYLE}
    >
      {/* ── Tab toggle (Sources / Wiki) ── */}
      <div
        data-testid="sources-tab-bar"
        style={TAB_BAR_STYLE}
      >
        <button
          data-testid="sources-tab-sources"
          style={tabBtnStyle(root === "sources")}
          onClick={() => setRoot("sources")}
          aria-pressed={root === "sources"}
        >
          {t("sources.tabSources")}
        </button>
        <button
          data-testid="sources-tab-wiki"
          style={tabBtnStyle(root === "wiki")}
          onClick={() => setRoot("wiki")}
          aria-pressed={root === "wiki"}
        >
          {t("sources.tabWiki")}
        </button>
        {isWiki && (
          <span style={WIKI_BADGE_STYLE}>
            {t("sources.wikiReadOnly")}
          </span>
        )}
      </div>

      {/* ── Header ── */}
      <div style={HEADER_STYLE}>
        <span style={{ fontWeight: 600, fontSize: 15, color: "var(--syn-text)" }}>
          {t("sources.title")}
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          {/* Index All button — hidden in wiki tab (read-only) */}
          {!isWiki && (
            <button
              data-testid="sources-ingest-all"
              style={{
                ...HEADER_BTN_STYLE,
                opacity: ingestAllProgress?.running ? 0.7 : 1,
              }}
              onClick={() => { void handleIngestAll(); }}
              disabled={ingestAllProgress?.running === true}
              title={t("sources.ingestAll")}
            >
              {ingestAllProgress?.running && !reducedMotion ? (
                <Loader2
                  size={14}
                  aria-hidden="true"
                  style={{ animation: "synapse-spin 1s linear infinite" }}
                />
              ) : (
                <Layers size={14} aria-hidden="true" />
              )}
              {ingestAllProgress?.running ? (
                <span data-testid="sources-ingest-all-progress">
                  {t("sources.ingestAllRunning", {
                    done: ingestAllProgress.done,
                    total: ingestAllProgress.total,
                  })}
                </span>
              ) : (
                t("sources.ingestAll")
              )}
            </button>
          )}
          <button
            data-testid="source-refresh"
            style={HEADER_BTN_STYLE}
            onClick={() => { void fetchSources(); }}
            title={t("sources.refresh")}
            disabled={loading}
          >
            <RefreshCw size={14} aria-hidden="true" />
            {t("sources.refresh")}
          </button>
          {/* Import + Folder buttons — hidden in wiki tab (read-only) */}
          {!isWiki && (
            <>
              <button
                style={IMPORT_BTN_STYLE}
                onClick={() => setShowImport((v) => !v)}
                title={t("sources.import")}
              >
                <Upload size={14} aria-hidden="true" />
                {t("sources.import")}
              </button>
              {/* S1: "+ Folder" button — triggers hidden directory input */}
              <button
                data-testid="source-import-folder"
                style={IMPORT_BTN_STYLE}
                onClick={() => folderInputRef.current?.click()}
                title={t("sources.importFolder")}
                disabled={folderUploadProgress !== null}
              >
                <Folder size={14} aria-hidden="true" />
                {t("sources.importFolder")}
              </button>
              {/* Hidden directory input (S1) */}
              <input
                ref={folderInputRef}
                type="file"
                // webkitdirectory and multiple are non-standard but broadly supported
                {...{ webkitdirectory: "true" }}
                multiple
                style={{ display: "none" }}
                aria-hidden="true"
                tabIndex={-1}
                onChange={(e) => { void handleFolderInputChange(e); }}
              />
            </>
          )}
        </div>
        {/* Keyframe for spinner — injected once, harmless if duplicated */}
        <style>{`@keyframes synapse-spin { to { transform: rotate(360deg); } }`}</style>
      </div>

      {/* ── Import zone (collapsible) — sources tab only ── */}
      {!isWiki && showImport && (
        <div style={{ borderBottom: "1px solid var(--syn-border)", paddingBottom: 8 }}>
          <UploadZone onSuccess={() => { setShowImport(false); void fetchSources(); }} />
        </div>
      )}

      {/* ── R7-11: Bulk actions bar (appears when >0 selected, sources tab only) ── */}
      {!isWiki && someSelected && !bulkProgress && (
        <div
          data-testid="sources-bulk-bar"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 16px",
            background: "var(--syn-accent-soft)",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "var(--syn-accent)", fontWeight: 600 }}>
            {t("sources.bulk.selected", { count: selectedPaths.size })}
          </span>
          <button
            data-testid="sources-bulk-ingest"
            style={BULK_ACTION_BTN}
            onClick={() => { void handleBulkIngest(); }}
          >
            {t("sources.bulk.ingest")}
          </button>
          <button
            data-testid="sources-bulk-delete"
            style={{ ...BULK_ACTION_BTN, color: "var(--syn-red)", borderColor: "color-mix(in srgb, var(--syn-red) 30%, transparent 70%)" }}
            onClick={() => setShowBulkDeleteDialog(true)}
          >
            {t("sources.bulk.delete")}
          </button>
          <button
            style={{ ...BULK_ACTION_BTN, marginLeft: "auto" }}
            onClick={() => setSelectedPaths(new Set())}
          >
            {t("sources.bulk.clearSelection")}
          </button>
        </div>
      )}

      {/* ── Bulk progress indicator ── */}
      {bulkProgress && (
        <div
          data-testid="sources-bulk-progress"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 16px",
            background: "var(--syn-surface)",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
          }}
        >
          <Loader2 size={13} style={{ animation: "synapse-spin 1s linear infinite" }} aria-hidden="true" />
          <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
            {t("sources.bulk.progress", {
              current: bulkProgress.current,
              total: bulkProgress.total,
              path: bulkProgress.currentPath,
            })}
          </span>
        </div>
      )}

      {/* ── S1: Folder upload progress indicator ── */}
      {folderUploadProgress && (
        <div
          data-testid="sources-folder-upload-progress"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 16px",
            background: "var(--syn-surface)",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
          }}
        >
          <Loader2 size={13} style={{ animation: "synapse-spin 1s linear infinite" }} aria-hidden="true" />
          <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
            {t("sources.bulk.progress", {
              current: folderUploadProgress.current,
              total: folderUploadProgress.total,
              path: folderUploadProgress.currentName,
            })}
          </span>
        </div>
      )}

      {/* ── Body: tree + preview ── */}
      <div style={BODY_STYLE}>
        {/* ─ Tree ─ */}
        <div style={TREE_PANEL_STYLE}>
          {loading && (
            <div style={CENTER_STYLE}>
              <span style={{ color: "var(--syn-text-dim)", fontSize: 12 }}>{t("common.loading")}</span>
            </div>
          )}
          {error && (
            <div style={CENTER_STYLE}>
              <span style={{ color: "var(--syn-danger, #e53e3e)", fontSize: 12 }}>{error}</span>
            </div>
          )}
          {isEmpty && (
            <div style={EMPTY_STYLE}>
              <Folder size={28} aria-hidden="true" style={{ color: "var(--syn-text-dim)", marginBottom: 8 }} />
              <span style={{ color: "var(--syn-text-dim)", fontSize: 13, textAlign: "center" }}>
                {t("sources.emptyHint")}
              </span>
              <button
                style={IMPORT_BTN_STYLE}
                onClick={() => setShowImport(true)}
              >
                <Upload size={13} aria-hidden="true" />
                {t("sources.import")}
              </button>
            </div>
          )}
          {!loading && !error && rows.length > 0 && (
            <nav
              data-testid="sources-tree"
              aria-label={t("sources.title")}
              style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}
            >
              {/* R7-11: select-all header checkbox — sources tab only */}
              {!isWiki && (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 8px",
                    borderBottom: "1px solid var(--syn-border-subtle, var(--syn-border))",
                    flexShrink: 0,
                  }}
                >
                  <input
                    type="checkbox"
                    data-testid="sources-select-all"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected && !allSelected;
                    }}
                    onChange={handleSelectAll}
                    aria-label={t("sources.bulk.selectAll")}
                    style={{ cursor: "pointer" }}
                  />
                  <span style={{ fontSize: 10, color: "var(--syn-text-dim)", userSelect: "none" }}>
                    {t("sources.bulk.selectAll")}
                  </span>
                </div>
              )}

              <div
                ref={scrollRef}
                style={{ overflow: "auto", flex: 1, minHeight: 0 }}
              >
                <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                  {virtualizer.getVirtualItems().map((vRow) => {
                    const row = rows[vRow.index] as TreeRow;
                    const style: CSSProperties = {
                      position: "absolute",
                      top: vRow.start,
                      width: "100%",
                    };
                    if (row.kind === "folder") {
                      return (
                        <FolderRowItem
                          key={row.path}
                          row={row}
                          style={style}
                          armed={armedPath === row.path}
                          deleting={deletingPath === row.path}
                          readOnly={isWiki}
                          onToggle={toggleFolder}
                          onDelete={handleFolderDeleteClick}
                        />
                      );
                    }
                    return (
                      <FileRowItem
                        key={row.path}
                        row={row}
                        selected={row.path === selectedPath}
                        checked={selectedPaths.has(row.path)}
                        armed={armedPath === row.path}
                        deleting={deletingPath === row.path}
                        ingesting={ingestingPath === row.path}
                        readOnly={isWiki}
                        style={style}
                        onClick={() => setSelectedPath(row.path)}
                        onToggleCheck={() => handleToggleSelect(row.path)}
                        onIngest={handleIngest}
                        onDelete={handleDeleteClick}
                      />
                    );
                  })}
                </div>
              </div>
            </nav>
          )}
        </div>

        {/* ─ Divider ─ */}
        <div style={DIVIDER_STYLE} />

        {/* ─ Preview ─ */}
        <div style={PREVIEW_PANEL_STYLE}>
          <SourcePreview path={selectedPath} root={root} />
        </div>
      </div>

      {/* ── S3: Footer count bar ── */}
      <div
        data-testid="sources-footer"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          padding: "4px 16px",
          borderTop: "1px solid var(--syn-border)",
          background: "var(--syn-bg-soft)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("sources.footerCount", { total })}
        </span>
      </div>

      {/* ── R7-11: Bulk delete confirmation dialog ── */}
      {showBulkDeleteDialog && (
        <ConfirmDialog
          title={t("sources.bulk.deleteDialogTitle")}
          body={t("sources.bulk.deleteDialogBody", { count: selectedPaths.size })}
          confirmLabel={t("sources.bulk.deleteConfirm")}
          cancelLabel={t("sources.bulk.deleteCancel")}
          danger
          onConfirm={() => { void handleBulkDeleteConfirm(); }}
          onCancel={() => setShowBulkDeleteDialog(false)}
        />
      )}
    </div>
  );
}

// ─── FolderRowItem ────────────────────────────────────────────────────────────

interface FolderRowItemProps {
  row: FolderRow;
  style: CSSProperties;
  armed: boolean;
  deleting: boolean;
  /** When true (wiki tab), the folder delete button is hidden. */
  readOnly?: boolean;
  onToggle: (path: string) => void;
  onDelete: (path: string) => Promise<void>;
}

function FolderRowItem({ row, style, armed, deleting, readOnly = false, onToggle, onDelete }: FolderRowItemProps) {
  const { t } = useTranslation();
  const indent = 8 + row.depth * 16;
  const expanded = !row.collapsed;
  return (
    <div
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: FOLDER_ROW_H,
        padding: `0 6px 0 ${indent}px`,
        gap: 5,
        userSelect: "none",
      }}
      role="row"
    >
      {/* Toggle area (expand/collapse) */}
      <button
        style={{
          display: "flex",
          alignItems: "center",
          flex: 1,
          minWidth: 0,
          border: "none",
          background: "transparent",
          cursor: "pointer",
          textAlign: "left",
          gap: 5,
          color: "var(--syn-text-muted)",
          fontSize: 12,
          fontWeight: 600,
          padding: 0,
          overflow: "hidden",
        }}
        aria-expanded={expanded}
        aria-label={`${row.name}, ${row.childCount} ${t("sources.folder")}`}
        onClick={() => onToggle(row.path)}
      >
        {expanded
          ? <ChevronDown size={12} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />
          : <ChevronRight size={12} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />}
        {expanded
          ? <FolderOpen size={14} aria-hidden="true" style={{ color: "var(--syn-accent)", flexShrink: 0 }} />
          : <Folder size={14} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />}
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {row.name}
        </span>
        <span style={{
          fontSize: 10,
          color: "var(--syn-text-dim)",
          background: "var(--syn-surface-sunken)",
          border: "1px solid var(--syn-border-subtle)",
          borderRadius: 10,
          padding: "1px 5px",
          flexShrink: 0,
        }}>
          {row.childCount}
        </span>
      </button>

      {/* S2: Folder delete (two-stage armed-confirm) — hidden in wiki tab */}
      {!readOnly && (
        <button
          data-testid="source-folder-delete"
          style={{
            ...ACTION_BTN_BASE,
            color: armed ? "var(--syn-danger, #e53e3e)" : "var(--syn-text-dim)",
            background: armed
              ? "color-mix(in srgb, var(--syn-danger, #e53e3e) 10%, transparent 90%)"
              : "transparent",
            opacity: deleting ? 0.5 : 1,
            minWidth: armed ? 64 : undefined,
            fontSize: armed ? 10 : undefined,
            fontWeight: armed ? 700 : undefined,
            transition: "color 0.15s, background 0.15s",
            flexShrink: 0,
          }}
          disabled={deleting}
          onClick={(e) => { e.stopPropagation(); void onDelete(row.path); }}
          title={armed ? t("sources.confirmDeleteFolder") : t("sources.deleteFolder")}
          aria-label={armed
            ? `${t("sources.confirmDeleteFolder")} ${row.name}`
            : `${t("sources.deleteFolder")} ${row.name}`}
        >
          {armed ? t("sources.confirmDeleteFolder") : <Trash2 size={12} aria-hidden="true" />}
        </button>
      )}
    </div>
  );
}

// ─── FileRowItem ──────────────────────────────────────────────────────────────

interface FileRowItemProps {
  row: FileRow;
  selected: boolean;
  /** R7-11: whether this row is in the bulk selection set */
  checked: boolean;
  armed: boolean;
  deleting: boolean;
  ingesting: boolean;
  /** When true (wiki tab), checkbox, Ingest, and Delete buttons are hidden. */
  readOnly?: boolean;
  style: CSSProperties;
  onClick: () => void;
  /** R7-11: toggle checkbox */
  onToggleCheck: () => void;
  onIngest: (path: string) => void;
  onDelete: (path: string) => Promise<void>;
}

/**
 * Filename with MIDDLE truncation. End-ellipsis on a narrow column eats the most
 * informative part of a source name — "01_Strategic_brief.md" collapses to
 * "01_Str…", indistinguishable from its siblings. Here the numeric/topic prefix
 * AND the extension stay pinned; only the middle collapses ("01_Strateg…brief.md").
 * Pure CSS: a flex row of two spans, no width measurement.
 */
const NAME_TAIL_LEN = 9;
function FileName({ name, selected, title }: { name: string; selected: boolean; title: string }) {
  const useMiddle = name.length > NAME_TAIL_LEN * 2;
  const tail = useMiddle ? name.slice(-NAME_TAIL_LEN) : "";
  const head = useMiddle ? name.slice(0, name.length - NAME_TAIL_LEN) : name;
  return (
    <span
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        fontSize: 12,
        color: selected ? "var(--syn-accent)" : "var(--syn-text-muted)",
      }}
      title={title}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
        {head}
      </span>
      {tail && <span style={{ whiteSpace: "nowrap", flexShrink: 0 }}>{tail}</span>}
    </span>
  );
}

function FileRowItem({
  row,
  selected,
  checked,
  armed,
  deleting,
  ingesting,
  readOnly = false,
  style,
  onClick,
  onToggleCheck,
  onIngest,
  onDelete,
}: FileRowItemProps) {
  const { t } = useTranslation();
  const indent = 8 + row.depth * 16;

  return (
    <div
      data-testid="source-row"
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        height: FILE_ROW_H,
        padding: `0 6px 0 ${indent}px`,
        background: selected ? "var(--syn-accent-soft)" : "transparent",
        cursor: "pointer",
        gap: 5,
        transition: "background 0.1s ease",
      }}
      onClick={onClick}
      role="row"
      aria-selected={selected}
      data-path={row.path}
    >
      {/* R7-11: per-row checkbox — hidden in wiki tab */}
      {!readOnly && (
        <input
          type="checkbox"
          data-testid="source-row-checkbox"
          checked={checked}
          onChange={(e) => { e.stopPropagation(); onToggleCheck(); }}
          onClick={(e) => e.stopPropagation()}
          aria-label={t("sources.bulk.selectFile", { name: row.name })}
          style={{ flexShrink: 0, cursor: "pointer" }}
        />
      )}

      {/* Icon */}
      <span style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}>
        {fileIcon(row.ext)}
      </span>
      {/* Name — middle-truncated so prefix + extension stay legible. The date moved
          into the tooltip so it no longer starves the (narrow) name column; the size
          stays inline as the one metric worth scanning at a glance. */}
      <FileName
        name={row.name}
        selected={selected}
        title={
          row.mtime !== undefined
            ? `${row.path}\n${formatMtime(row.mtime)}`
            : row.path
        }
      />
      {/* Size */}
      {row.size_bytes !== undefined && (
        <span style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}>
          {formatBytes(row.size_bytes)}
        </span>
      )}

      {/* Action buttons — hidden in wiki tab (read-only) */}
      {!readOnly && (
        <div
          style={{ display: "flex", gap: 3, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Ingest */}
          <button
            data-testid="source-ingest"
            style={{
              ...ACTION_BTN_BASE,
              color: "var(--syn-accent)",
              opacity: ingesting ? 0.5 : 1,
            }}
            disabled={ingesting}
            onClick={(e) => { e.stopPropagation(); onIngest(row.path); }}
            title={t("sources.ingest")}
            aria-label={`${t("sources.ingest")} ${row.name}`}
          >
            <BookOpen size={12} aria-hidden="true" />
          </button>

          {/* Delete (two-stage) */}
          <button
            data-testid="source-delete"
            style={{
              ...ACTION_BTN_BASE,
              color: armed
                ? "var(--syn-danger, #e53e3e)"
                : "var(--syn-text-dim)",
              background: armed
                ? "color-mix(in srgb, var(--syn-danger, #e53e3e) 10%, transparent 90%)"
                : "transparent",
              opacity: deleting ? 0.5 : 1,
              minWidth: armed ? 64 : undefined,
              fontSize: armed ? 10 : undefined,
              fontWeight: armed ? 700 : undefined,
              transition: "color 0.15s, background 0.15s",
            }}
            disabled={deleting}
            onClick={(e) => { e.stopPropagation(); void onDelete(row.path); }}
            title={armed ? t("sources.confirmDelete") : t("sources.delete")}
            aria-label={armed
              ? `${t("sources.confirmDelete")} ${row.name}`
              : `${t("sources.delete")} ${row.name}`}
          >
            {armed ? t("sources.confirmDelete") : <Trash2 size={12} aria-hidden="true" />}
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Inline styles ────────────────────────────────────────────────────────────

const OUTER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  width: "100%",
  height: "100%",
  overflow: "hidden",
  background: "var(--syn-bg)",
};

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "10px 16px",
  borderBottom: "1px solid var(--syn-border)",
  background: "var(--syn-bg-soft)",
  flexShrink: 0,
  gap: 8,
};

const BODY_STYLE: CSSProperties = {
  display: "flex",
  flex: 1,
  overflow: "hidden",
};

const TREE_PANEL_STYLE: CSSProperties = {
  width: 280,
  flexShrink: 0,
  borderRight: "1px solid var(--syn-border)",
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
  background: "var(--syn-bg-soft)",
};

const DIVIDER_STYLE: CSSProperties = {
  width: 1,
  background: "var(--syn-border)",
  flexShrink: 0,
};

const PREVIEW_PANEL_STYLE: CSSProperties = {
  flex: 1,
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
};

const CENTER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
};

const EMPTY_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  gap: 8,
  padding: 24,
};

const HEADER_BTN_STYLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 12,
  padding: "5px 10px",
  border: "1px solid var(--syn-border)",
  borderRadius: "var(--syn-radius-md, 6px)",
  background: "transparent",
  color: "var(--syn-text-muted)",
  cursor: "pointer",
};

const IMPORT_BTN_STYLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 12,
  padding: "5px 10px",
  border: "1px solid var(--syn-accent)",
  borderRadius: "var(--syn-radius-md, 6px)",
  background: "var(--syn-accent-soft)",
  color: "var(--syn-accent)",
  cursor: "pointer",
};

const ACTION_BTN_BASE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  border: "none",
  borderRadius: "var(--syn-radius-sm, 4px)",
  background: "transparent",
  cursor: "pointer",
  padding: "3px 5px",
};

const BULK_ACTION_BTN: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  fontSize: 11,
  padding: "4px 10px",
  border: "1px solid var(--syn-border)",
  borderRadius: 4,
  background: "transparent",
  color: "var(--syn-text-muted)",
  cursor: "pointer",
};

// ─── Tab toggle styles ────────────────────────────────────────────────────────

const TAB_BAR_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 0,
  padding: "0 12px",
  borderBottom: "1px solid var(--syn-border)",
  background: "var(--syn-bg-soft)",
  flexShrink: 0,
};

function tabBtnStyle(active: boolean): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    fontSize: 12,
    fontWeight: active ? 600 : 400,
    padding: "7px 12px",
    border: "none",
    borderBottom: active ? "2px solid var(--syn-accent)" : "2px solid transparent",
    background: "transparent",
    color: active ? "var(--syn-accent)" : "var(--syn-text-dim)",
    cursor: "pointer",
    transition: "color 0.12s, border-bottom-color 0.12s",
    marginBottom: "-1px", // overlap the container border
  };
}

const WIKI_BADGE_STYLE: CSSProperties = {
  marginLeft: 8,
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  padding: "2px 6px",
  borderRadius: "var(--syn-radius-sm, 4px)",
  background: "var(--syn-surface-sunken)",
  border: "1px solid var(--syn-border-subtle)",
  color: "var(--syn-text-dim)",
};
