/**
 * SectionChangelog.tsx — parses CHANGELOG.md into expandable per-version cards.
 *
 * Sourcing: fetches /CHANGELOG.md (static asset; copied from repo root into
 * frontend/public/ by scripts/copy-changelog.mjs via predev/prebuild npm hooks).
 * Works identically in the Vite dev server (public/ served verbatim) and in
 * the nginx production build (vite build embeds public/ into dist/).
 *
 * Parsing: `parseChangelog()` splits on `## [version]` (Keep a Changelog format)
 * producing one entry per version. The file is already newest-first, so no sort
 * is required. Exported for direct unit testing.
 *
 * Display: renders VISIBLE_MAX (10) most recent cards — Unreleased first if
 * present. A footer links to GitHub Releases for the full history.
 *
 * Rendering: one accordion card per displayed entry.
 *   - Collapsed: version badge + date + optional codename + chevron.
 *   - Expanded:  body rendered once via renderMarkdown() (I3/G3: called only
 *                when the card is opened, never per-token or on every render).
 *   - First card auto-expanded on load.
 *   - Empty body shows a localised placeholder rather than crashing.
 *
 * Graceful degradation: "unavailable" note + refresh button on error.
 */
import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader } from "../ui";
import { Button } from "../../ui/Button";
import { renderMarkdown } from "../../chat/renderMarkdown";

// ─── Constants ────────────────────────────────────────────────────────────────

/** Maximum number of version cards to display (newest first). */
export const VISIBLE_MAX = 10;

/** GitHub Releases URL for the "full history" footer link. */
const GITHUB_RELEASES_URL = "https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ChangelogEntry {
  /** Raw version string — "Unreleased" or semver e.g. "1.3.16". */
  version: string;
  /** ISO date from the `## [x.y.z] — YYYY-MM-DD` line, or null. */
  date: string | null;
  /** Any text after the date on the header line (codename / title), or null. */
  codename: string | null;
  /** Raw markdown body (### Added / Changed / Fixed …) for this version. */
  body: string;
}

// ─── Parser ───────────────────────────────────────────────────────────────────

/**
 * Parse a Keep-a-Changelog formatted text into an array of ChangelogEntry.
 * Preserves the order present in the file (newest / Unreleased first).
 * Defensively handles: missing dates, empty bodies, em/en/hyphen dashes.
 * Exported for direct unit testing without rendering the component.
 */
export function parseChangelog(text: string): ChangelogEntry[] {
  // Match `## [version]` lines anywhere in the document
  const sectionRe = /^## \[([^\]]+)\]([^\n]*)/gm;

  // Collect all match positions first (so we know each section's extent)
  const positions: Array<{ index: number; version: string; extra: string }> = [];
  let m: RegExpExecArray | null;
  while ((m = sectionRe.exec(text)) !== null) {
    positions.push({ index: m.index, version: m[1] ?? "", extra: m[2] ?? "" });
  }

  return positions.map(({ version, extra, index }, i) => {
    // Body starts on the line AFTER the header
    const newlineIdx = text.indexOf("\n", index);
    const bodyStart = newlineIdx >= 0 ? newlineIdx + 1 : text.length;
    // Body ends just before the next `## [...]` header (or EOF)
    const nextPos = positions[i + 1];
    const bodyEnd = nextPos !== undefined ? nextPos.index : text.length;
    const body = text.slice(bodyStart, bodyEnd).trim();

    // Parse date: look for em-dash / en-dash / hyphen followed by YYYY-MM-DD
    const dateMatch = extra.match(/[—–-]\s*(\d{4}-\d{2}-\d{2})/);
    const date = dateMatch ? (dateMatch[1] ?? null) : null;

    // Codename: text remaining on the header line after the date
    let codename: string | null = null;
    if (date && dateMatch) {
      const dateEnd = extra.indexOf(date) + date.length;
      const rest = extra.slice(dateEnd).trim();
      codename = rest.length > 0 ? rest : null;
    }

    return { version, date, codename, body };
  });
}

// ─── Component ────────────────────────────────────────────────────────────────

type FetchState =
  | { status: "loading" }
  | { status: "ok"; entries: ChangelogEntry[]; total: number }
  | { status: "error" };

