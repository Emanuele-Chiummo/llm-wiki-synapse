/**
 * NavRail.tsx — persistent ~72px left icon rail with persistent text labels.
 *
 * v1.7.0 — Three labelled groups (WS-F slice F2):
 *   MAKE  (nav.group.create)  : Home · Sources · Chat · Convert
 *   SEE   (nav.group.understand): Wiki · Graph · Search · Deep Research
 *   TEND  (nav.group.maintain): Review · Lint · Ingest
 *   [spacer]
 *   Settings · Projects (pinned bottom)
 *
 * Active item visual: --syn-accent-soft background + --syn-accent icon color
 *   + 3px accent left bar (::before in theme.css .nav-rail__item--active).
 *
 * Rail/section decision [F11 / v0.6]:
 *   - "sources" (FolderOpen icon, nav.sources label) → raw-source file browser (SourcesView).
 *   - "ingest"  (Activity icon, nav.ingest label) → ingest run-history / cost ledger (IngestView).
 *
 * INVARIANT I3: reads activeSection (scalar) + setActiveSection only from graphStore.
 * i18n: all labels are translation keys; group labels use nav.group.{create,understand,maintain}.
 *
 * Icons: lucide-react tree-shaken named imports [F1].
 * Icon size: 20px; inactive color var(--syn-text-dim); active var(--syn-accent).
 */

import { useCallback, useRef, type KeyboardEvent, type ReactNode } from "react";
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
  ArrowLeftRight,
} from "lucide-react";
import { selectActiveSection, selectSetActiveSection, useAppStore } from "../../store/appStore";
import type { Section } from "../../store/appStore";
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
 * MAKE group — creation-oriented destinations.
 * Home · Sources · Chat · Convert [F2 / v1.7.0]
 */
const GROUP_CREATE: RailItem[] = [
  { id: "home", icon: <House size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.home" },
  {
    id: "sources",
    icon: <FolderOpen size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.sources",
  },
  { id: "chat", icon: <MessageSquare size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.chat" },
  {
    id: "convert",
    icon: <FileDown size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.convert",
  },
];

/**
 * SEE group — knowledge-exploration destinations.
 * Wiki · Graph · Search · Deep Research [F2 / v1.7.0]
 */
const GROUP_UNDERSTAND: RailItem[] = [
  { id: "pages", icon: <FileText size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.wiki" },
  { id: "graph", icon: <Share2 size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.graph" },
  { id: "search", icon: <Search size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.search" },
  {
    id: "deep-search",
    icon: <Globe size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.deepSearch",
  },
];

/**
 * TEND group — maintenance + curation destinations.
 * Review · Lint · Ingest [F2 / v1.7.0]
 */
const GROUP_MAINTAIN: RailItem[] = [
  {
    id: "review",
    icon: <ListChecks size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.review",
  },
  {
    id: "lint",
    icon: <ClipboardCheck size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.lint",
  },
  { id: "ingest", icon: <Activity size={ICON_SIZE} aria-hidden="true" />, labelKey: "nav.ingest" },
];

const BOTTOM_ITEMS: RailItem[] = [
  {
    id: "settings",
    icon: <Settings size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.settings",
  },
  // v1.5 P2: multi-vault Project Launcher (⇄), very bottom — llm_wiki parity.
  {
    id: "projects",
    icon: <ArrowLeftRight size={ICON_SIZE} aria-hidden="true" />,
    labelKey: "nav.projects",
  },
];

// ─── Logo removed (R11-3) ────────────────────────────────────────────────────
// The Logo() component was removed as part of R11-3 (logo deduplication).
// Branding is handled exclusively by the Header wordmark (Header.tsx).
// The freed top slot is replaced with intentional top padding of 8px applied
// to the nav's paddingTop value (see NavRail below).

// ─── Component ────────────────────────────────────────────────────────────────

export function NavRail() {
  const { t } = useTranslation();
  const activeSection = useAppStore(selectActiveSection);
  const setActiveSection = useAppStore(selectSetActiveSection);
  const runningCount = useIngestRunningCount();
  // Pending review items — fed by the existing 30s /status poll via statusStore (I3).
  const reviewPending = useStatusStore(selectReviewPending);

  // a11y-navrail: hold DOM refs for every rail button so arrow-key navigation
  // can call .focus() on the newly-activated button without a full roving-tabindex
  // rewrite. Keyed by Section id. Map is populated via RailButton's buttonRef prop.
  const buttonRefs = useRef(new Map<string, HTMLButtonElement>());

  const handleItemClick = useCallback(
    (item: RailItem) => {
      setActiveSection(item.id);
    },
    [setActiveSection],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const allItems = [...GROUP_CREATE, ...GROUP_UNDERSTAND, ...GROUP_MAINTAIN, ...BOTTOM_ITEMS];
      const currentIdx = allItems.findIndex((i) => i.id === activeSection);
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        const next = allItems[(currentIdx + 1) % allItems.length];
        if (next) {
          setActiveSection(next.id);
          // DOM focus follows the newly-activated button (WCAG 2.4.3 focus order).
          buttonRefs.current.get(next.id)?.focus();
        }
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = allItems[(currentIdx - 1 + allItems.length) % allItems.length];
        if (prev) {
          setActiveSection(prev.id);
          buttonRefs.current.get(prev.id)?.focus();
        }
      }
    },
    [activeSection, setActiveSection],
  );

  return (
    <nav
      className="nav-rail"
      aria-label={t("nav.ariaLabel")}
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
        minHeight: 0,
      }}
    >
      {/* R11-3: Logo removed — branding lives in Header.tsx only.
          The nav starts 8px below the top edge (paddingTop above) so the
          first nav button is not flush against the rail edge. */}

      <div className="nav-rail__scroll">
        {/* MAKE group — Home · Sources · Chat · Convert */}
        <RailGroup
          labelKey="nav.group.create"
          items={GROUP_CREATE}
          activeSection={activeSection}
          getBadge={() => 0}
          t={t}
          onItemClick={handleItemClick}
          buttonRefs={buttonRefs.current}
        />

        {/* SEE group — Wiki · Graph · Search · Deep Research */}
        <RailGroup
          labelKey="nav.group.understand"
          items={GROUP_UNDERSTAND}
          activeSection={activeSection}
          getBadge={() => 0}
          t={t}
          onItemClick={handleItemClick}
          buttonRefs={buttonRefs.current}
        />

        {/* TEND group — Review · Lint · Ingest */}
        <RailGroup
          labelKey="nav.group.maintain"
          items={GROUP_MAINTAIN}
          activeSection={activeSection}
          getBadge={(id) =>
            id === "ingest" ? runningCount : id === "review" ? (reviewPending ?? 0) : 0
          }
          t={t}
          onItemClick={handleItemClick}
          buttonRefs={buttonRefs.current}
        />
      </div>

      {/* Bottom items (pinned) */}
      <div className="nav-rail__bottom">
        {BOTTOM_ITEMS.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={0}
            label={t(item.labelKey)}
            onClick={() => handleItemClick(item)}
            buttonRef={(el) => {
              if (el) buttonRefs.current.set(item.id, el);
              else buttonRefs.current.delete(item.id);
            }}
          />
        ))}
      </div>
    </nav>
  );
}

