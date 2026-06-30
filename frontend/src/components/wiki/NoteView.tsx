/**
 * NoteView.tsx — wiki page reader + CodeMirror 6 editor for the 'pages' section.
 *
 * INVARIANT I3 compliance:
 *   - Markdown is rendered (via renderMarkdown) ONCE when entering READ mode.
 *     It is NOT re-rendered per keystroke. CodeMirrorEditor owns its internal state;
 *     no keystroke is mirrored into Zustand or causes a React re-render here.
 *   - Zustand subscribed via typed scalar selectors only.
 *
 * INVARIANT I4 compliance:
 *   - Editor is CodeMirror 6 ONLY (via CodeMirrorEditor.tsx). No ProseMirror/Milkdown.
 *
 * INVARIANT I2: no force-layout anywhere here (graph concern is separate).
 *
 * Optimistic-concurrency (409 stale-conflict handling):
 *   - PUT sends expected_hash from the last GET.
 *   - 200 OK  → update stored hash, switch to READ, toast "Saved".
 *   - 409     → stale-conflict banner + "Reload" button (re-GET fresh content).
 *   - other   → error toast.
 *
 * CodeMirror is isolated in CodeMirrorEditor.tsx so tests can vi.mock that file
 * without loading the 4 MB CM bundle into jsdom.
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  memo,
  type CSSProperties,
} from "react";
import { useTranslation } from "react-i18next";

import { useGraphStore, selectSelectedNodeId } from "../../store/graphStore";
import { fetchPageContent, savePageContent } from "../../api/pagesClient";
import { ApiError } from "../../api/graphClient";
import { renderMarkdown } from "../chat/renderMarkdown";
import { EmptyState } from "../common/EmptyState";
import { showToast } from "../common/Toast";
import { CodeMirrorEditor } from "./CodeMirrorEditor";
import type { CodeMirrorEditorHandle } from "./CodeMirrorEditor";
import type { PageContentResponse } from "../../api/types";

// ─── READ-mode markdown renderer (I3: memoised on content string) ─────────────

interface MarkdownBodyProps {
  html: string;
}

const MarkdownBody = memo(function MarkdownBody({ html }: MarkdownBodyProps) {
  return (
    <div
      className="note-view__body"
      // renderMarkdown already runs DOMPurify — safe for dangerouslySetInnerHTML.
      dangerouslySetInnerHTML={{ __html: html }}
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "16px 20px",
        color: "#e6edf3",
        fontSize: 14,
        lineHeight: 1.7,
        fontFamily: "'Inter', system-ui, sans-serif",
      }}
    />
  );
});

// ─── Component ────────────────────────────────────────────────────────────────

type NoteViewMode = "read" | "edit";

interface NoteViewState {
  phase: "idle" | "loading" | "ready" | "error";
  data: PageContentResponse | null;
  errorMessage: string | null;
}

export function NoteView() {
  const { t } = useTranslation();
  const selectedNodeId = useGraphStore(selectSelectedNodeId);

  const [state, setState] = useState<NoteViewState>({
    phase: "idle",
    data: null,
    errorMessage: null,
  });
  const [mode, setMode] = useState<NoteViewMode>("read");
  const [isSaving, setIsSaving] = useState(false);

  // Handle to the CodeMirror editor — valid only while mode === "edit"
  const editorHandleRef = useRef<CodeMirrorEditorHandle | null>(null);

  // Memoised rendered HTML — computed ONCE when entering read mode (I3).
  // useMemo key: state.data reference changes only when we receive fresh content.
  const renderedHtml = useMemo(() => {
    if (state.phase !== "ready" || !state.data) return "";
    return renderMarkdown(state.data.content);
  }, [state.phase, state.data]);

  // ── Fetch on selection change ──────────────────────────────────────────────

  const loadPage = useCallback(
    (pageId: string, abortSignal: AbortSignal) => {
      setState({ phase: "loading", data: null, errorMessage: null });
      setMode("read");

      fetchPageContent(pageId, abortSignal)
        .then((data) => {
          setState({ phase: "ready", data, errorMessage: null });
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          if (err instanceof Error && err.name === "AbortError") return;
          const msg =
            err instanceof ApiError
              ? `${t("noteView.loadError")}: ${err.message}`
              : t("noteView.loadError");
          setState({ phase: "error", data: null, errorMessage: msg });
        });
    },
    [t],
  );

  useEffect(() => {
    if (!selectedNodeId) {
      setState({ phase: "idle", data: null, errorMessage: null });
      setMode("read");
      return;
    }
    const controller = new AbortController();
    loadPage(selectedNodeId, controller.signal);
    return () => {
      controller.abort();
    };
  }, [selectedNodeId, loadPage]);

  // ── Edit / Cancel ──────────────────────────────────────────────────────────

  const handleEdit = useCallback(() => {
    setMode("edit");
  }, []);

  const handleCancel = useCallback(() => {
    setMode("read");
  }, []);

  // ── Save ───────────────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    if (!state.data || !selectedNodeId) return;
    const handle = editorHandleRef.current;
    if (!handle) return;

    const newContent = handle.getContent();
    const expectedHash = state.data.content_hash;

    setIsSaving(true);
    try {
      const result = await savePageContent(selectedNodeId, newContent, expectedHash);
      // Update stored data with new content + hash so next read-mode render is fresh.
      setState((prev) => ({
        ...prev,
        errorMessage: null,
        data: prev.data
          ? {
              ...prev.data,
              content: newContent,
              content_hash: result.content_hash,
              updated_at: result.updated_at,
            }
          : null,
      }));
      setMode("read");
      showToast(t("noteView.saved"), "success");
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        // Stale conflict — surface the reload affordance in the UI.
        showToast(t("noteView.staleConflict"), "error");
        setState((prev) => ({ ...prev, errorMessage: "stale" }));
      } else {
        const msg =
          err instanceof ApiError ? err.message : t("noteView.loadError");
        showToast(msg, "error");
      }
    } finally {
      setIsSaving(false);
    }
  }, [state.data, selectedNodeId, t]);

  // ── Stale-conflict reload ──────────────────────────────────────────────────

  const handleReload = useCallback(() => {
    if (!selectedNodeId) return;
    setState((prev) => ({ ...prev, errorMessage: null }));
    setMode("read");
    const controller = new AbortController();
    loadPage(selectedNodeId, controller.signal);
  }, [selectedNodeId, loadPage]);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!selectedNodeId) {
    return (
      <div data-testid="note-view" style={ROOT_STYLE}>
        <EmptyState
          title={t("noteView.selectPagePrompt")}
          body={t("noteView.selectPageBody")}
          testId="note-view-empty"
        />
      </div>
    );
  }

  if (state.phase === "loading") {
    return (
      <div data-testid="note-view" style={ROOT_STYLE}>
        <div style={LOADING_STYLE} role="status" aria-label={t("common.loading")}>
          <span style={SPINNER_STYLE} aria-hidden="true" />
          <span>{t("common.loading")}</span>
        </div>
      </div>
    );
  }

  if (state.phase === "error" && state.errorMessage !== "stale") {
    const errorBody = state.errorMessage ?? undefined;
    return (
      <div data-testid="note-view" style={ROOT_STYLE}>
        <EmptyState
          title={t("noteView.loadError")}
          {...(errorBody !== undefined ? { body: errorBody } : {})}
          testId="note-view-error"
          actions={[
            {
              label: t("common.retry"),
              onClick: () => {
                if (selectedNodeId) {
                  const controller = new AbortController();
                  loadPage(selectedNodeId, controller.signal);
                }
              },
              variant: "secondary",
            },
          ]}
        />
      </div>
    );
  }

  if (state.phase !== "ready" || !state.data) {
    return <div data-testid="note-view" style={ROOT_STYLE} />;
  }

  const { data } = state;
  const isStale = state.errorMessage === "stale";

  return (
    <div data-testid="note-view" style={ROOT_STYLE}>
      {/* ── Toolbar ── */}
      <div style={TOOLBAR_STYLE}>
        <h2 style={TITLE_STYLE} title={data.file_path}>
          {data.title ?? data.file_path}
        </h2>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          {isStale && (
            <button
              type="button"
              data-testid="note-reload-btn"
              onClick={handleReload}
              style={BTN_SECONDARY}
            >
              {t("noteView.reload")}
            </button>
          )}

          {mode === "read" && (
            <button
              type="button"
              data-testid="note-edit-btn"
              onClick={handleEdit}
              style={BTN_PRIMARY}
            >
              {t("noteView.edit")}
            </button>
          )}

          {mode === "edit" && (
            <>
              <button
                type="button"
                data-testid="note-cancel-btn"
                onClick={handleCancel}
                style={BTN_SECONDARY}
                disabled={isSaving}
              >
                {t("noteView.cancel")}
              </button>
              <button
                type="button"
                data-testid="note-save-btn"
                onClick={() => void handleSave()}
                style={BTN_PRIMARY}
                disabled={isSaving}
              >
                {isSaving ? t("noteView.saving") : t("noteView.save")}
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Stale conflict banner ── */}
      {isStale && (
        <div
          role="alert"
          style={{
            padding: "8px 20px",
            background: "#1a0f0f",
            borderBottom: "1px solid #f8514933",
            color: "#f85149",
            fontSize: 13,
          }}
        >
          {t("noteView.staleConflict")}
        </div>
      )}

      {/* ── Content area ── */}
      {mode === "read" ? (
        <MarkdownBody html={renderedHtml} />
      ) : (
        <CodeMirrorEditor
          initialContent={data.content}
          handleRef={editorHandleRef}
        />
      )}
    </div>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const ROOT_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  overflow: "hidden",
  background: "#0d1117",
};

