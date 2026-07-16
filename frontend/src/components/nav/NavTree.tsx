/**
 * NavTree.tsx — virtualised file tree panel.
 *
 * INVARIANT I4: uses TanStack Virtual (useVirtualizer) so that 1000+ nodes render
 * at 60fps with < 30 DOM nodes visible at any time. Group headers and page rows share
 * the same flat virtualizer index — no nested virtualizers.
 *
 * INVARIANT I3: subscribes to graphStore only via typed selectors + useShallow.
 *
 * Light design (llm_wiki parity): white/--syn-bg-soft background, --syn-text labels,
 * per-type Lucide icons colored from --syn-type-* tokens, selected row = --syn-accent-soft bg
 * + --syn-accent text.
 *
 * Icons: lucide-react tree-shaken named imports [F1].
 * Group headers: type icon (16px) colored by var(--syn-type-*) replaces the colored dot.
 * Page rows: small type dot retained (6px) — consistent with llm_wiki file-row style.
 *
 * R7-2: "+" button in header opens a modal to create a new page.
 */

import {
  useRef,
  useState,
  useCallback,
  useLayoutEffect,
  useMemo,
  type CSSProperties,
  type ElementType,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import {
  LayoutDashboard,
  Lightbulb,
  Users,
  BookOpen,
  GitBranch,
  BarChart3,
  HelpCircle,
  File,
  Plus,
  FolderKey,
  FileText,
  X,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useGraphStore, selectNodes } from "../../store/graphStore";
import {
  useTreeCollapsed,
  selectSelectedNodeId,
  selectSelectPage,
  selectToggleGroup,
  useAppStore,
} from "../../store/appStore";
import { useNavTreeData } from "./useNavTreeData";
import type { TreeRow, KnownType } from "./useNavTreeData";
import { createPage } from "../../api/pagesClient";
import type { NewPageType } from "../../api/pagesClient";
import { ApiError } from "../../api/graphClient";
import { showToast } from "../common/Toast";
import { MetaFileView } from "../wiki/MetaFileView";
import type { VaultMetaFile } from "../../api/vaultMetaClient";

// ─── Colour palette — consumed from CSS custom properties (theme.css) ─────────
// Values reference --syn-type-* so they inherit light/dark theme automatically.
// We use inline CSS strings; the actual color is resolved by the browser at paint time.

const TYPE_COLOR: Record<KnownType, string> = {
  overview: "var(--syn-type-overview)",
  concept: "var(--syn-type-concept)",
  entity: "var(--syn-type-entity)",
  source: "var(--syn-type-source)",
  synthesis: "var(--syn-type-synthesis)",
  comparison: "var(--syn-type-comparison)",
  query: "var(--syn-type-query)",
  other: "var(--syn-type-other)",
};

/**
 * Lucide icon component per type — colored by the matching --syn-type-* token.
 * Used in GroupHeader only; PageRow retains a small colored dot for compactness.
 */
const TYPE_ICON: Record<KnownType, ElementType> = {
  overview: LayoutDashboard,
  concept: Lightbulb,
  entity: Users,
  source: BookOpen,
  synthesis: GitBranch,
  comparison: BarChart3,
  query: HelpCircle,
  other: File,
};

const TYPE_LABEL: Record<KnownType, string> = {
  overview: "Overview",
  concept: "Concepts",
  entity: "Entities",
  source: "Sources",
  synthesis: "Synthesis",
  comparison: "Comparisons",
  query: "Queries",
  other: "Other",
};

// ─── Row heights (px) ─────────────────────────────────────────────────────────
// AC-R11-4-BUG3: both heights are >= 32px so estimateSize never returns a
// sub-32 value. The old PAGE_ROW_HEIGHT of 28 was raised to 32 to match the
// minimum required by the virtualizer zero-height fix.

const GROUP_ROW_HEIGHT = 32;
const PAGE_ROW_HEIGHT = 32;

// ─── New Page types available for creation (AC-R7-2-1) ───────────────────────

const NEW_PAGE_TYPES: NewPageType[] = [
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
];

// ─── NewPageModal ─────────────────────────────────────────────────────────────

interface NewPageModalProps {
  onClose: () => void;
  onCreated: (pageId: string) => void;
}

function NewPageModal({ onClose, onCreated }: NewPageModalProps) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const [pageType, setPageType] = useState<NewPageType>("concept");
  const [dir, setDir] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);

  const titleRef = useRef<HTMLInputElement>(null);

  // Focus the title input on mount
  const focusTitleRef = useCallback((el: HTMLInputElement | null) => {
    if (el) {
      if (titleRef.current) titleRef.current.value = "";
      el.focus();
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      // AC-R7-2-3: client-side validation
      setTitleError(t("nav.newPage.titleRequired"));
      return;
    }
    setTitleError(null);
    setConflictError(null);
    setSubmitting(true);
    try {
      const dirTrimmed = dir.trim();
      const resp = await createPage({
        title: trimmedTitle,
        page_type: pageType,
        ...(dirTrimmed ? { dir: dirTrimmed } : {}),
      });
      showToast(t("nav.newPage.created", { title: resp.title }), "success");
      onCreated(resp.id);
      onClose();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        // AC-R7-2-3 inline conflict error
        setConflictError(t("nav.newPage.conflict"));
      } else {
        showToast(err instanceof Error ? err.message : String(err), "error");
      }
    } finally {
      setSubmitting(false);
    }
  }, [title, pageType, dir, t, onCreated, onClose]);

  // Backdrop click / Esc close
  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Enter" && !submitting) void handleSubmit();
    },
    [onClose, handleSubmit, submitting],
  );

  return (
    <div
      data-testid="new-page-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("nav.newPage.title")}
        data-testid="new-page-modal"
        onKeyDown={handleKeyDown}
        style={{
          background: "var(--syn-bg-card, var(--syn-bg-soft))",
          border: "1px solid var(--syn-border)",
          borderRadius: 8,
          boxShadow: "0 8px 32px rgba(0,0,0,0.35)",
          padding: "20px 24px",
          width: "min(440px, calc(100vw - 32px))",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "var(--syn-text)" }}>
          {t("nav.newPage.title")}
        </h2>

        {/* Title field */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "var(--syn-text-muted)" }}>
            {t("nav.newPage.titleLabel")}{" "}
            <span aria-hidden="true" style={{ color: "var(--syn-red)" }}>
              *
            </span>
          </label>
          <input
            ref={focusTitleRef}
            type="text"
            data-testid="new-page-title-input"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (titleError) setTitleError(null);
              if (conflictError) setConflictError(null);
            }}
            placeholder={t("nav.newPage.titlePlaceholder")}
            style={INPUT_STYLE}
            aria-required="true"
            aria-invalid={titleError !== null ? "true" : undefined}
            aria-describedby={titleError ? "new-page-title-error" : undefined}
          />
          {titleError && (
            <span
              id="new-page-title-error"
              role="alert"
              style={{ fontSize: 11, color: "var(--syn-red)" }}
            >
              {titleError}
            </span>
          )}
          {conflictError && (
            <span role="alert" style={{ fontSize: 11, color: "var(--syn-red)" }}>
              {conflictError}
            </span>
          )}
        </div>

        {/* Type field */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "var(--syn-text-muted)" }}>
            {t("nav.newPage.typeLabel")}
          </label>
          <select
            data-testid="new-page-type-select"
            value={pageType}
            onChange={(e) => setPageType(e.target.value as NewPageType)}
            style={INPUT_STYLE}
          >
            {NEW_PAGE_TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`nav.newPage.type.${type}`)}
              </option>
            ))}
          </select>
        </div>

        {/* Directory field (optional) */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "var(--syn-text-muted)" }}>
            {t("nav.newPage.dirLabel")}
          </label>
          <input
            type="text"
            data-testid="new-page-dir-input"
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder={t("nav.newPage.dirPlaceholder")}
            style={INPUT_STYLE}
          />
        </div>

        {/* Actions */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            data-testid="new-page-cancel-btn"
            onClick={onClose}
            style={{
              padding: "6px 14px",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              background: "transparent",
              color: "var(--syn-text-muted)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t("nav.newPage.cancel")}
          </button>
          <button
            type="button"
            data-testid="new-page-create-btn"
            disabled={submitting}
            onClick={() => {
              void handleSubmit();
            }}
            style={{
              padding: "6px 14px",
              border: "1px solid var(--syn-accent)",
              borderRadius: 6,
              background: "var(--syn-accent)",
              color: "#fff",
              fontSize: 12,
              cursor: submitting ? "not-allowed" : "pointer",
              fontWeight: 600,
              opacity: submitting ? 0.6 : 1,
            }}
          >
            {submitting ? "…" : t("nav.newPage.create")}
          </button>
        </div>
      </div>
    </div>
  );
}

const INPUT_STYLE: CSSProperties = {
  fontSize: 12,
  padding: "6px 8px",
  border: "1px solid var(--syn-border)",
  borderRadius: 4,
  background: "var(--syn-bg)",
  color: "var(--syn-text)",
  width: "100%",
  boxSizing: "border-box",
};

// ─── Component ────────────────────────────────────────────────────────────────

interface NavTreeProps {
  /** Vault id forwarded from the AppShell context. */
  vaultId: string;
}

export function NavTree({ vaultId }: NavTreeProps) {
  const { t } = useTranslation();

  // Store subscriptions — typed selectors + shallow where needed (I3)
  const selectedNodeId = useAppStore(selectSelectedNodeId);
  const selectPage = useAppStore(selectSelectPage);
  const toggleGroup = useAppStore(selectToggleGroup);
  const collapsed = useTreeCollapsed(); // shallow equality

  // Graph node degree map — used to show connection counts in page rows (I3: shallow).
  // Only populated after the graph loads; rows without a match simply omit the count.
  const graphNodes = useGraphStore(useShallow(selectNodes));
  const degreeMap = useMemo<Map<string, number>>(() => {
    const m = new Map<string, number>();
    for (const n of graphNodes) {
      if (n.degree != null) m.set(n.id, n.degree);
    }
    return m;
  }, [graphNodes]);

  // Data hook (WS-D8: now also returns metaFiles; NavFilter: filterLabel + clearFilter)
  const { rows, loading, error, refresh, filterLabel, clearFilter } = useNavTreeData(
    vaultId,
    collapsed,
  );

  // New page modal state (R7-2)
  const [showNewPageModal, setShowNewPageModal] = useState(false);

  // WS-D8: meta file drawer state — null = closed
  const [openMetaFile, setOpenMetaFile] = useState<VaultMetaFile | null>(null);

  const handlePageCreated = useCallback(
    (pageId: string) => {
      // Navigate to the new page and refresh the tree
      selectPage(pageId, "tree");
      void refresh();
    },
    [selectPage, refresh],
  );

  // Virtualizer
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => {
      const row = rows[index];
      // AC-R11-4-BUG3: always return >= 32 so getTotalSize() > 0 on initial
      // render when the row data is available.
      return row?.kind === "group" ? GROUP_ROW_HEIGHT : PAGE_ROW_HEIGHT;
    },
    overscan: 10,
  });

  // AC-R11-4-BUG3: remeasure on mount and whenever the scroll container first
  // acquires non-zero height. In JSDOM (tests) and on very fast mounts in the
  // browser the scroll element may have clientHeight = 0 when the virtualizer
  // first reads it (ResizeObserver hasn't fired yet). A ResizeObserver here
  // calls virtualizer.measure() once when height becomes non-zero so the
  // virtual items are immediately correct without waiting for a scroll event.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    // Trigger an immediate remeasure so that if the container already has
    // height (e.g. after a hot-reload or when the panel has a known size),
    // the virtualizer reflects it without waiting for a ResizeObserver tick.
    virtualizer.measure();

    if (typeof ResizeObserver === "undefined") return;

    let measured = false;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      const h = entry?.contentRect.height ?? el.clientHeight;
      if (h > 0 && !measured) {
        measured = true;
        virtualizer.measure();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
    // virtualizer is stable (same instance across renders); scrollRef.current
    // is read inside the effect so it is not a dependency here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="nav-tree nav-tree--loading" role="status" aria-label={t("common.loading")}>
        <span className="nav-tree__spinner" aria-hidden="true" />
        <span className="nav-tree__loading-text">{t("common.loading")}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="nav-tree nav-tree--error" role="alert">
        <span className="nav-tree__error-icon" aria-hidden="true">
          !
        </span>
        <span className="nav-tree__error-text">{error}</span>
      </div>
    );
  }

  const totalHeight = virtualizer.getTotalSize();
  const items = virtualizer.getVirtualItems();

  return (
    <>
      <nav
        className="nav-tree"
        aria-label={t("navTree.ariaLabel")}
        data-testid="nav-tree"
        // height:100% + flex-column gives the nav a *bounded* height so the inner
        // scroll container's height resolves to a real pixel value (not auto).
        // Without this, scrollRef.current.clientHeight equals the total virtual
        // content height and TanStack Virtual thinks the whole list is visible,
        // rendering every row (I4 violation).
        style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}
      >
        {/* ── Tree header with + button (R7-2) ── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "6px 8px 4px",
            flexShrink: 0,
            borderBottom: "1px solid var(--syn-border-subtle, var(--syn-border))",
          }}
        >
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: "var(--syn-text-dim)",
            }}
          >
            {t("nav.wiki")}
          </span>
          <button
            type="button"
            data-testid="nav-tree-new-page-btn"
            onClick={() => setShowNewPageModal(true)}
            title={t("nav.newPage.title")}
            aria-label={t("nav.newPage.title")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "2px",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: "var(--syn-text-dim)",
              borderRadius: 3,
              lineHeight: 1,
            }}
          >
            <Plus size={13} aria-hidden="true" />
          </button>
        </div>

        {/* ── Active filter banner (NavFilter — dismissible) ── */}
        {filterLabel !== null && (
          <div
            data-testid="nav-tree-filter-banner"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 8px",
              background: "color-mix(in srgb, var(--syn-accent) 10%, var(--syn-bg-soft) 90%)",
              borderBottom:
                "1px solid color-mix(in srgb, var(--syn-accent) 25%, var(--syn-border) 75%)",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                flex: 1,
                fontSize: 10,
                color: "var(--syn-text-muted)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {t("navTree.filterBanner", { label: filterLabel })}
            </span>
            <button
              type="button"
              data-testid="nav-tree-filter-clear"
              onClick={clearFilter}
              title={t("navTree.clearFilter")}
              aria-label={t("navTree.clearFilter")}
              style={{
                display: "flex",
                alignItems: "center",
                padding: 2,
                border: "none",
                background: "transparent",
                cursor: "pointer",
                color: "var(--syn-text-dim)",
                borderRadius: 3,
                flexShrink: 0,
              }}
            >
              <X size={11} aria-hidden="true" />
            </button>
          </div>
        )}

        <div
          ref={scrollRef}
          className="nav-tree__scroll"
          // flex:1 + minHeight:0 (not height:100%) so the div fills available space
          // without forcing the parent to grow to fit it — the bounded height above
          // propagates correctly to TanStack Virtual's scroll element measurement.
          style={{ overflow: "auto", flex: 1, minHeight: 0 }}
        >
          {/* Outer spacer for correct scroll height */}
          <div style={{ height: totalHeight, position: "relative" }}>
            {items.map((virtualRow) => {
              const row = rows[virtualRow.index] as TreeRow;

              if (row.kind === "group") {
                return (
                  <GroupHeader
                    key={`group-${row.type}`}
                    row={row}
                    style={{ position: "absolute", top: virtualRow.start, width: "100%" }}
                    onToggle={() => toggleGroup(row.type)}
                  />
                );
              }

              // WS-D8: vault-meta section header
              if (row.kind === "vault-meta-group") {
                return (
                  <VaultMetaGroupHeader
                    key="vault-meta-group"
                    count={row.count}
                    style={{ position: "absolute", top: virtualRow.start, width: "100%" }}
                    label={t("meta.vaultSection")}
                  />
                );
              }

              // WS-D8: individual meta file row
              if (row.kind === "meta") {
                return (
                  <MetaRow
                    key={row.file.path}
                    file={row.file}
                    selected={openMetaFile?.path === row.file.path}
                    style={{ position: "absolute", top: virtualRow.start, width: "100%" }}
                    onClick={() => setOpenMetaFile(row.file)}
                  />
                );
              }

              // row.kind === "page"
              return (
                <PageRow
                  key={row.id}
                  row={row}
                  selected={row.id === selectedNodeId}
                  count={degreeMap.get(row.id)}
                  style={{ position: "absolute", top: virtualRow.start, width: "100%" }}
                  onClick={() => selectPage(row.id, "tree")}
                />
              );
            })}
          </div>
        </div>
      </nav>

      {/* New page modal (R7-2) */}
      {showNewPageModal && (
        <NewPageModal onClose={() => setShowNewPageModal(false)} onCreated={handlePageCreated} />
      )}

      {/* WS-D8 + v1.5 P1: meta file drawer — reads AND edits schema.md / purpose.md */}
      <MetaFileView
        file={openMetaFile}
        onClose={() => setOpenMetaFile(null)}
        onSaved={(updated) => {
          // Reflect the saved content in the open drawer + refresh the tree cache.
          setOpenMetaFile(updated);
          void refresh();
        }}
      />
    </>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