export function SectionChangelog() {
  const { t } = useTranslation();
  const [fetchState, setFetchState] = useState<FetchState>({ status: "loading" });
  /** Set of version strings whose cards are currently open. */
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    setFetchState({ status: "loading" });
    fetch("/CHANGELOG.md")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        const all = parseChangelog(text);
        // Keep only the VISIBLE_MAX most recent entries (file is newest-first).
        const entries = all.slice(0, VISIBLE_MAX);
        // Auto-expand the first entry (Unreleased or most recent version)
        const firstVersion = entries[0]?.version ?? "";
        setExpanded(new Set(firstVersion ? [firstVersion] : []));
        setFetchState({ status: "ok", entries, total: all.length });
      })
      .catch(() => {
        setFetchState({ status: "error" });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function toggleEntry(version: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(version)) {
        next.delete(version);
      } else {
        next.add(version);
      }
      return next;
    });
  }

  return (
    <div data-testid="section-changelog">
      <SectionHeader title={t("settings.changelog.title")} desc={t("settings.changelog.desc")} />

      {/* ── Loading ── */}
      {fetchState.status === "loading" && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
          {t("settings.changelog.loading")}
        </p>
      )}

      {/* ── Error ── */}
      {fetchState.status === "error" && (
        <div>
          <p style={{ fontSize: 12, color: "var(--syn-text-dim)", marginBottom: 12 }}>
            {t("settings.changelog.unavailable")}
          </p>
          <Button variant="ghost" onClick={load} data-testid="changelog-refresh-btn">
            {t("settings.changelog.refresh")}
          </Button>
        </div>
      )}

      {/* ── Accordion list ── */}
      {fetchState.status === "ok" && (
        <div>
          {/* Version count */}
          <p
            style={{ margin: "0 0 10px", fontSize: 11, color: "var(--syn-text-dim)" }}
            data-testid="changelog-count"
          >
            {fetchState.entries.length} {t("settings.changelog.versions")}
          </p>

          <div
            role="list"
            aria-label={t("settings.changelog.title")}
            style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}
          >
            {fetchState.entries.map((entry) => {
              const isOpen = expanded.has(entry.version);
              const isUnreleased = entry.version.toLowerCase() === "unreleased";
              // renderMarkdown called once when card opens — I3 / G3 satisfied.
              const bodyHtml = isOpen ? renderMarkdown(entry.body) : "";

              return (
                <div
                  key={entry.version}
                  role="listitem"
                  data-testid={`changelog-entry-${entry.version}`}
                  style={{
                    border: "1px solid var(--syn-border)",
                    borderRadius: 8,
                    overflow: "hidden",
                    background: "var(--syn-bg-soft)",
                  }}
                >
                  {/* ── Card header (always visible) ── */}
                  <button
                    onClick={() => toggleEntry(entry.version)}
                    aria-expanded={isOpen}
                    data-testid={`changelog-toggle-${entry.version}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      width: "100%",
                      padding: "10px 14px",
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      textAlign: "left",
                      color: "var(--syn-text)",
                    }}
                  >
                    {/* Version badge */}
                    <span
                      data-testid={`changelog-version-${entry.version}`}
                      style={{
                        fontSize: 11,
                        fontWeight: 700,
                        fontFamily: "var(--syn-font-mono)",
                        letterSpacing: "0.03em",
                        padding: "2px 7px",
                        borderRadius: 4,
                        flexShrink: 0,
                        background: isUnreleased ? "var(--syn-accent-soft)" : "var(--syn-surface)",
                        color: isUnreleased ? "var(--syn-accent)" : "var(--syn-text-muted)",
                        border: `1px solid ${isUnreleased ? "var(--syn-accent)" : "var(--syn-border)"}`,
                      }}
                    >
                      {isUnreleased ? t("settings.changelog.unreleased") : `v${entry.version}`}
                    </span>

                    {/* Date */}
                    {entry.date && (
                      <span
                        data-testid={`changelog-date-${entry.version}`}
                        style={{ fontSize: 12, color: "var(--syn-text-dim)", flexShrink: 0 }}
                      >
                        {entry.date}
                      </span>
                    )}

                    {/* Codename / title */}
                    {entry.codename && (
                      <span
                        style={{
                          fontSize: 12,
                          color: "var(--syn-text-muted)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          minWidth: 0,
                        }}
                      >
                        {entry.codename}
                      </span>
                    )}

                    {/* Spacer + chevron */}
                    <span
                      aria-hidden="true"
                      style={{ marginLeft: "auto", flexShrink: 0, opacity: 0.45, fontSize: 11 }}
                    >
                      {isOpen ? "▲" : "▼"}
                    </span>
                  </button>

                  {/* ── Card body (shown when expanded) ── */}
                  {isOpen && (
                    <div
                      data-testid={`changelog-body-${entry.version}`}
                      style={{
                        padding: "12px 16px 16px",
                        borderTop: "1px solid var(--syn-border)",
                      }}
                    >
                      {entry.body ? (
                        <div
                          className="synapse-markdown"
                          style={{ fontSize: 13, lineHeight: 1.7 }}
                          // Safe: renderMarkdown pipes through DOMPurify.
                          dangerouslySetInnerHTML={{ __html: bodyHtml }}
                        />
                      ) : (
                        <p style={{ margin: 0, fontSize: 12, color: "var(--syn-text-dim)" }}>
                          {t("settings.changelog.emptyBody")}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* ── Footer: full history link ── */}
          <p
            style={{ margin: "0 0 14px", fontSize: 11, color: "var(--syn-text-dim)" }}
            data-testid="changelog-footer"
          >
            {t("settings.changelog.footer")}{" "}
            <a
              href={GITHUB_RELEASES_URL}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--syn-accent)", textDecoration: "none" }}
            >
              {t("settings.changelog.footerLink")} ↗
            </a>
          </p>

          {/* Refresh */}
          <Button variant="ghost" onClick={load} data-testid="changelog-refresh-btn">
            {t("settings.changelog.refresh")}
          </Button>
        </div>
      )}
    </div>
  );
}
