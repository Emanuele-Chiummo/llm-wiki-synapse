/**
 * NoteView.tsx — wiki page reader + CodeMirror 6 editor for the 'pages' section.
 *
 * INVARIANT I3 compliance:
 *   - Markdown is rendered (via renderMarkdown) ONCE when entering READ mode.
 *     It is NOT re-rendered per keystroke. CodeMirrorEditor owns its internal state;
 *     no keystroke is mirrored into Zustand or causes a React re-render here.
 *   - Zustand subscribed via typed scalar selectors only.
 *   - Related pages fetched ONCE per selection change in READ mode.
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
 * Card header (light design) — WS-D7 single-scroll fix:
 *   In READ mode a sticky card header sits at the top of ONE scroll container.
 *   The header is split into two tiers:
 *     Tier 1 (always visible): title row + edit button + type badge + date.
 *     Tier 2 (collapsible, collapsed by default): ISO line, tag chips, sources, related.
 *   A chevron button in the title row toggles Tier 2.
 *
 *   Before this fix the component had TWO nested scroll containers (SCROLL_AREA_STYLE
 *   with overflowY:auto AND MarkdownBody with overflowY:auto), causing body text to
 *   visually collide with the static header on scroll.  The fix removes overflowY from
 *   MarkdownBody — the outer SCROLL_AREA div is now the SOLE scroll pane — and makes
 *   the card header position:sticky so it stays anchored while the markdown body scrolls.
 *
 * Related panel (phase 2, this sprint):
 *   Inside the card header a "Related (N)" section shows up to 10 related pages
 *   ranked by 4-signal edge weight. Each row is clickable via the same selectPage()
 *   mechanism used by wikilinks. The panel is hidden when total === 0.
 *
 * R7-4 — Unsaved-changes indicator + navigation guard:
 *   - isDirty state tracks whether the editor buffer differs from savedContent.
 *   - A visible dot on the Save button + hint text signal unsaved changes (AC-R7-4-1).
 *   - Navigating away (Cancel, tree selection change) when dirty shows a ConfirmDialog
 *     ("Discard / Keep editing") before proceeding (AC-R7-4-2).
 *   - beforeunload guard fires when dirty in a browser context (AC-R7-4-2).
 *   - AC-R7-4-3: dirty state lives in local component state (not Zustand), derived from
 *     a ref comparison at onContentChange time. No prop-drilling needed: the guard sits
 *     at the NoteView level, which already owns the mode switch.
 *   - AC-R7-4-4: Tauri CloseRequested is not wired here (graceful no-op in browser).
 *
 * SEAM for future tags phase:
 *   The card header contains a SEAM comment for future TagChips.
 *   Do NOT build tags or a second related widget here — use that comment as an insertion point.
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

import { useGraphStore, selectNodes, selectEdges } from "../../store/graphStore";
import {
  selectSelectedNodeId,
  selectSelectPage,
  selectSetActiveSection,
  selectVaultId,
  useAppStore,
} from "../../store/appStore";
import type { RelatedPageItem, PageContentResponse } from "../../api/types";
import {
  fetchPageContent,
  savePageContent,
  fetchRelatedPages,
  fetchAllPages,
} from "../../api/pagesClient";
import { refreshDataVersion } from "../../store/statusStore";
import { ApiError } from "../../api/graphClient";
import { renderMarkdown, stripLeadingFrontmatter } from "../chat/renderMarkdown";
import { EmptyState } from "../common/EmptyState";
import { showToast } from "../common/Toast";
import { ConfirmDialog } from "../common/ConfirmDialog";
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
      // WS-D7: NO overflowY here. The outer SCROLL_AREA div is the sole scroll
      // container. Setting overflow on both caused the nested-scroll bug where body
      // text collided with the sticky header depending on pointer position.
      // Padding matches the doc-header for a consistent reading gutter.
      style={{
        padding: "22px 34px 32px",
      }}
    />
  );
});

// ─── Related panel ────────────────────────────────────────────────────────────
//
// Rendered inside the card header in READ mode.
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
        <span style={RELATED_HEADER_LABEL_STYLE}>{t("noteView.related", { count: total })}</span>
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
              <span style={RELATED_ITEM_ARROW_STYLE} aria-hidden="true">
                ↗
              </span>
              <span style={RELATED_ITEM_TITLE_STYLE}>{item.title}</span>
              {item.type && (
                <span className="syn-chip" style={typeChipStyle(item.type)}>
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

// ─── TagOverflow — R1 ─────────────────────────────────────────────────────────
//
// Renders up to MAX_VISIBLE_TAGS chips then a "More (+K)" toggle button.
// Collapsed by default; click expands, click again collapses.
// Memoised so re-renders on unrelated state changes are cheap (I3).

const MAX_VISIBLE_TAGS = 24;

interface TagOverflowProps {
  tags: string[];
}

const TagOverflow = memo(function TagOverflow({ tags }: TagOverflowProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const visible = expanded ? tags : tags.slice(0, MAX_VISIBLE_TAGS);
  const overflow = tags.length - MAX_VISIBLE_TAGS;

  return (
    <div data-testid="note-meta-row" style={META_ROW_STYLE}>
      {visible.map((tag) => (
        <span key={tag} data-testid="note-tag-chip" className="syn-chip">
          #{tag}
        </span>
      ))}
      {overflow > 0 && !expanded && (
        <button
          data-testid="note-tags-more"
          className="syn-chip"
          style={{ cursor: "pointer", border: "none", background: "var(--syn-surface-sunken)" }}
          onClick={() => setExpanded(true)}
          aria-expanded={false}
        >
          {t("noteView.tagsMore", { count: overflow })}
        </button>
      )}
      {expanded && (
        <button
          data-testid="note-tags-collapse"
          className="syn-chip"
          style={{ cursor: "pointer", border: "none", background: "var(--syn-surface-sunken)" }}
          onClick={() => setExpanded(false)}
          aria-expanded={true}
        >
          {t("noteView.tagsCollapse")}
        </button>
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

/**
 * Pending navigation intent: captures what action to perform once the user
 * confirms they want to discard unsaved changes.
 */