interface GroupHeaderProps {
  row: Extract<TreeRow, { kind: "group" }>;
  style: CSSProperties;
  onToggle: () => void;
}

function GroupHeader({ row, style, onToggle }: GroupHeaderProps) {
  const color = TYPE_COLOR[row.type];
  const label = TYPE_LABEL[row.type];
  const ariaExpanded = !row.collapsed;
  const TypeIcon = TYPE_ICON[row.type];

  return (
    <button
      className="nav-tree__group-header"
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: GROUP_ROW_HEIGHT,
        padding: "0 10px 0 8px",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        textAlign: "left",
        gap: 6,
        color: "var(--syn-text-dim)",
        fontFamily: "var(--syn-font-mono, monospace)",
        fontSize: 10.5,
        fontWeight: 600,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        userSelect: "none",
      }}
      aria-expanded={ariaExpanded}
      aria-label={`${label}, ${row.count} items, ${ariaExpanded ? "collapse" : "expand"}`}
      onClick={onToggle}
      data-type={row.type}
    >
      {/* Type icon — colored by per-type CSS variable */}
      <TypeIcon
        size={13}
        aria-hidden="true"
        style={{ color, flexShrink: 0 }}
        data-testid={`type-icon-${row.type}`}
      />
      {/* Label */}
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      {/* Count — mono, right-aligned */}
      <span
        aria-hidden="true"
        style={{
          fontFamily: "var(--syn-font-mono, monospace)",
          fontSize: 10,
          color: "var(--syn-text-dim)",
          flexShrink: 0,
        }}
      >
        {row.count}
      </span>
      {/* Chevron */}
      <span
        aria-hidden="true"
        style={{
          fontSize: 9,
          color: "var(--syn-text-dim)",
          transform: ariaExpanded ? "rotate(0deg)" : "rotate(-90deg)",
          transition: "transform 0.15s ease",
          flexShrink: 0,
          marginLeft: 2,
        }}
      >
        &#9660;
      </span>
    </button>
  );
}

