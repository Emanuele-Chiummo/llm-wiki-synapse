/**
 * NavTree.tsx — virtualised file tree panel.
 *
 * INVARIANT I4: uses TanStack Virtual (useVirtualizer) so that 1000+ nodes render
 * at 60fps with < 30 DOM nodes visible at any time. Group headers and page rows share
 * the same flat virtualizer index — no nested virtualizers.
 *
 * INVARIANT I3: subscribes to graphStore only via typed selectors + useShallow.
 */

import { useRef, type CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  useGraphStore,
  useTreeCollapsed,
  selectSelectedNodeId,
  selectSelectPage,
  selectToggleGroup,
} from "../../store/graphStore";
import { useNavTreeData } from "./useNavTreeData";
import type { TreeRow, KnownType } from "./useNavTreeData";

// ─── Colour palette (mirrors graph legend / CVD-safe) ─────────────────────────

const TYPE_COLOR: Record<KnownType, string> = {
  concept: "#58a6ff",
  entity: "#3fb950",
  source: "#ffa657",
  synthesis: "#d2a8ff",
  comparison: "#f2cc60",
  other: "#8b949e",
};

const TYPE_LABEL: Record<KnownType, string> = {
  concept: "Concepts",
  entity: "Entities",
  source: "Sources",
  synthesis: "Synthesis",
  comparison: "Comparisons",
  other: "Other",
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
        color: "#8b949e",
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
      {/* Colour dot */}
      <span
        aria-hidden="true"
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
        }}
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
          color: "#484f58",
          background: "#21262d",
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
        background: selected ? "#1f2937" : "transparent",
        cursor: "pointer",
        textAlign: "left",
        gap: 6,
        color: selected ? "#e6edf3" : "#8b949e",
        fontSize: 13,
        outline: selected ? `1px solid ${color}` : "none",
        outlineOffset: -1,
        borderRadius: 4,
        transition: "background 0.1s ease, color 0.1s ease",
      }}
      aria-current={selected ? "page" : undefined}
      aria-label={row.title}
      onClick={onClick}
      data-page-id={row.id}
      data-type={row.type}
    >
      {/* Type dot */}
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          opacity: selected ? 1 : 0.6,
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