// ─── Rail group ───────────────────────────────────────────────────────────────

interface RailGroupProps {
  labelKey: string;
  items: RailItem[];
  activeSection: Section;
  getBadge: (id: string) => number;
  t: (key: string) => string;
  onItemClick: (item: RailItem) => void;
  buttonRefs: Map<string, HTMLButtonElement>;
}

/**
 * RailGroup — renders a labelled group of rail items.
 * Group label: small uppercase mono text (aria-hidden, decorative).
 * Separator: 1px --syn-border line before the label.
 */
function RailGroup({
  labelKey,
  items,
  activeSection,
  getBadge,
  t,
  onItemClick,
  buttonRefs,
}: RailGroupProps) {
  return (
    <>
      {/* 1px separator above each group */}
      <div
        style={{
          width: "calc(100% - 16px)",
          height: 1,
          background: "var(--syn-border)",
          margin: "6px 8px 2px",
        }}
      />
      {/* Group label: uppercase mono, muted, aria-hidden */}
      <span
        aria-hidden="true"
        style={{
          fontFamily: "var(--syn-font-mono)",
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: "0.10em",
          textTransform: "uppercase",
          color: "var(--syn-text-dim)",
          opacity: 0.75,
          padding: "0 4px 2px",
          maxWidth: "100%",
          textAlign: "center",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          userSelect: "none",
          display: "block",
        }}
      >
        {t(labelKey)}
      </span>
      {/* Items */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          alignItems: "center",
          width: "100%",
        }}
      >
        {items.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeSection}
            badge={getBadge(item.id)}
            label={t(item.labelKey)}
            onClick={() => onItemClick(item)}
            buttonRef={(el) => {
              if (el) buttonRefs.set(item.id, el);
              else buttonRefs.delete(item.id);
            }}
          />
        ))}
      </div>
    </>
  );
}

// ─── Rail button ──────────────────────────────────────────────────────────────

interface RailButtonProps {
  item: RailItem;
  isActive: boolean;
  badge: number;
  label: string;
  onClick: () => void;
  /** a11y-navrail: callback ref so NavRail can imperatively focus this button after arrow-key navigation. */
  buttonRef?: (el: HTMLButtonElement | null) => void;
}

function RailButton({ item, isActive, badge, label, onClick, buttonRef }: RailButtonProps) {
  return (
    <button
      ref={buttonRef}
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
        // Active: accent-soft bg + accent color; inactive: transparent + dim text.
        // The 3px left accent bar is rendered via .nav-rail__item--active::before in theme.css.
        background: isActive ? "var(--syn-accent-soft)" : "transparent",
        color: isActive ? "var(--syn-accent)" : "var(--syn-text-dim)",
        cursor: "pointer",
        padding: "6px 4px 4px",
        gap: 3,
        transition: "background 0.1s ease, color 0.1s ease",
        // No inline outline override — :focus-visible from theme.css supplies the keyboard
        // ring for ALL buttons (UXA-05). Never set outline: none here.
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
