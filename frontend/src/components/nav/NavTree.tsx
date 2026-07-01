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
 */

import { useRef, type CSSProperties, type ElementType } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  LayoutDashboard,
  Lightbulb,
  Users,
  BookOpen,
  GitBranch,
  BarChart3,
  HelpCircle,
  File,
} from "lucide-react";
import {
  useGraphStore,
  useTreeCollapsed,
  selectSelectedNodeId,
  selectSelectPage,
  selectToggleGroup,
} from "../../store/graphStore";
import { useNavTreeData } from "./useNavTreeData";
import type { TreeRow, KnownType } from "./useNavTreeData";

// ─── Colour palette — consumed from CSS custom properties (theme.css) ─────────
// Values reference --syn-type-* so they inherit light/dark theme automatically.
// We use inline CSS strings; the actual color is resolved by the browser at paint time.

const TYPE_COLOR: Record<KnownType, string> = {
  overview:   "var(--syn-type-overview)",
  concept:    "var(--syn-type-concept)",
  entity:     "var(--syn-type-entity)",
  source:     "var(--syn-type-source)",
  synthesis:  "var(--syn-type-synthesis)",
  comparison: "var(--syn-type-comparison)",
  query:      "var(--syn-type-query)",
  other:      "var(--syn-type-other)",
};

/**
 * Lucide icon component per type — colored by the matching --syn-type-* token.
 * Used in GroupHeader only; PageRow retains a small colored dot for compactness.
 */
const TYPE_ICON: Record<KnownType, ElementType> = {
  overview:   LayoutDashboard,
  concept:    Lightbulb,
  entity:     Users,
  source:     BookOpen,
  synthesis:  GitBranch,
  comparison: BarChart3,
  query:      HelpCircle,
  other:      File,
};

const TYPE_LABEL: Record<KnownType, string> = {
  overview:   "Overview",
  concept:    "Concepts",
  entity:     "Entities",
  source:     "Sources",
  synthesis:  "Synthesis",
  comparison: "Comparisons",
  query:      "Queries",
  other:      "Other",
};

// ─── Row heights (px) ─────────────────────────────────────────────────────────

const GROUP_ROW_HEIGHT = 32;
const PAGE_ROW_HEIGHT = 28;

// ─── Component ────────────────────────────────────────────────────────────────

interface NavTreeProps {
  /** Vault id forwarded from the AppShell context. */
  vaultId: string;
}

export function NavTree({ vaultId }: NavTreeProps) {
  // Store subscriptions — typed selectors + shallow where needed (I3)
  const selectedNodeId = useGraphStore(selectSelectedNodeId);
  const selectPage = useGraphStore(selectSelectPage);
  const toggleGroup = useGraphStore(selectToggleGroup);
  const collapsed = useTreeCollapsed(); // shallow equality

  // Data hook
  const { rows, loading, error } = useNavTreeData(vaultId, collapsed);

  // Virtualizer
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => {
      const row = rows[index];
      return row?.kind === "group" ? GROUP_ROW_HEIGHT : PAGE_ROW_HEIGHT;
    },
    overscan: 10,
  });

  // ── Render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="nav-tree nav-tree--loading" role="status" aria-label="Loading pages">
        <span className="nav-tree__spinner" aria-hidden="true" />
        <span className="nav-tree__loading-text">Loading…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="nav-tree nav-tree--error" role="alert">
        <span className="nav-tree__error-icon" aria-hidden="true">!</span>
        <span className="nav-tree__error-text">{error}</span>
      </div>
    );
  }

  const totalHeight = virtualizer.getTotalSize();
  const items = virtualizer.getVirtualItems();

  return (
    <nav
      className="nav-tree"
      aria-label="Wiki pages"
      data-testid="nav-tree"
      // height:100% + flex-column gives the nav a *bounded* height so the inner
      // scroll container's height resolves to a real pixel value (not auto).
      // Without this, scrollRef.current.clientHeight equals the total virtual
      // content height and TanStack Virtual thinks the whole list is visible,
      // rendering every row (I4 violation).
      style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}
    >
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

            // row.kind === "page"
            return (
              <PageRow
                key={row.id}
                row={row}
                selected={row.id === selectedNodeId}
                style={{ position: "absolute", top: virtualRow.start, width: "100%" }}
                onClick={() => selectPage(row.id, "tree")}
              />
            );
          })}
        </div>
      </div>
    </nav>
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
        padding: "0 8px",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        textAlign: "left",
        gap: 6,
        color: "var(--syn-text-muted)",
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        userSelect: "none",
      }}
      aria-expanded={ariaExpanded}
      aria-label={`${label}, ${row.count} items, ${ariaExpanded ? "collapse" : "expand"}`}
      onClick={onToggle}
      data-type={row.type}
    >
      {/* Type icon — replaces the colored dot; uses per-type CSS variable */}
      <TypeIcon
        size={14}
        aria-hidden="true"
        style={{ color, flexShrink: 0 }}
        data-testid={`type-icon-${row.type}`}
      />
      {/* Label */}
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      {/* Count badge */}
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
        {row.count}
      </span>
      {/* Chevron */}
      <span
        aria-hidden="true"
        style={{
          fontSize: 10,
          color: "var(--syn-text-dim)",
          transform: ariaExpanded ? "rotate(0deg)" : "rotate(-90deg)",
          transition: "transform 0.15s ease",
          flexShrink: 0,
        }}
      >
        &#9660;
      </span>
    </button>
  );
}

interface PageRowProps {
  row: Extract<TreeRow, { kind: "page" }>;
  selected: boolean;
  style: CSSProperties;
  onClick: () => void;
}

function PageRow({ row, selected, style, onClick }: PageRowProps) {
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
        padding: "0 8px 0 20px",
        border: "none",
        // llm_wiki style: accent-soft bg + accent text when selected; transparent otherwise
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
      aria-label={row.title}
      onClick={onClick}
      data-page-id={row.id}
      data-type={row.type}
    >
      {/* Type dot — compact file-row indicator; matches llm_wiki file-row style */}
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          opacity: selected ? 1 : 0.7,
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
    </button>
  );
}
