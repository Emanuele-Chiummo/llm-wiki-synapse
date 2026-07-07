/**
 * NavRail.tsx — persistent ~72px left icon rail with persistent text labels.
 *
 * Order (top → bottom):
 *   Logo (branding, non-nav)
 *   Chat · Wiki · Sources · Search · Graph  (TOP_ITEMS)
 *   [separator]
 *   Lint · Review · Deep Search · Ingest    (M5_ITEMS)
 *   [spacer]
 *   Settings  (pinned bottom)
 *
 * Rail/section decision [F11 / v0.6]:
 *   - "sources" (FolderOpen icon, nav.sources label) → raw-source file browser (SourcesView).
 *   - "ingest"  (Activity icon, nav.ingest label) → ingest run-history / cost ledger (IngestView).
 *     IngestView is now in the secondary M5 group so the ingest cost ledger remains reachable.
 *   - This keeps the TOP_ITEMS group at 5 user-facing actions (unchanged item count).
 *
 * CHANGE v0.6-SEARCH: Search added to rail between Sources and Graph (F5/llm_wiki parity).
 * CHANGE v0.6-SOURCES [F11]: "Sources" rail item now maps to "sources" section (SourcesView);
 *   "Ingest" item moved to M5 group pointing to "ingest" section (IngestView run-history).
 *
 * CHANGE F1-HARD-NAV-LABELS: rail widened to 72px; each button renders the icon
 * SVG with a <span> caption below it (10px, centered, truncate with ellipsis).
 * Button height increased to 52px to accommodate icon + label.
 *
 * INVARIANT I3: reads activeSection (scalar) + setActiveSection only from graphStore.
 * i18n: all labels are translation keys.
 *
 * Icons: lucide-react tree-shaken named imports [F1].
 * Icon size: 20px; inactive color var(--syn-text-dim); active var(--syn-accent).
 */

import { useCallback, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  House,
  MessageSquare,
  FileText,
  FolderOpen,
  Search,
  Share2,
  ClipboardCheck,
  ListChecks,
  Globe,
  Settings,
  Activity,
  FileDown,
} from "lucide-react";
import { useGraphStore, selectActiveSection, selectSetActiveSection } from "../../store/graphStore";
import type { Section } from "../../store/graphStore";
import { useIngestRunningCount } from "../../store/ingestStore";
import { useStatusStore, selectReviewPending } from "../../store/statusStore";

// ─── Lucide icon size constant ────────────────────────────────────────────────

const ICON_SIZE = 20;

// ─── Rail item types ──────────────────────────────────────────────────────────

interface RailItem {
  id: Section;
  icon: ReactNode;
  labelKey: string;
}

/**
 * Active items. Home at the top slot freed by R11-3 logo removal [F18][R12-1].
 * Search between Sources and Graph (llm_wiki parity, F5/v0.6).
 * "sources" now maps to SourcesView (file browser) — [F11 / v0.6].
 */