// ─── WS-D8: Vault Meta sub-components ────────────────────────────────────────

interface VaultMetaGroupHeaderProps {
  count: number;
  style: CSSProperties;
  label: string;
}

/**
 * VaultMetaGroupHeader — section header for the "Vault / Meta" tree group.
 *
 * Intentionally NOT collapsible: the section always has exactly 2 files
 * (schema.md + purpose.md) and collapsing it adds no value.
 */
function VaultMetaGroupHeader({ count, style, label }: VaultMetaGroupHeaderProps) {
  return (
    <div
      className="nav-tree__vault-meta-group"
      data-testid="nav-tree-vault-meta-group"
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: GROUP_ROW_HEIGHT,
        padding: "0 8px",
        gap: 6,
        color: "var(--syn-text-muted)",
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        userSelect: "none",
        borderTop: "1px solid var(--syn-border-subtle, var(--syn-border))",
        marginTop: 2,
        boxSizing: "border-box",
      }}
    >
      <FolderKey
        size={14}
        aria-hidden="true"
        style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
        data-testid="vault-meta-group-icon"
      />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      <span
        aria-hidden="true"
        style={{
          fontSize: 10,
          color: "var(--syn-text-dim)",
          background: "var(--syn-surface-sunken)",
          border: "1px solid var(--syn-border-subtle)",
          borderRadius: 10,
          padding: "1px 5px",
          flexShrink: 0,
        }}
      >
        {count}
      </span>
    </div>
  );
}

