/**
 * NoteView.tsx — wiki page reader + CodeMirror 6 editor for the 'pages' section.
 *
 * INVARIANT I3 compliance:
 *   - Markdown is rendered (via renderMarkdown) ONCE when entering READ mode.
 *     It is NOT re-rendered per keystroke. CodeMirrorEditor owns its internal state;
 *     no keystroke is mirrored into Zustand or causes a React re-render here.
 *   - Zustand subscribed via typed scalar selectors only.
 *   - Related pages fetched ONCE per selection change via its own AbortController.
 *     Never fetched per render, never fetched in edit mode.
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
 *
 * Wikilink click navigation (phase 1):
 *   Rendered HTML from renderMarkdown() may contain <a class="wikilink" data-wikilink="Title">
 *   anchors (no href). A single delegated click handler on the body div intercepts them,
 *   resolves the title to a node id via graphStore.nodes, and calls selectPage().
 *   If no match, shows a "page not found" toast.
 *
 * Type badge / metadata row (phase 1):
 *   The type badge prefers data.type from the GET /content response (more reliable)
 *   and falls back to the graph node type when the content response is absent or has
 *   no type field. The sources list is rendered when data.sources is non-empty.
 *
 * Related panel (phase 2, this sprint):
 *   Below the markdown body in READ mode a "Related (N)" collapsible section shows
 *   up to 10 related pages ranked by 4-signal edge weight. Each row is clickable
 *   via the same selectPage() mechanism used by wikilinks. The panel is hidden when
 *   total === 0.
 *
 * SEAM for future tags phase:
 *   The META_ROW_STYLE div contains a {/* SEAM: <TagChips page={data} /> *} comment.
 *   A Related-panel seam comment sits below the markdown body area. Do NOT build
 *   tags or a second related widget here — use those comments as insertion points.
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  memo,
  type CSSProperties,
  type MouseEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";

import {
  useGraphStore,
  selectSelectedNodeId,
  selectNodes,
  selectSelectPage,
} from "../../store/graphStore";
import type { RelatedPageItem, PageContentResponse } from "../../api/types";
import { fetchPageContent, savePageContent, fetchRelatedPages } from "../../api/pagesClient";
import { ApiError } from "../../api/graphClient";
import { renderMarkdown, stripLeadingFrontmatter } from "../chat/renderMarkdown";
import { EmptyState } from "../common/EmptyState";
import { showToast } from "../common/Toast";
import { CodeMirrorEditor } from "./CodeMirrorEditor";
import type { CodeMirrorEditorHandle } from "./CodeMirrorEditor";
import "../../styles/markdown.css";

// ─── READ-mode markdown renderer (I3: memoised on content string) ─────────────
//
// Prose styles live in ../../styles/markdown.css scoped to .note-view__body.
// The inline style here keeps only the layout/scroll properties that must be
// applied as inline styles (flex, overflowY, padding) — those don't belong in
// a scoped stylesheet since they depend on the parent flex container.

interface MarkdownBodyProps {
  html: string;
  /** Delegated click handler for .wikilink anchors. Click-only; never per-keystroke (I3). */
  onBodyClick?: (e: MouseEvent<HTMLDivElement>) => void;
}

const MarkdownBody = memo(function MarkdownBody({ html, onBodyClick }: MarkdownBodyProps) {
  return (
    <div
      className="note-view__body"
      // renderMarkdown already runs DOMPurify — safe for dangerouslySetInnerHTML.
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={onBodyClick}
      style={{
        flex: "1 1 0",
        overflowY: "auto",
        padding: "16px 20px",
        minHeight: 0,
      }}
    />
  );
});

// ─── Related panel ────────────────────────────────────────────────────────────
//
// Rendered below the markdown body in READ mode only.
// items come from GET /pages/{id}/related (fetched once per selection, I3).

interface RelatedPanelProps {
  items: RelatedPageItem[];
  total: number;
  loading: boolean;
  error: boolean;
  onSelect: (pageId: string) => void;
}

