/**
 * CommandPalette.tsx — Cmd/Ctrl+K command palette overlay (ADR-0048 §2.2 / T2).
 *
 * Sources:
 *   1. App sections (from NavRail constants, same Section type from graphStore).
 *   2. Wiki pages via fetchAllPages — fetched ONCE per palette open (in-flight guard).
 *   3. Executable actions (v2, FE-UIUX-3): new chat / import / run lint / switch project /
 *      switch theme / regenerate overview. Each action calls the SAME store action or API
 *      client function the dedicated UI already uses for that operation — the palette is
 *      just another entry point, never a second implementation.
 *
 * Filter: case-insensitive substring on title/label. Results CAPPED at 20 (I4 compliance —
 * hard cap instead of virtualizer; ADR-0048 §2.2 explicitly calls this out).
 * Order when unfiltered: actions, then sections, then pages.
 *
 * Keyboard:
 *   Arrow Up/Down → move selection
 *   Enter         → activate selected item (page: selectPage + switch to pages section;
 *                   section: setActiveSection; action: run its handler)
 *   Esc           → close
 *
 * Styling: uses only --syn-* CSS variables. Modal centered, top-third of screen.
 *
 * INVARIANT I3: fetchAllPages called once per open, never per keystroke.
 * INVARIANT I4: results capped at 20 → no virtualizer needed.
 */

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { fetchAllPages } from "../../api/pagesClient";
import type { PageListItem } from "../../api/types";
import {
  selectVaultId,
  selectSetActiveSection,
  selectSelectPage,
  useAppStore,
} from "../../store/appStore";
import type { Section } from "../../store/appStore";
import { startNewConversation } from "../../store/chatActions";
import { useImportScheduleStore } from "../../store/importScheduleStore";
import { useLintStore } from "../../store/lintStore";
import { useSettingsStore, type Theme } from "../../store/settingsStore";
import { triggerRegenerateOverview } from "../../api/opsClient";
import { showToast } from "./Toast";

// ─── Section definitions (mirrors NavRail order) ──────────────────────────────

interface SectionEntry {
  kind: "section";
  id: Section;
  labelKey: string;
}

// ORDER mirrors NavRail v1.7: GROUP_CREATE → GROUP_UNDERSTAND → GROUP_MAINTAIN → bottom.
// All 13 navigable sections are listed so the palette covers Home, Convert and Projects
// (previously missing). [FE-NAV-1]
const ALL_SECTIONS: SectionEntry[] = [
  { kind: "section", id: "home", labelKey: "nav.home" },
  { kind: "section", id: "sources", labelKey: "nav.sources" },
  { kind: "section", id: "chat", labelKey: "nav.chat" },
  { kind: "section", id: "convert", labelKey: "nav.convert" },
  { kind: "section", id: "pages", labelKey: "nav.wiki" },
  { kind: "section", id: "graph", labelKey: "nav.graph" },
  { kind: "section", id: "search", labelKey: "nav.search" },
  { kind: "section", id: "deep-search", labelKey: "nav.deepSearch" },
  { kind: "section", id: "review", labelKey: "nav.review" },
  { kind: "section", id: "lint", labelKey: "nav.lint" },
  { kind: "section", id: "ingest", labelKey: "nav.ingest" },
  { kind: "section", id: "settings", labelKey: "nav.settings" },
  { kind: "section", id: "projects", labelKey: "nav.projects" },
];

// ─── Result union ─────────────────────────────────────────────────────────────

interface PageEntry {
  kind: "page";
  id: string;
  title: string;
  file_path: string;
}

/**
 * ActionEntry — an executable command (v2, FE-UIUX-3). `run` receives the palette's
 * own context (current vaultId + navigation actions) and MUST delegate to the same
 * store action / API client the dedicated UI uses — never re-implement the operation.
 */
interface ActionEntry {
  kind: "action";
  id: string;
  labelKey: string;
  run: (ctx: ActionContext) => void;
}

interface ActionContext {
  vaultId: string;
  setActiveSection: (section: Section) => void;
  t: ReturnType<typeof useTranslation>["t"];
}

type PaletteEntry = SectionEntry | PageEntry | ActionEntry;

const MAX_RESULTS = 20;

// ─── Actions (v2, FE-UIUX-3) ───────────────────────────────────────────────────
//
// Every action calls a function/store that already exists elsewhere in the app for
// that operation — the palette adds no new business logic, only a faster entry point.