interface MetaRowProps {
  file: VaultMetaFile;
  selected: boolean;
  style: CSSProperties;
  onClick: () => void;
}

/**
 * MetaRow — a single meta file entry in the Vault/Meta section.
 *
 * Visually mirrors PageRow but uses a FileText icon instead of a type-colored dot,
 * and carries `data-meta-path` instead of `data-page-id` to distinguish it from
 * regular wiki page rows in tests and analytics.
 */
function MetaRow({ file, selected, style, onClick }: MetaRowProps) {
  return (
    <button
      className={`nav-tree__meta-row${selected ? " nav-tree__meta-row--selected" : ""}`}
      data-testid={`nav-tree-meta-row-${file.name}`}
      data-meta-path={file.path}
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: PAGE_ROW_HEIGHT,
        padding: "0 8px 0 20px",
        border: "none",
        background: selected ? "var(--syn-accent-soft)" : "transparent",
        cursor: "pointer",
        textAlign: "left",
        gap: 6,
        color: selected ? "var(--syn-accent)" : "var(--syn-text-muted)",
        fontSize: 13,
        outline: "none",
        borderRadius: 4,
        transition: "background 0.1s ease, color 0.1s ease",
      }}
      aria-current={selected ? "page" : undefined}
      aria-label={file.title}
      onClick={onClick}
    >
      <FileText
        size={12}
        aria-hidden="true"
        style={{
          color: selected ? "var(--syn-accent)" : "var(--syn-text-dim)",
          flexShrink: 0,
          opacity: selected ? 1 : 0.8,
        }}
      />
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {file.title}
      </span>
    </button>
  );
}