const TOP_ITEMS: RailItem[] = [
  { id: "home",    icon: <House         size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.home" },
  { id: "chat",    icon: <MessageSquare size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.chat" },
  { id: "pages",   icon: <FileText      size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.wiki" },
  { id: "sources", icon: <FolderOpen    size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.sources" },
  { id: "search",  icon: <Search        size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.search" },
  { id: "graph",   icon: <Share2        size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.graph" },
];

/**
 * M5_ITEMS — Lint (K2), Review (F9), Deep Search (F10), Ingest run-history (cost ledger),
 * and Convert (F12/R11-1) — dedicated Marker PDF conversion surface [R11-1 A1].
 * "ingest" kept here so the cost ledger is always reachable — [F11 / v0.6].
 */
const M5_ITEMS: RailItem[] = [
  { id: "lint",        icon: <ClipboardCheck size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.lint" },
  { id: "review",      icon: <ListChecks     size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.review" },
  { id: "deep-search", icon: <Globe          size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.deepSearch" },
  { id: "ingest",      icon: <Activity       size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.ingest" },
  { id: "convert",     icon: <FileDown       size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.convert" },
];

const BOTTOM_ITEMS: RailItem[] = [
  { id: "settings", icon: <Settings size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.settings" },
];

// ─── Logo removed (R11-3) ────────────────────────────────────────────────────
// The Logo() component was removed as part of R11-3 (logo deduplication).
// Branding is handled exclusively by the Header wordmark (Header.tsx).
// The freed top slot is replaced with intentional top padding of 8px applied
// to the nav's paddingTop value (see NavRail below).

// ─── Component ────────────────────────────────────────────────────────────────

export function NavRail() {
  const { t } = useTranslation();
  const activeSection = useGraphStore(selectActiveSection);
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const runningCount = useIngestRunningCount();
  // Pending review items — fed by the existing 30s /status poll via statusStore (I3).
  const reviewPending = useStatusStore(selectReviewPending);

  const handleItemClick = useCallback(
    (item: RailItem) => {
      setActiveSection(item.id);
    },
    [setActiveSection],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      // Only iterate currently-rendered items (M5_ITEMS is empty in M4)
      const allItems = [...TOP_ITEMS, ...M5_ITEMS, ...BOTTOM_ITEMS];
      const currentIdx = allItems.findIndex((i) => i.id === activeSection);
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        const next = allItems[(currentIdx + 1) % allItems.length];
        if (next) setActiveSection(next.id);
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = allItems[(currentIdx - 1 + allItems.length) % allItems.length];
        if (prev) setActiveSection(prev.id);
      }
    },
    [activeSection, setActiveSection],
  );

  return (
    <nav
      className="nav-rail"
      aria-label="Main navigation"
      data-testid="nav-rail"
      onKeyDown={handleKeyDown}
      style={{
        width: 72,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        background: "var(--syn-bg-soft)",
        borderRight: "1px solid var(--syn-border)",
        paddingTop: 8,
        paddingBottom: 8,
        gap: 2,
        overflow: "hidden",
      }}
    >
      {/* R11-3: Logo removed — branding lives in Header.tsx only.
          The nav starts 8px below the top edge (paddingTop above) so the
          first nav button is not flush against the rail edge. */}

      {/* Top items */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center", width: "100%" }}>
        {TOP_ITEMS.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={0}
            label={t(item.labelKey)}
            onClick={() => handleItemClick(item)}
          />
        ))}
      </div>

      {/* M5 items (Lint + Review + Deep Search + Ingest run-history) */}
      {M5_ITEMS.length > 0 && (
        <>
          {/* Full-width divider + group label (UXA-01) */}
          <div style={{ width: "100%", height: 1, background: "var(--syn-border)", margin: "4px 0 0" }} />
          <span
            aria-hidden="true"
            style={{
              fontSize: 9,
              fontWeight: 600,
              // Tightened + width-constrained so "STRUMENTI" fits the ~64px rail
              // instead of clipping to "TRUMENT" at the edges.
              letterSpacing: "0.02em",
              textTransform: "uppercase",
              color: "var(--syn-text-dim)",
              opacity: 0.7,
              padding: "2px 2px",
              maxWidth: "100%",
              textAlign: "center",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              userSelect: "none",
            }}
          >
            {t("nav.toolsGroup")}
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center", width: "100%" }}>
            {M5_ITEMS.map((item) => (
              <RailButton
                key={item.id}
                item={item}
                isActive={item.id === activeSection}
                badge={
                  item.id === "ingest"
                    ? runningCount
                    : item.id === "review"
                      ? (reviewPending ?? 0)
                      : 0
                }
                label={t(item.labelKey)}
                onClick={() => handleItemClick(item)}
              />
            ))}
          </div>
        </>
      )}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Bottom items (pinned) */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center", width: "100%" }}>
        {BOTTOM_ITEMS.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={0}
            label={t(item.labelKey)}
            onClick={() => handleItemClick(item)}
          />
        ))}
      </div>
    </nav>
  );
}

// ─── Rail button ──────────────────────────────────────────────────────────────

interface RailButtonProps {
  item: RailItem;
  isActive: boolean;
  badge: number;
  label: string;
  onClick: () => void;
}

function RailButton({ item, isActive, badge, label, onClick }: RailButtonProps) {
  return (
    <button
      id={`nav-rail-${item.id}`}
      className={`nav-rail__item${isActive ? " nav-rail__item--active" : ""}`}
      data-section={item.id}
      aria-label={label}
      aria-current={isActive ? "page" : undefined}
      tabIndex={0}
      title={label}
      onClick={onClick}
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        width: 64,
        height: 52,
        border: "none",
        borderRadius: "var(--syn-radius-md)",
        // Active: accent-soft bg + accent color; inactive: transparent + dim text
        background: isActive ? "var(--syn-accent-soft)" : "transparent",
        color: isActive ? "var(--syn-accent)" : "var(--syn-text-dim)",
        cursor: "pointer",
        padding: "6px 4px 4px",
        gap: 3,
        transition: "background 0.1s ease, color 0.1s ease",
        // Active: persistent accent ring as a visual state indicator (not focus ring).
        // Inactive: no override → :focus-visible from theme.css supplies the keyboard ring (UXA-05).
        outline: isActive ? `1px solid color-mix(in srgb, var(--syn-accent) 20%, transparent 80%)` : undefined,
      }}
    >
      {item.icon}

      <span
        className="nav-rail__label"
        style={{
          fontSize: 10,
          lineHeight: 1.15,
          textAlign: "center",
          // Allow up to 2 lines so multi-word labels ("Ricerca profonda") stay
          // legible instead of being ellipsis-clipped in the narrow rail.
          whiteSpace: "normal",
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: 2,
          overflow: "hidden",
          maxWidth: "100%",
          userSelect: "none",
        }}
      >
        {label}
      </span>

      {badge > 0 && (
        <span
          aria-label={`${badge}`}
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            // Exact count (owner request, v1.2.3) — pill grows with the digits
            // instead of capping at "9+"; 999+ only as an extreme guard.
            minWidth: 14,
            height: 14,
            padding: "0 4px",
            borderRadius: 7,
            background: "var(--syn-accent)",
            color: "#ffffff",
            fontSize: 9,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            lineHeight: 1,
            boxSizing: "border-box",
          }}
        >
          {badge > 999 ? "999+" : badge}
        </span>
      )}
    </button>
  );
}