const THEME_CYCLE: Theme[] = ["light", "dark", "system"];

const ALL_ACTIONS: ActionEntry[] = [
  {
    kind: "action",
    id: "new-chat",
    labelKey: "palette.action.newChat",
    run: ({ vaultId, setActiveSection, t }) => {
      // Same sequence as ConversationList's "+" button (store/chatActions.ts).
      setActiveSection("chat");
      void startNewConversation(vaultId).catch((err: unknown) => {
        showToast(t("chat.newConvError"), "error");
        console.error("[palette] new chat error", err);
      });
    },
  },
  {
    kind: "action",
    id: "import-ingest",
    labelKey: "palette.action.importIngest",
    run: ({ setActiveSection, t }) => {
      // Same store action as the "Run now" button in ImportScheduleCard.tsx.
      setActiveSection("ingest");
      void useImportScheduleStore
        .getState()
        .runNow()
        .then(() => showToast(t("settings.import.runNowToast"), "success"))
        .catch((err: unknown) => {
          const detail = err instanceof Error ? err.message : t("common.unknown");
          showToast(t("settings.import.runNowError", { detail }), "error");
        });
    },
  },
  {
    kind: "action",
    id: "run-lint",
    labelKey: "palette.action.runLint",
    run: ({ vaultId, setActiveSection }) => {
      // Same store action as LintView's "Run lint" button (selectLintScan).
      setActiveSection("lint");
      void useLintStore.getState().scan(vaultId);
    },
  },
  {
    kind: "action",
    id: "switch-project",
    labelKey: "palette.action.switchProject",
    run: ({ setActiveSection }) => {
      // Same navigation as the "Projects" section entry — a dedicated action entry
      // so "switch project" fuzzy-matches without requiring the exact section name.
      setActiveSection("projects");
    },
  },
  {
    kind: "action",
    id: "switch-theme",
    labelKey: "palette.action.switchTheme",
    run: () => {
      // Same immediate setTheme() the Settings > Interface radios apply on commit;
      // also updates draftTheme so Settings doesn't show a stale/dirty draft next open.
      const { theme, setTheme, setDraftTheme } = useSettingsStore.getState();
      const next = THEME_CYCLE[(THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length] ?? "system";
      setDraftTheme(next);
      setTheme(next);
    },
  },
  {
    kind: "action",
    id: "regenerate-overview",
    labelKey: "palette.action.regenerateOverview",
    run: ({ t }) => {
      // Same POST /ops/overview/regenerate the endpoint exposes (ADR-0078) — bounded,
      // degrade-safe single provider call; never throws for a provider failure.
      void triggerRegenerateOverview()
        .then((res) => {
          if (res.status === "regenerated") {
            showToast(t("palette.action.regenerateDone"), "success");
          } else {
            showToast(t("palette.action.regenerateDegraded"), "error");
          }
        })
        .catch((err: unknown) => {
          const detail = err instanceof Error ? err.message : t("common.unknown");
          showToast(detail, "error");
        });
    },
  },
  {
    kind: "action",
    id: "new-page",
    labelKey: "palette.action.newPage",
    run: ({ setActiveSection }) => {
      // Navigate to the wiki tree, then dispatch the event NavTree listens for
      // to open its new-page modal — same pattern as synapse:openPalette.
      setActiveSection("pages");
      // Defer until next tick so SectionRouter has switched to the pages section
      // (and NavTree is mounted) before the event fires.
      setTimeout(() => {
        window.dispatchEvent(new Event("synapse:newPage"));
      }, 0);
    },
  },
];

// ─── Props ────────────────────────────────────────────────────────────────────

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function CommandPalette({ open, onClose }: CommandPaletteProps): ReactNode {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);
  const setActiveSection = useAppStore(selectSetActiveSection);
  const selectPage = useAppStore(selectSelectPage);

  const [query, setQuery] = useState("");
  const [pages, setPages] = useState<PageListItem[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  // In-flight guard: abort any previous fetch when a new open happens.
  const fetchAbortRef = useRef<AbortController | null>(null);
  // Guard: track the vaultId for which we already fetched (reset when palette closes).
  const fetchedForVaultRef = useRef<string | null>(null);

  // ─── Open / Close lifecycle ───────────────────────────────────────────────

  useEffect(() => {
    if (!open) {
      // Reset state when closed so re-opening is fresh.
      setQuery("");
      setSelectedIdx(0);
      fetchedForVaultRef.current = null;
      fetchAbortRef.current?.abort();
      fetchAbortRef.current = null;
      return;
    }

    // Autofocus input.
    // Use rAF to ensure the dialog is in the DOM first.
    const raf = requestAnimationFrame(() => {
      inputRef.current?.focus();
    });

    // Fetch pages ONCE per open (I3 — not per keystroke).
    if (fetchedForVaultRef.current !== vaultId) {
      fetchedForVaultRef.current = vaultId;
      const ctrl = new AbortController();
      fetchAbortRef.current = ctrl;
      setLoading(true);
      fetchAllPages(vaultId, ctrl.signal)
        .then((res) => {
          setPages(res.items);
        })
        .catch((err: unknown) => {
          if (err instanceof Error && err.name === "AbortError") return;
          // On error keep pages empty — palette still works for sections.
          setPages([]);
        })
        .finally(() => {
          setLoading(false);
        });
    }

    return () => {
      cancelAnimationFrame(raf);
    };
  }, [open, vaultId]);

  // ─── Filtered results (capped at 20) ─────────────────────────────────────

  const results: PaletteEntry[] = (() => {
    const q = query.trim().toLowerCase();

    const matchedActions: ActionEntry[] = ALL_ACTIONS.filter((a) =>
      t(a.labelKey).toLowerCase().includes(q),
    );

    const matchedSections: SectionEntry[] = ALL_SECTIONS.filter((s) =>
      t(s.labelKey).toLowerCase().includes(q),
    );

    const matchedPages: PageEntry[] = pages
      .filter((p) => (p.title ?? "").toLowerCase().includes(q))
      .map((p) => ({
        kind: "page" as const,
        id: p.id,
        title: p.title ?? p.file_path,
        file_path: p.file_path,
      }));

    // Actions first, then sections, then pages — total capped at MAX_RESULTS.
    const combined: PaletteEntry[] = [...matchedActions, ...matchedSections, ...matchedPages];
    return combined.slice(0, MAX_RESULTS);
  })();

  // Keep selectedIdx in bounds when results change.
  const clampedIdx = Math.min(selectedIdx, Math.max(0, results.length - 1));

  // ─── Navigation ──────────────────────────────────────────────────────────

  const openEntry = useCallback(
    (entry: PaletteEntry) => {
      if (entry.kind === "action") {
        entry.run({ vaultId, setActiveSection, t });
      } else if (entry.kind === "section") {
        setActiveSection(entry.id);
      } else {
        // Navigate to page: select it in the store + switch to pages section.
        selectPage(entry.id, "tree");
        setActiveSection("pages");
      }
      onClose();
    },
    [vaultId, setActiveSection, selectPage, onClose, t],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const entry = results[clampedIdx];
        if (entry) openEntry(entry);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    },
    [results, clampedIdx, openEntry, onClose],
  );

  // Reset selection when query changes.
  const handleQueryChange = useCallback((v: string) => {
    setQuery(v);
    setSelectedIdx(0);
  }, []);

  // ─── Backdrop click ───────────────────────────────────────────────────────

  const handleBackdropClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  // ─── Render ───────────────────────────────────────────────────────────────

  if (!open) return null;

  return (
    /* Backdrop */
    <div
      data-testid="command-palette-backdrop"
      onClick={handleBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9000,
        background: "rgba(0, 0, 0, 0.45)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "15vh",
      }}
    >
      {/* Modal */}
      <div
        data-testid="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label={t("palette.placeholder")}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 90vw)",
          background: "var(--syn-surface)",
          border: "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-lg)",
          boxShadow: "var(--syn-shadow-pop)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          maxHeight: "60vh",
        }}
      >
        {/* Search input */}
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--syn-border)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexShrink: 0,
          }}
        >
          <span
            aria-hidden="true"
            style={{ color: "var(--syn-text-dim)", fontSize: 14, flexShrink: 0 }}
          >
            ⌘
          </span>
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-autocomplete="list"
            aria-controls="palette-results"
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t("palette.placeholder")}
            data-testid="palette-input"
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              fontSize: 15,
              color: "var(--syn-text)",
              caretColor: "var(--syn-accent)",
            }}
          />
          {loading && (
            <span
              style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}
              aria-live="polite"
            >
              …
            </span>
          )}
        </div>

        {/* Results list */}
        <div
          id="palette-results"
          role="listbox"
          style={{ overflowY: "auto", flex: 1, minHeight: 0 }}
          data-testid="palette-results"
        >
          {results.length === 0 && !loading && (
            <div
              style={{
                padding: "16px",
                color: "var(--syn-text-dim)",
                fontSize: 13,
                textAlign: "center",
              }}
            >
              {t("palette.noResults")}
            </div>
          )}

          {results.length > 0 && (
            <ResultList results={results} selectedIdx={clampedIdx} t={t} onSelect={openEntry} />
          )}
        </div>

        {/* Hint bar */}
        <div
          style={{
            padding: "6px 16px",
            borderTop: "1px solid var(--syn-border)",
            fontSize: 11,
            color: "var(--syn-text-dim)",
            flexShrink: 0,
          }}
        >
          {t("palette.hint")}
        </div>
      </div>
    </div>
  );
}