interface PageRowProps {
  row: Extract<TreeRow, { kind: "page" }>;
  selected: boolean;
  /** Connection degree from the graph store — shown as a mono count right-aligned. */
  count?: number | undefined;
  style: CSSProperties;
  onClick: () => void;
}

function PageRow({ row, selected, count, style, onClick }: PageRowProps) {
  const color = TYPE_COLOR[row.type];

  return (
    <button
      className={`nav-tree__page-row${selected ? " nav-tree__page-row--selected" : ""}`}
      style={{
        ...style,
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: PAGE_ROW_HEIGHT,
        padding: "0 10px 0 22px",
        border: "none",
        // llm_wiki style: accent-soft bg + accent text when selected; transparent otherwise
        background: selected ? "var(--syn-accent-soft)" : "transparent",
        cursor: "pointer",
        textAlign: "left",
        gap: 9,
        color: selected ? "var(--syn-accent)" : "var(--syn-text-muted)",
        fontSize: 13.5,
        outline: "none",
        borderRadius: 4,
        transition: "background 0.1s ease, color 0.1s ease",
      }}
      aria-current={selected ? "page" : undefined}
      aria-label={row.title}
      onClick={onClick}
      data-page-id={row.id}
      data-type={row.type}
    >
      {/* Type dot — square-ish colored indicator matching design reference */}
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: 2,
          background: color,
          flexShrink: 0,
          opacity: selected ? 1 : 0.75,
        }}
      />
      {/* Title */}
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {row.title}
      </span>
      {/* Connection count — mono, right-aligned, hidden when 0 or unavailable */}
      {count != null && count > 0 && (
        <span
          aria-hidden="true"
          style={{
            fontFamily: "var(--syn-font-mono, monospace)",
            fontSize: 11,
            color: "var(--syn-text-dim)",
            flexShrink: 0,
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
}