const RelatedPanel = memo(function RelatedPanel({
  items,
  total,
  loading,
  error,
  onSelect,
}: RelatedPanelProps) {
  const { t } = useTranslation();

  // Hide entirely when there are no related pages and we're not loading.
  if (!loading && !error && total === 0) return null;

  return (
    <div data-testid="related-panel" style={RELATED_PANEL_STYLE}>
      <div style={RELATED_HEADER_STYLE}>
        <span style={RELATED_HEADER_LABEL_STYLE}>
          {t("noteView.related", { count: total })}
        </span>
      </div>

      {loading && (
        <span style={RELATED_MUTED_STYLE} data-testid="related-loading">
          {t("common.loading")}
        </span>
      )}

      {!loading && error && (
        <span style={RELATED_MUTED_STYLE} data-testid="related-error">
          {t("noteView.relatedError")}
        </span>
      )}

      {!loading && !error && items.length > 0 && (
        <div style={RELATED_LIST_STYLE} data-testid="related-list">
          {items.map((item) => (
            <button
              key={item.page_id}
              type="button"
              data-testid={`related-item-${item.page_id}`}
              onClick={() => onSelect(item.page_id)}
              style={RELATED_ITEM_STYLE}
            >
              <span style={RELATED_ITEM_TITLE_STYLE}>{item.title}</span>
              {item.type && (
                <span
                  style={{
                    ...TYPE_BADGE_BASE,
                    ...(TYPE_BADGE_COLORS[item.type] ?? TYPE_BADGE_COLORS["__default__"]),
                    fontSize: 10,
                    padding: "0px 6px",
                  }}
                >
                  {item.type}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

// ─── Component ────────────────────────────────────────────────────────────────

type NoteViewMode = "read" | "edit";

interface NoteViewState {
  phase: "idle" | "loading" | "ready" | "error";
  data: PageContentResponse | null;
  errorMessage: string | null;
}

interface RelatedState {
  phase: "idle" | "loading" | "ready" | "error";
  items: RelatedPageItem[];
  total: number;
}

export function NoteView() {
  const { t } = useTranslation();
  const selectedNodeId = useGraphStore(selectSelectedNodeId);
  // Shallow-compared nodes array for wikilink resolution + type badge fallback (I3).
  const nodes = useGraphStore(useShallow(selectNodes));
  const selectPage = useGraphStore(selectSelectPage);

  const [state, setState] = useState<NoteViewState>({
    phase: "idle",
    data: null,
    errorMessage: null,
  });
  const [mode, setMode] = useState<NoteViewMode>("read");
  const [isSaving, setIsSaving] = useState(false);

  // Related pages state — fetched once per selection change in READ mode.
  const [relatedState, setRelatedState] = useState<RelatedState>({
    phase: "idle",
    items: [],
    total: 0,
  });

  // Handle to the CodeMirror editor — valid only while mode === "edit"
  const editorHandleRef = useRef<CodeMirrorEditorHandle | null>(null);

  // Memoised rendered HTML — computed ONCE when entering read mode (I3).
  // useMemo key: state.data reference changes only when we receive fresh content.
  const renderedHtml = useMemo(() => {
    if (state.phase !== "ready" || !state.data) return "";
    // Strip the leading YAML frontmatter block(s) so raw `type:`/`sources:` YAML never
    // renders as body text; type/sources are surfaced via the metadata row instead.
    return renderMarkdown(stripLeadingFrontmatter(state.data.content));
  }, [state.phase, state.data]);

  // ── Effective page type (Task B) ──────────────────────────────────────────
  // Prefer the content response type (authoritative, comes from the DB row).
  // Fall back to the graph node type only when the content response has no type field.
  // This is more robust: the graph may lag behind the content endpoint by one
  // dataVersion tick.
  const effectiveType: string | null = useMemo(() => {
    if (state.data?.type != null) return state.data.type;
    if (!selectedNodeId) return null;
    return nodes.find((n) => n.id === selectedNodeId)?.type ?? null;
  }, [state.data, nodes, selectedNodeId]);

  // ── Wikilink click handler ─────────────────────────────────────────────────
  // Event delegation on the body container — fires only on real user clicks,
  // never per-keystroke (I3). Resolves [[Title]] → node id via nodes array.
  const handleWikilinkClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      const target = e.target as Element;
      const anchor = target.closest("a.wikilink");
      if (!anchor) return;
      e.preventDefault();

      const wikilinkTitle = anchor.getAttribute("data-wikilink");
      if (!wikilinkTitle) return;

      const titleLower = wikilinkTitle.toLowerCase();
      const match = nodes.find((n) => n.title.toLowerCase() === titleLower);
      if (match) {
        selectPage(match.id, "tree");
      } else {
        showToast(t("noteView.wikilinkNotFound", { title: wikilinkTitle }), "error");
      }
    },
    [nodes, selectPage, t],
  );

  // ── Fetch page content on selection change ─────────────────────────────────

  const loadPage = useCallback(
    (pageId: string, abortSignal: AbortSignal) => {
      setState({ phase: "loading", data: null, errorMessage: null });
      setMode("read");
      // Reset related state whenever we navigate to a new page.
      setRelatedState({ phase: "idle", items: [], total: 0 });

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
      setRelatedState({ phase: "idle", items: [], total: 0 });
      return;
    }
    const controller = new AbortController();
    loadPage(selectedNodeId, controller.signal);
    return () => {
      controller.abort();
    };
  }, [selectedNodeId, loadPage]);

  // ── Fetch related pages — once per selection, READ mode only (I3) ──────────
  // Separate AbortController from the content fetch so cancellation is independent.
  // Only runs when the page content is in the "ready" phase to avoid racing.

  useEffect(() => {
    // Do not fetch while editing or before content is loaded.
    if (!selectedNodeId || state.phase !== "ready") return;

    const controller = new AbortController();
    setRelatedState({ phase: "loading", items: [], total: 0 });

    fetchRelatedPages(selectedNodeId, 10, controller.signal)
      .then((resp) => {
        setRelatedState({ phase: "ready", items: resp.items, total: resp.total });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof Error && err.name === "AbortError") return;
        // Quiet error — never block the page reader for a related-panel failure.
        setRelatedState({ phase: "error", items: [], total: 0 });
      });

    return () => {
      controller.abort();
    };
    // Intentional dep list: only re-run when the selected page or content readiness changes.
    // fetchRelatedPages is a stable module import, not a reactive dep.
  }, [selectedNodeId, state.phase]);

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

  // ── Related page select ────────────────────────────────────────────────────

  const handleRelatedSelect = useCallback(
    (pageId: string) => {
      selectPage(pageId, "tree");
    },
    [selectPage],
  );

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

  // Non-empty sources list from the content response (Task B).
  const sources =
    data.sources && data.sources.length > 0 ? data.sources : null;

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

      {/* ── Page metadata row ── */}
      {/* Type badge: prefers data.type from the content response (Task B).
          Falls back to the graph node type (already set in effectiveType).
          Sources row: shown when data.sources is non-empty (Task B).
          SEAM: <TagChips page={data} /> goes here (future tags phase). */}
      {(effectiveType || sources) && (
        <div data-testid="note-meta-row" style={META_ROW_STYLE}>
          {effectiveType && (
            <span
              data-testid="note-type-badge"
              style={{
                ...TYPE_BADGE_BASE,
                ...(TYPE_BADGE_COLORS[effectiveType] ?? TYPE_BADGE_COLORS["__default__"]),
              }}
            >
              {effectiveType}
            </span>
          )}
          {sources && (
            <span data-testid="note-sources" style={SOURCES_STYLE}>
              {t("noteView.sources")}: {sources.join(", ")}
            </span>
          )}
          {/* SEAM: <TagChips page={data} /> */}
        </div>
      )}

      {/* ── Content area + related panel ── */}
      {mode === "read" ? (
        /* Scroll wrapper: the markdown body and related panel scroll together. */
        <div style={SCROLL_AREA_STYLE}>
          <MarkdownBody html={renderedHtml} onBodyClick={handleWikilinkClick} />

          {/* ── Related pages panel (Task C) ── */}
          {/* SEAM: a second "in-page links" widget could sit here in a future phase. */}
          <RelatedPanel
            items={relatedState.items}
            total={relatedState.total}
            loading={relatedState.phase === "loading"}
            error={relatedState.phase === "error"}
            onSelect={handleRelatedSelect}
          />
        </div>
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

// The scroll area wraps the markdown body + related panel so they scroll together.
const SCROLL_AREA_STYLE: CSSProperties = {
  flex: "1 1 0",
  overflowY: "auto",
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
};

// ─── Metadata row styles ──────────────────────────────────────────────────────

const META_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 20px 6px",
  borderBottom: "1px solid #21262d",
  flexShrink: 0,
  minHeight: 30,
  flexWrap: "wrap",
  // SEAM: when tags are added, they go here alongside the type badge.
};

const TYPE_BADGE_BASE: CSSProperties = {
  display: "inline-block",
  padding: "1px 8px",
  borderRadius: 10,
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: "0.03em",
  textTransform: "uppercase",
  border: "1px solid",
  flexShrink: 0,
};

/** Badge color map by page type. Falls back to __default__ for unknown types. */
const TYPE_BADGE_COLORS: Record<string, CSSProperties> = {
  entity:      { background: "rgba(56,139,253,0.15)",  borderColor: "#388bfd55", color: "#79c0ff" },
  concept:     { background: "rgba(63,185,80,0.12)",   borderColor: "#3fb95044", color: "#56d364" },
  source:      { background: "rgba(242,204,96,0.13)",  borderColor: "#f2cc6044", color: "#e3b341" },
  synthesis:   { background: "rgba(210,153,255,0.13)", borderColor: "#d2a8ff44", color: "#d2a8ff" },
  comparison:  { background: "rgba(248,81,73,0.12)",   borderColor: "#f8514933", color: "#ff7b72" },
  __default__: { background: "rgba(139,148,158,0.12)", borderColor: "#8b949e33", color: "#8b949e" },
};

const SOURCES_STYLE: CSSProperties = {
  fontSize: 11,
  color: "#8b949e",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  maxWidth: "60%",
};

// ─── Related panel styles ─────────────────────────────────────────────────────

const RELATED_PANEL_STYLE: CSSProperties = {
  borderTop: "1px solid #21262d",
  padding: "10px 20px 14px",
  flexShrink: 0,
};

const RELATED_HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  marginBottom: 8,
};

const RELATED_HEADER_LABEL_STYLE: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "#8b949e",
};

const RELATED_MUTED_STYLE: CSSProperties = {
  fontSize: 12,
  color: "#8b949e",
};

const RELATED_LIST_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const RELATED_ITEM_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 8px",
  borderRadius: 5,
  background: "transparent",
  border: "none",
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  color: "#e6edf3",
  fontSize: 13,
  transition: "background 0.1s",
};

const RELATED_ITEM_TITLE_STYLE: CSSProperties = {
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "#58a6ff",
};