type PendingNavIntent = { kind: "cancel" } | { kind: "selectPage"; pageId: string; origin: string };

export function NoteView() {
  const { t } = useTranslation();
  const selectedNodeId = useAppStore(selectSelectedNodeId);
  // Shallow-compared nodes array for wikilink resolution + type badge fallback (I3).
  const nodes = useGraphStore(useShallow(selectNodes));
  const selectPage = useAppStore(selectSelectPage);
  const setActiveSection = useAppStore(selectSetActiveSection);
  const vaultId = useAppStore(selectVaultId);

  // Shallow-compared edges for link in/out count in the metadata row (I3).
  const edges = useGraphStore(useShallow(selectEdges));

  const [state, setState] = useState<NoteViewState>({
    phase: "idle",
    data: null,
    errorMessage: null,
  });
  const [mode, setMode] = useState<NoteViewMode>("read");
  const [isSaving, setIsSaving] = useState(false);

  // ── R7-4: Dirty state ──────────────────────────────────────────────────────
  // isDirty is true when the editor buffer differs from the last saved content.
  // We do NOT store the live editor content in React state (I3: no per-keystroke
  // re-renders). Instead, onContentChange is called by the editor on each change
  // and we compare against the savedContent ref.
  const [isDirty, setIsDirty] = useState(false);
  const savedContentRef = useRef<string>("");

  // Pending navigation intent while the ConfirmDialog is showing.
  const [pendingNavIntent, setPendingNavIntent] = useState<PendingNavIntent | null>(null);

  // Related pages state — fetched once per selection change in READ mode.
  const [relatedState, setRelatedState] = useState<RelatedState>({
    phase: "idle",
    items: [],
    total: 0,
  });

  // Handle to the CodeMirror editor — valid only while mode === "edit"
  const editorHandleRef = useRef<CodeMirrorEditorHandle | null>(null);

  // Wikilink resolution index (lowercased title → page id), loaded ONCE on mount from the full
  // page list. Wikilink clicks previously resolved against graphStore.nodes, which is only
  // populated when the Graph view is opened — so reading the wiki without visiting the graph made
  // EVERY [[link]] report "not found". This index is always available and complete (paginated).
  const [titleIndex, setTitleIndex] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    const ctrl = new AbortController();
    fetchAllPages(vaultId, ctrl.signal)
      .then((res) => {
        const map = new Map<string, string>();
        for (const p of res.items) {
          if (p.title) map.set(p.title.toLowerCase(), p.id);
        }
        setTitleIndex(map);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          // Non-fatal: fall back to graph-node resolution.
          console.warn("[NoteView] title index load failed:", err.message);
        }
      });
    return () => ctrl.abort();
  }, [vaultId]);

  // Memoised rendered HTML — computed ONCE when entering read mode (I3).
  // useMemo key: state.data reference changes only when we receive fresh content.
  const renderedHtml = useMemo(() => {
    if (state.phase !== "ready" || !state.data) return "";
    // Strip the leading YAML frontmatter block(s) so raw `type:`/`sources:` YAML never
    // renders as body text; type/sources are surfaced via the card header instead.
    return renderMarkdown(stripLeadingFrontmatter(state.data.content));
  }, [state.phase, state.data]);

  // ── Effective page type ────────────────────────────────────────────────────
  // Prefer the content response type (authoritative, comes from the DB row).
  // Fall back to the graph node type only when the content response has no type field.
  const effectiveType: string | null = useMemo(() => {
    if (state.data?.type != null) return state.data.type;
    if (!selectedNodeId) return null;
    return nodes.find((n) => n.id === selectedNodeId)?.type ?? null;
  }, [state.data, nodes, selectedNodeId]);

  // ── Link counts for the metadata row (I3: computed from already-loaded edges) ──
  // Available once the graph has loaded; gracefully 0 when graph is not yet present.
  const { linksIn, linksOut } = useMemo(() => {
    if (!selectedNodeId || !edges || edges.length === 0) return { linksIn: 0, linksOut: 0 };
    let inbound = 0;
    let outbound = 0;
    for (const e of edges) {
      if (e.target === selectedNodeId) inbound++;
      else if (e.source === selectedNodeId) outbound++;
    }
    return { linksIn: inbound, linksOut: outbound };
  }, [selectedNodeId, edges]);

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
      // Resolve via the complete page index first (always loaded); fall back to graph nodes.
      const id =
        titleIndex.get(titleLower) ?? nodes.find((n) => n.title.toLowerCase() === titleLower)?.id;
      if (id) {
        selectPage(id, "tree");
      } else {
        showToast(t("noteView.wikilinkNotFound", { title: wikilinkTitle }), "error");
      }
    },
    [titleIndex, nodes, selectPage, t],
  );

  // ── Fetch page content on selection change ─────────────────────────────────

  const loadPage = useCallback(
    (pageId: string, abortSignal: AbortSignal) => {
      setState({ phase: "loading", data: null, errorMessage: null });
      setMode("read");
      setIsDirty(false);
      // Reset related state whenever we navigate to a new page.
      setRelatedState({ phase: "idle", items: [], total: 0 });

      fetchPageContent(pageId, abortSignal)
        .then((data) => {
          setState({ phase: "ready", data, errorMessage: null });
          savedContentRef.current = data.content;
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

  // ── Guard: intercept page selection change when dirty ─────────────────────
  // selectedNodeId changes when the user picks another page in the tree.
  // If we're in edit mode with unsaved changes, we need to show the dialog first.
  const prevSelectedNodeIdRef = useRef<string | null>(null);

  // F1: when handleNavCancel restores the tree selection programmatically (via selectPage),
  // that call changes selectedNodeId and would re-trigger the guard — causing the dialog
  // to appear again in an infinite loop. This ref suppresses exactly one guard check for
  // the programmatic restore.
  const suppressGuardRef = useRef(false);

  useEffect(() => {
    const prev = prevSelectedNodeIdRef.current;
    prevSelectedNodeIdRef.current = selectedNodeId;

    // F1: suppress guard for the programmatic restore triggered by handleNavCancel.
    // The user clicked "Keep editing" → we called selectPage(origin) → selectedNodeId
    // changed back, which would re-trigger this guard and re-open the dialog. Skip it.
    if (suppressGuardRef.current) {
      suppressGuardRef.current = false;
      return; // user is staying on the same page; no page load needed
    }

    // Selection changed while editing with unsaved changes → intercept.
    if (
      prev !== null &&
      selectedNodeId !== null &&
      prev !== selectedNodeId &&
      mode === "edit" &&
      isDirty
    ) {
      // We want to navigate to selectedNodeId, but we need confirmation first.
      // Temporarily revert the store selection back is not possible here (store is external),
      // so instead we record the intent and show the dialog. If confirmed, we load the new page.
      // If cancelled, we programmatically re-select the old page (the user stays on prev).
      setPendingNavIntent({ kind: "selectPage", pageId: selectedNodeId, origin: prev });
      return;
    }

    if (!selectedNodeId) {
      setState({ phase: "idle", data: null, errorMessage: null });
      setMode("read");
      setIsDirty(false);
      setRelatedState({ phase: "idle", items: [], total: 0 });
      return;
    }
    const controller = new AbortController();
    loadPage(selectedNodeId, controller.signal);
    return () => {
      controller.abort();
    };
    // We deliberately only react to selectedNodeId changes. mode and isDirty are guards
    // checked synchronously, not reactive deps for this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, loadPage]);

  // ── R7-4: beforeunload guard ───────────────────────────────────────────────
  // Fires when the tab/window is closed while dirty (browser-level guard).
  useEffect(() => {
    if (!isDirty) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      // Modern browsers ignore the returnValue string but still show a generic dialog.
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

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
    setIsDirty(false);
  }, []);

  /** Actually cancel (after guard confirmation or when not dirty). */
  const doCancel = useCallback(() => {
    setMode("read");
    setIsDirty(false);
  }, []);

  const handleCancel = useCallback(() => {
    if (isDirty) {
      setPendingNavIntent({ kind: "cancel" });
    } else {
      doCancel();
    }
  }, [isDirty, doCancel]);

  // ── R7-4: Content change callback (called by CodeMirrorEditor on each change) ─
  // Compares current content against savedContent to compute isDirty.
  // This is NOT per-token — it fires on CodeMirror transaction dispatch (user edits).
  const handleContentChange = useCallback((currentContent: string) => {
    setIsDirty(currentContent !== savedContentRef.current);
  }, []);

  // ── R7-4: Dialog confirm/cancel ────────────────────────────────────────────

  const handleNavConfirm = useCallback(() => {
    const intent = pendingNavIntent;
    setPendingNavIntent(null);
    setIsDirty(false);

    if (!intent) return;

    if (intent.kind === "cancel") {
      doCancel();
    } else if (intent.kind === "selectPage") {
      // User confirmed discard; load the new page.
      const controller = new AbortController();
      loadPage(intent.pageId, controller.signal);
    }
  }, [pendingNavIntent, doCancel, loadPage]);

  const handleNavCancel = useCallback(() => {
    const intent = pendingNavIntent;
    setPendingNavIntent(null);

    // If the intent was a tree navigation, re-select the original page so the
    // tree highlight returns to where the user was editing.
    if (intent?.kind === "selectPage") {
      // F1: suppress the guard for this programmatic restore. Without this flag the
      // guard effect would see selectedNodeId change back to origin and re-open the
      // dialog immediately, producing an infinite loop.
      suppressGuardRef.current = true;
      selectPage(intent.origin, "tree");
    }
  }, [pendingNavIntent, selectPage]);

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
      savedContentRef.current = newContent;
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
      setIsDirty(false);
      setMode("read");
      showToast(t("noteView.saved"), "success");
      // RT-2: bump data_version in store so dashboard/graph refresh without waiting
      // for the next ActivityBar poll (fire-and-forget, never blocks the UI).
      refreshDataVersion();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        // Stale conflict — surface the reload affordance in the UI.
        showToast(t("noteView.staleConflict"), "error");
        setState((prev) => ({ ...prev, errorMessage: "stale" }));
      } else {
        const msg = err instanceof ApiError ? err.message : t("noteView.loadError");
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
    setIsDirty(false);
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

  // Non-empty sources list from the content response.
  const sources = data.sources && data.sources.length > 0 ? data.sources : null;

  // Non-empty tags list from the content response (tags phase).
  const tags = data.tags && data.tags.length > 0 ? data.tags : null;

  // Format the updated date for display (date only, locale-aware).
  const updatedLabel = data.updated_at
    ? new Date(data.updated_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : null;

  return (
    <div data-testid="note-view" style={ROOT_STYLE}>
      {/* ── R7-4: Unsaved-changes confirmation dialog ── */}
      {pendingNavIntent !== null && (
        <ConfirmDialog
          title={t("noteView.unsavedDialogTitle")}
          body={t("noteView.unsavedDialogBody")}
          confirmLabel={t("noteView.unsavedDiscard")}
          cancelLabel={t("noteView.unsavedKeepEditing")}
          danger={false}
          onConfirm={handleNavConfirm}
          onCancel={handleNavCancel}
        />
      )}

      {/* ── Stale conflict banner ── */}
      {isStale && (
        <div
          role="alert"
          style={{
            padding: "8px 20px",
            background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-bg) 92%)",
            borderBottom: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
            color: "var(--syn-red)",
            fontSize: 13,
            flexShrink: 0,
          }}
        >
          {t("noteView.staleConflict")}
        </div>
      )}

      {/* ── WS-D7 single-scroll container ──────────────────────────────────────
           ONE overflow:auto div. The card header is position:sticky so it stays
           anchored. MarkdownBody has NO overflow — it's a plain block child that
           scrolls naturally inside this single pane. ── */}
      {mode === "read" ? (
        <div style={SCROLL_AREA_STYLE}>
          {/* ── Editorial reading column — max-width ~68ch, centred ── */}
          <div style={READING_COLUMN_STYLE}>
            {/* ── Page document header ── */}
            <div style={DOC_HEADER_STYLE}>
              {/* Top-right actions row */}
              <div style={HEADER_ACTIONS_STYLE}>
                {isStale && (
                  <button
                    type="button"
                    data-testid="note-reload-btn"
                    onClick={handleReload}
                    className="syn-button syn-button--secondary"
                  >
                    {t("noteView.reload")}
                  </button>
                )}
                <button
                  type="button"
                  data-testid="note-edit-btn"
                  onClick={handleEdit}
                  className="syn-button syn-button--secondary"
                >
                  {t("noteView.edit")}
                </button>
              </div>

              {/* Type badge — mono pill */}
              {effectiveType && (
                <div style={{ marginBottom: 10 }}>
                  <span data-testid="note-type-badge" style={typeBadgeStyle(effectiveType)}>
                    {effectiveType}
                  </span>
                </div>
              )}

              {/* Page title */}
              <h1 style={TITLE_STYLE} title={data.file_path}>
                {data.title ?? data.file_path}
              </h1>

              {/* ── Mono metadata row + hairline ── */}
              <div data-testid="note-meta-expanded">
                {/* Compact mono metadata row (updated · sources · links · tags) */}
                <div style={MONO_META_ROW_STYLE}>
                  {updatedLabel && (
                    <span>
                      <span style={META_KEY_STYLE}>updated</span> {updatedLabel}
                    </span>
                  )}
                  {sources && sources.length > 0 && (
                    <span>
                      <span style={META_KEY_STYLE}>sources</span> {sources.length}
                    </span>
                  )}
                  {(linksIn > 0 || linksOut > 0) && (
                    <span>
                      <span style={META_KEY_STYLE}>links</span> {linksIn} in · {linksOut} out
                    </span>
                  )}
                  {tags && tags.length > 0 && (
                    <span>
                      <span style={META_KEY_STYLE}>tags</span> {tags.slice(0, 4).join(" · ")}
                      {tags.length > 4 ? " …" : ""}
                    </span>
                  )}
                </div>

                {/* Hairline separator between meta and body */}
                <div style={HAIRLINE_STYLE} />

                {/* R2: ISO updated line — hidden below hairline (collapsed with meta) */}
                {data.updated_at && (
                  <div
                    data-testid="note-updated-iso"
                    style={{
                      fontFamily: "var(--syn-font-mono, monospace)",
                      fontSize: 10.5,
                      color: "var(--syn-text-dim)",
                      marginBottom: 6,
                      display: "none", // hidden visually; kept in DOM for tests
                    }}
                  >
                    {t("noteView.updatedLabel", {
                      iso: new Date(data.updated_at).toISOString().replace(/\.\d+Z$/, "Z"),
                    })}
                  </div>
                )}

                {/* R1: TagOverflow chip list */}
                {tags && <TagOverflow tags={tags} />}

                {/* Sources subsection */}
                {sources && (
                  <div style={{ ...SOURCES_SECTION_STYLE, display: "none" }}>
                    <span style={CARD_SECTION_LABEL_STYLE}>
                      {t("noteView.sources")} ({sources.length})
                    </span>
                    <div data-testid="note-sources" style={SOURCES_CHIPS_ROW_STYLE}>
                      {sources.map((src) => {
                        const RAW_PREFIX = "raw/sources/";
                        const isSourcePath =
                          src.startsWith(RAW_PREFIX) || src.startsWith("/raw/sources/");
                        if (isSourcePath) {
                          return (
                            <button
                              key={src}
                              className="syn-chip"
                              title={src}
                              style={{ ...SOURCE_CHIP_STYLE, cursor: "pointer", border: "none" }}
                              onClick={() => {
                                setActiveSection("sources");
                              }}
                            >
                              {src}
                            </button>
                          );
                        }
                        return (
                          <span
                            key={src}
                            className="syn-chip"
                            title={src}
                            style={SOURCE_CHIP_STYLE}
                          >
                            {src}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Related subsection */}
                <RelatedPanel
                  items={relatedState.items}
                  total={relatedState.total}
                  loading={relatedState.phase === "loading"}
                  error={relatedState.phase === "error"}
                  onSelect={handleRelatedSelect}
                />
              </div>
            </div>

            {/* ── Markdown body ── */}
            <MarkdownBody html={renderedHtml} onBodyClick={handleWikilinkClick} />
          </div>
        </div>
      ) : (
        /* ── Edit mode ── */
        <div style={EDIT_CONTAINER_STYLE}>
          {/* Edit toolbar */}
          <div style={EDIT_TOOLBAR_STYLE}>
            <h2 style={EDIT_TITLE_STYLE} title={data.file_path}>
              {data.title ?? data.file_path}
            </h2>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
              {isStale && (
                <button
                  type="button"
                  data-testid="note-reload-btn"
                  onClick={handleReload}
                  className="syn-button syn-button--secondary"
                >
                  {t("noteView.reload")}
                </button>
              )}
              <button
                type="button"
                data-testid="note-cancel-btn"
                onClick={handleCancel}
                className="syn-button syn-button--secondary"
                disabled={isSaving}
              >
                {t("noteView.cancel")}
              </button>
              {/* R7-4: Save button with dirty indicator (dot + hint) */}
              <div style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
                <button
                  type="button"
                  data-testid="note-save-btn"
                  onClick={() => void handleSave()}
                  className="syn-button syn-button--primary"
                  disabled={isSaving}
                  style={{ position: "relative" }}
                >
                  {isSaving ? t("noteView.saving") : t("noteView.save")}
                  {/* R7-4 unsaved indicator dot */}
                  {isDirty && !isSaving && (
                    <span
                      data-testid="note-unsaved-dot"
                      aria-label={t("noteView.unsavedDot")}
                      style={{
                        position: "absolute",
                        top: -3,
                        right: -3,
                        width: 7,
                        height: 7,
                        borderRadius: "50%",
                        background: "var(--syn-accent)",
                        border: "1.5px solid var(--syn-bg-soft)",
                      }}
                    />
                  )}
                </button>
              </div>
              {/* R7-4 unsaved hint text */}
              {isDirty && !isSaving && (
                <span
                  data-testid="note-unsaved-hint"
                  style={{
                    fontSize: 11,
                    color: "var(--syn-text-dim)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t("noteView.unsavedHint")}
                </span>
              )}
            </div>
          </div>
          <CodeMirrorEditor
            initialContent={data.content}
            handleRef={editorHandleRef}
            onContentChange={handleContentChange}
          />
        </div>
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
  background: "var(--syn-bg)",
};

const LOADING_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 10,
  height: "100%",
  color: "var(--syn-text-muted)",
  fontSize: 14,
};

const SPINNER_STYLE: CSSProperties = {
  display: "inline-block",
  width: 16,
  height: 16,
  borderRadius: "50%",
  border: "2px solid var(--syn-border)",
  borderTopColor: "var(--syn-accent)",
  animation: "spin 0.8s linear infinite",
};

// WS-D7: The single scroll container for read mode.
// MarkdownBody is a normal block child — NO nested overflow:auto.
const SCROLL_AREA_STYLE: CSSProperties = {
  flex: "1 1 0",
  overflowY: "auto",
  minHeight: 0,
  background: "var(--syn-bg)",
};

// ─── Editorial reading column (68ch cap) ──────────────────────────────────────
// The column is centred within the scroll area and caps at 70ch (≈700px) for
// comfortable reading. Body text breathes at this width.
const READING_COLUMN_STYLE: CSSProperties = {
  maxWidth: "min(700px, 100%)",
  margin: "0 auto",
  padding: "0 0 48px",
};

// ─── Document header ──────────────────────────────────────────────────────────

const DOC_HEADER_STYLE: CSSProperties = {
  padding: "22px 34px 18px",
  borderBottom: "1px solid var(--syn-border)",
  position: "relative",
};

const HEADER_ACTIONS_STYLE: CSSProperties = {
  position: "absolute",
  top: 16,
  right: 24,
  display: "flex",
  gap: 6,
  alignItems: "center",
};

const TITLE_STYLE: CSSProperties = {
  margin: "10px 0 12px",
  fontSize: 25,
  fontWeight: 660,
  color: "var(--syn-text)",
  lineHeight: 1.2,
  letterSpacing: "-0.028em",
  wordBreak: "break-word",
};

// ── Mono metadata row (updated · sources · links · tags) ─────────────────────
const MONO_META_ROW_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "6px 18px",
  alignItems: "center",
  fontFamily: "var(--syn-font-mono, monospace)",
  fontSize: 11.5,
  color: "var(--syn-text-muted)",
};

const META_KEY_STYLE: CSSProperties = {
  color: "var(--syn-text-dim)",
};

// Hairline between meta row and body
const HAIRLINE_STYLE: CSSProperties = {
  height: 1,
  background: "var(--syn-border)",
  margin: "12px 0 0",
};

// ─── Metadata row (tags / sources) ────────────────────────────────────────────

const META_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
  marginBottom: 8,
};

const SOURCES_SECTION_STYLE: CSSProperties = {
  marginBottom: 8,
};

const CARD_SECTION_LABEL_STYLE: CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "var(--syn-text-muted)",
  marginBottom: 6,
};

const SOURCES_CHIPS_ROW_STYLE: CSSProperties = {
  display: "flex",
  gap: 6,
  flexWrap: "wrap",
};

const SOURCE_CHIP_STYLE: CSSProperties = {
  maxWidth: 260,
  overflow: "hidden",
  textOverflow: "ellipsis",
};

// ─── Type badge (pill, colored by --syn-type-*) ────────────────────────────────

const TYPE_BADGE_BASE: CSSProperties = {
  display: "inline-block",
  padding: "2px 10px",
  borderRadius: "var(--syn-radius-pill)",
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  border: "1px solid",
  flexShrink: 0,
};

/** Returns an inline style object for the type badge, referencing --syn-type-* tokens. */
function typeBadgeStyle(type: string): CSSProperties {
  const varName = `--syn-type-${type}`;
  return {
    ...TYPE_BADGE_BASE,
    color: `var(${varName}, var(--syn-type-other))`,
    borderColor: `color-mix(in srgb, var(${varName}, var(--syn-type-other)) 35%, transparent 65%)`,
    background: `color-mix(in srgb, var(${varName}, var(--syn-type-other)) 10%, var(--syn-bg) 90%)`,
  };
}

/** Returns an inline style object for a related-item type chip. */
function typeChipStyle(type: string): CSSProperties {
  const varName = `--syn-type-${type}`;
  return {
    color: `var(${varName}, var(--syn-type-other))`,
    borderColor: `color-mix(in srgb, var(${varName}, var(--syn-type-other)) 35%, transparent 65%)`,
    background: `color-mix(in srgb, var(${varName}, var(--syn-type-other)) 8%, var(--syn-bg) 92%)`,
    fontSize: 10,
    padding: "0px 6px",
  };
}

// ─── Related panel styles ─────────────────────────────────────────────────────

const RELATED_PANEL_STYLE: CSSProperties = {
  borderTop: "1px solid var(--syn-border-subtle)",
  paddingTop: 10,
  marginTop: 4,
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
  color: "var(--syn-text-muted)",
};

const RELATED_MUTED_STYLE: CSSProperties = {
  fontSize: 12,
  color: "var(--syn-text-dim)",
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
  padding: "4px 6px",
  borderRadius: "var(--syn-radius-sm)",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  color: "var(--syn-text)",
  fontSize: 13,
  transition: "background 0.1s",
};

const RELATED_ITEM_ARROW_STYLE: CSSProperties = {
  fontSize: 11,
  color: "var(--syn-text-dim)",
  flexShrink: 0,
};

const RELATED_ITEM_TITLE_STYLE: CSSProperties = {
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--syn-accent)",
};

// ─── Edit mode container ──────────────────────────────────────────────────────

const EDIT_CONTAINER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: "1 1 0",
  overflow: "hidden",
  minHeight: 0,
};

const EDIT_TOOLBAR_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "10px 16px",
  borderBottom: "1px solid var(--syn-border)",
  gap: 12,
  flexShrink: 0,
  minHeight: 48,
  background: "var(--syn-bg-soft)",
};

const EDIT_TITLE_STYLE: CSSProperties = {
  flex: 1,
  margin: 0,
  fontSize: 15,
  fontWeight: 600,
  color: "var(--syn-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