// ─── ResultList ───────────────────────────────────────────────────────────────

interface ResultListProps {
  results: PaletteEntry[];
  selectedIdx: number;
  t: (key: string) => string;
  onSelect: (entry: PaletteEntry) => void;
}

function ResultList({ results, selectedIdx, t, onSelect }: ResultListProps): ReactNode {
  // Group actions/sections/pages for visual separation (results are always ordered
  // actions → sections → pages — see the `combined` array above). A group's label is
  // inserted right before its first item, and only when that group has at least one
  // match — independent of which other groups are also present.
  const firstActionIdx = results.findIndex((r) => r.kind === "action");
  const firstSectionIdx = results.findIndex((r) => r.kind === "section");
  const firstPageIdx = results.findIndex((r) => r.kind === "page");

  return (
    <>
      {results.map((entry, idx) => {
        const key =
          entry.kind === "action"
            ? `action-${entry.id}`
            : entry.kind === "section"
              ? `section-${entry.id}`
              : `page-${entry.id}`;
        return (
          <span key={key}>
            {idx === firstActionIdx && <GroupLabel label={t("palette.actions")} />}
            {idx === firstSectionIdx && <GroupLabel label={t("palette.sections")} />}
            {idx === firstPageIdx && <GroupLabel label={t("palette.pages")} />}
            <ResultItem
              entry={entry}
              isSelected={idx === selectedIdx}
              t={t}
              onSelect={onSelect}
              idx={idx}
            />
          </span>
        );
      })}
    </>
  );
}

