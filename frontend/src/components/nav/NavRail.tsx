/**
 * NavRail.tsx — persistent ~72px left icon rail with persistent text labels.
 *
 * Order (top → bottom):
 *   Logo (branding, non-nav)
 *   Chat · Wiki · Sources · Graph  (TOP_ITEMS)
 *   [spacer]
 *   Settings  (pinned bottom)
 *
 * CHANGE F1-HARD-M5-PLACEHOLDER: Search, Lint, Review, Deep Search removed from
 * rail entirely (Point B ruling). M5_ITEMS emptied; separator removed.
 * i18n keys nav.search / nav.lint / nav.review / nav.deepSearch / nav.comingSoon
 * are RETAINED in en.json/it.json — do NOT delete them.
 * The Section type in graphStore.ts is UNCHANGED.
 *
 * CHANGE F1-HARD-NAV-LABELS: rail widened to 72px; each button renders the icon
 * SVG with a <span> caption below it (10px, centered, truncate with ellipsis).
 * Button height increased to 52px to accommodate icon + label.
 *
 * INVARIANT I3: reads activeSection (scalar) + setActiveSection only from graphStore.
 * i18n: all labels are translation keys.
 */

import { useCallback, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useGraphStore, selectActiveSection, selectSetActiveSection } from "../../store/graphStore";
import type { Section } from "../../store/graphStore";
import { useIngestRunningCount } from "../../store/ingestStore";

// ─── Icons ────────────────────────────────────────────────────────────────────

function IconMessageSquare() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  );
}

function IconFiles() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
      <path d="M14 2v4a2 2 0 0 0 2 2h4"/>
      <path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>
    </svg>
  );
}

function IconDownload() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
  );
}

function IconShare() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
    </svg>
  );
}

function IconSettings() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
  );
}

function IconSearch() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8"/>
      <path d="m21 21-4.35-4.35"/>
      <path d="M11 8v6m-3-3h6"/>
    </svg>
  );
}

// ─── Rail item types ──────────────────────────────────────────────────────────

interface RailItem {
  id: Section;
  icon: ReactNode;
  labelKey: string;
}

/**
 * Active items for M4. Search/Lint/Review/Deep-Search removed per F1-HARD-M5-PLACEHOLDER.
 * i18n keys for removed items are retained in en.json/it.json for M5.
 */
const TOP_ITEMS: RailItem[] = [
  { id: "chat",   icon: <IconMessageSquare />, labelKey: "nav.chat" },
  { id: "pages",  icon: <IconFiles />,        labelKey: "nav.wiki" },
  { id: "ingest", icon: <IconDownload />,     labelKey: "nav.sources" },
  { id: "graph",  icon: <IconShare />,        labelKey: "nav.graph" },
];

/**
 * M5_ITEMS — Deep Search is active (F10, ADR-0024, EC-M5-HCP-3).
 * Search, Lint, Review remain as placeholders until their M5 phases land.
 * i18n keys nav.search / nav.lint / nav.review / nav.comingSoon retained.
 */
const M5_ITEMS: RailItem[] = [
  { id: "deep-search", icon: <IconSearch />, labelKey: "nav.deepSearch" },
];

const BOTTOM_ITEMS: RailItem[] = [
  { id: "settings", icon: <IconSettings />, labelKey: "nav.settings" },
];

// ─── Logo ─────────────────────────────────────────────────────────────────────

function Logo() {
  return (
    <div
      aria-label="Synapse"
      style={{
        width: 32,
        height: 32,
        borderRadius: 8,
        background: "linear-gradient(135deg, #1f6feb 0%, #58a6ff 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        margin: "4px 0 8px",
      }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="2"/>
        <path d="M12 2a10 10 0 0 1 10 10"/>
        <path d="M12 22a10 10 0 0 1-10-10"/>
        <path d="M2 12h4m12 0h4"/>
        <path d="m4.93 4.93 2.83 2.83m8.48 8.48 2.83 2.83"/>
        <path d="m19.07 4.93-2.83 2.83M7.76 16.24l-2.83 2.83"/>
      </svg>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function NavRail() {
  const { t } = useTranslation();
  const activeSection = useGraphStore(selectActiveSection);
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const runningCount = useIngestRunningCount();

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
        background: "#161b22",
        borderRight: "1px solid #21262d",
        paddingTop: 8,
        paddingBottom: 8,
        gap: 2,
        overflow: "hidden",
      }}
    >
      {/* Branding */}
      <Logo />

      {/* Top items */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center", width: "100%" }}>
        {TOP_ITEMS.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={item.id === "ingest" ? runningCount : 0}
            label={t(item.labelKey)}
            onClick={() => handleItemClick(item)}
          />
        ))}
      </div>

      {/* M5 items (Deep Search active; Search/Lint/Review come in subsequent phases) */}
      {M5_ITEMS.length > 0 && (
        <>
          <div style={{ width: 40, height: 1, background: "#21262d", margin: "4px 0 2px" }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center", width: "100%" }}>
            {M5_ITEMS.map((item) => (
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
        borderRadius: 8,
        background: isActive ? "#1f2937" : "transparent",
        color: isActive ? "#e6edf3" : "#6e7681",
        cursor: "pointer",
        padding: "6px 4px 4px",
        gap: 3,
        transition: "background 0.1s ease, color 0.1s ease",
        outline: isActive ? "1px solid #21262d" : "none",
      }}
    >
      {item.icon}

      <span
        className="nav-rail__label"
        style={{
          fontSize: 10,
          lineHeight: 1.2,
          textAlign: "center",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          maxWidth: "100%",
          userSelect: "none",
        }}
      >
        {label}
      </span>

      {badge > 0 && (
        <span
          aria-label={`${badge} running`}
          style={{
            position: "absolute",
            top: 4,
            right: 4,
            width: 14,
            height: 14,
            borderRadius: "50%",
            background: "#1f6feb",
            color: "#e6edf3",
            fontSize: 9,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            lineHeight: 1,
          }}
        >
          {badge > 9 ? "9+" : badge}
        </span>
      )}
    </button>
  );
}
