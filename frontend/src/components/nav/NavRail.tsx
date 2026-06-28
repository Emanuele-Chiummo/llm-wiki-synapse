/**
 * NavRail.tsx — persistent ~48px left icon rail (ADR-0018 §1 / F1-NAV).
 *
 * Always visible; outside and to the left of the section content area.
 * Items: Pages / Graph / Ingest(+badge) / [Chat disabled] + Settings (pinned bottom).
 * Active item = soft tint highlight (#1f2937).
 * INVARIANT I3: reads activeSection (scalar) + setActiveSection only from graphStore;
 *               ingest badge reads from useIngestRunningCount() (separate hook, not graphStore).
 * Keyboard: role="navigation", arrow key nav, aria-current for active.
 * i18n: all labels are keys (ADR-0018 §6 / AC-F1-NAV-5).
 */

import { useCallback, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useGraphStore } from "../../store/graphStore";
import { selectActiveSection, selectSetActiveSection } from "../../store/graphStore";
import type { Section } from "../../store/graphStore";
import { useIngestRunningCount } from "../../store/ingestStore";

// ─── Inline SVG icons (lucide-style outline, 20×20) ─────────────────────────

function IconFiles() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
      <path d="M14 2v4a2 2 0 0 0 2 2h4"/>
      <path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>
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

function IconDownload() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
  );
}

function IconMessageSquare() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
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

// ─── Rail item types ──────────────────────────────────────────────────────────

interface RailItem {
  id: Section | "chat";
  icon: ReactNode;
  labelKey: string;
  disabled?: boolean;
  disabledLabel?: string;
}

const TOP_ITEMS: RailItem[] = [
  { id: "pages",   icon: <IconFiles />,         labelKey: "nav.pages" },
  { id: "graph",   icon: <IconShare />,         labelKey: "nav.graph" },
  { id: "ingest",  icon: <IconDownload />,      labelKey: "nav.ingest" },
  { id: "chat",    icon: <IconMessageSquare />, labelKey: "nav.chat",     disabled: true, disabledLabel: "nav.chatComingSoon" },
];

const BOTTOM_ITEMS: RailItem[] = [
  { id: "settings", icon: <IconSettings />, labelKey: "nav.settings" },
];

// ─── Component ────────────────────────────────────────────────────────────────

export function NavRail() {
  const { t } = useTranslation();
  const activeSection = useGraphStore(selectActiveSection);
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const runningCount = useIngestRunningCount();

  const handleItemClick = useCallback(
    (item: RailItem) => {
      if (item.disabled || item.id === "chat") return;
      setActiveSection(item.id as Section);
    },
    [setActiveSection],
  );

  // Arrow-key navigation within the rail (WCAG 2.1)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const allItems = [...TOP_ITEMS, ...BOTTOM_ITEMS].filter((i) => !i.disabled);
      const currentIdx = allItems.findIndex((i) => i.id === activeSection);
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        const next = allItems[(currentIdx + 1) % allItems.length];
        if (next) setActiveSection(next.id as Section);
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = allItems[(currentIdx - 1 + allItems.length) % allItems.length];
        if (prev) setActiveSection(prev.id as Section);
      }
    },
    [activeSection, setActiveSection],
  );

  return (
    <nav
      className="nav-rail"
      aria-label={t("nav.pages") + " navigation"}
      data-testid="nav-rail"
      onKeyDown={handleKeyDown}
      style={{
        width: 48,
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
      {/* Top items */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, alignItems: "center", width: "100%" }}>
        {TOP_ITEMS.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={item.id === "ingest" ? runningCount : 0}
            label={t(item.labelKey)}
            disabledLabel={item.disabledLabel ? t(item.disabledLabel) : undefined}
            onClick={() => handleItemClick(item)}
          />
        ))}
      </div>

      {/* Separator */}
      <div
        aria-hidden="true"
        style={{ width: 28, height: 1, background: "#21262d", flexShrink: 0, margin: "4px 0" }}
      />

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

// ─── Rail button sub-component ────────────────────────────────────────────────

interface RailButtonProps {
  item: RailItem;
  isActive: boolean;
  badge: number;
  label: string;
  disabledLabel?: string | undefined;
  onClick: () => void;
}

function RailButton({ item, isActive, badge, label, disabledLabel, onClick }: RailButtonProps) {
  const isDisabled = item.disabled === true;

  return (
    <button
      id={`nav-rail-${item.id}`}
      className={`nav-rail__item${isActive ? " nav-rail__item--active" : ""}${isDisabled ? " nav-rail__item--disabled" : ""}`}
      data-section={item.id}
      aria-label={isDisabled && disabledLabel ? `${label} — ${disabledLabel}` : label}
      aria-current={isActive ? "page" : undefined}
      aria-disabled={isDisabled}
      disabled={isDisabled}
      tabIndex={isActive ? 0 : isDisabled ? -1 : 0}
      title={isDisabled && disabledLabel ? `${label} (${disabledLabel})` : label}
      onClick={onClick}
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        width: 40,
        height: 40,
        border: "none",
        borderRadius: 8,
        background: isActive ? "#1f2937" : "transparent",
        color: isActive ? "#e6edf3" : isDisabled ? "#30363d" : "#6e7681",
        cursor: isDisabled ? "not-allowed" : "pointer",
        padding: 0,
        transition: "background 0.1s ease, color 0.1s ease",
        outline: isActive ? "1px solid #21262d" : "none",
      }}
    >
      {item.icon}

      {/* Running badge on ingest */}
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