const TOOLBAR_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "10px 16px",
  borderBottom: "1px solid #21262d",
  gap: 12,
  flexShrink: 0,
  minHeight: 48,
};

const TITLE_STYLE: CSSProperties = {
  flex: 1,
  margin: 0,
  fontSize: 15,
  fontWeight: 600,
  color: "#e6edf3",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const BTN_BASE: CSSProperties = {
  padding: "4px 12px",
  fontSize: 13,
  borderRadius: 5,
  border: "1px solid",
  cursor: "pointer",
  fontWeight: 500,
  lineHeight: "20px",
};

const BTN_PRIMARY: CSSProperties = {
  ...BTN_BASE,
  background: "#238636",
  borderColor: "#2ea043",
  color: "#ffffff",
};

const BTN_SECONDARY: CSSProperties = {
  ...BTN_BASE,
  background: "transparent",
  borderColor: "#30363d",
  color: "#8b949e",
};

const LOADING_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 10,
  height: "100%",
  color: "#8b949e",
  fontSize: 14,
};

const SPINNER_STYLE: CSSProperties = {
  display: "inline-block",
  width: 16,
  height: 16,
  borderRadius: "50%",
  border: "2px solid #30363d",
  borderTopColor: "#58a6ff",
  animation: "spin 0.8s linear infinite",
};