function GroupLabel({ label }: { label: string }): ReactNode {
  return (
    <div
      style={{
        padding: "6px 16px 4px",
        fontSize: 10,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.07em",
        color: "var(--syn-text-dim)",
        userSelect: "none",
      }}
    >
      {label}
    </div>
  );
}

interface ResultItemProps {
  entry: PaletteEntry;
  isSelected: boolean;
  t: (key: string) => string;
  onSelect: (entry: PaletteEntry) => void;
  idx: number;
}

function ResultItem({ entry, isSelected, t, onSelect, idx }: ResultItemProps): ReactNode {
  const label = entry.kind === "page" ? entry.title : t(entry.labelKey);

  return (
    <div
      role="option"
      aria-selected={isSelected}
      data-palette-idx={idx}
      onClick={() => onSelect(entry)}
      style={{
        padding: "8px 16px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        cursor: "pointer",
        background: isSelected ? "var(--syn-accent-soft)" : "transparent",
        color: isSelected ? "var(--syn-accent)" : "var(--syn-text)",
        fontSize: 13,
        borderLeft: isSelected ? "2px solid var(--syn-accent)" : "2px solid transparent",
        transition: "background 0.08s ease",
      }}
    >
      <span
        aria-hidden="true"
        style={{ fontSize: 12, color: "var(--syn-text-dim)", flexShrink: 0, width: 16 }}
      >
        {entry.kind === "action" ? "▸" : entry.kind === "section" ? "⊞" : "↗"}
      </span>
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      {entry.kind === "page" && (
        <span
          style={{
            fontSize: 11,
            color: "var(--syn-text-dim)",
            flexShrink: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 160,
          }}
        >
          {entry.file_path}
        </span>
      )}
    </div>
  );
}
