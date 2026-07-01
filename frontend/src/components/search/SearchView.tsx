/**
 * SearchView.tsx — dedicated Search section (F5 / llm_wiki parity).
 *
 * Layout:
 *   [ Search input with Lucide Search icon ]
 *   [ Result list — title chip + snippet + score ]
 *
 * Behaviour:
 *   - Debounced 300ms; minimum 2 characters before firing (I3).
 *   - Calls GET /search via searchWiki() from searchClient.ts.
 *   - Each result row shows: title, type badge (--syn-type-* chip), snippet excerpt,
 *     optional score. Clicking a result sets activeSection="pages" and selectPage(id).
 *   - Empty / loading / no-results / error states rendered inline.
 *
 * INVARIANT I3: single fetch per debounced query; AbortController on each call;
 *   no per-token work; Zustand selectors + shallow equality where store is used.
 * INVARIANT I4: unaffected (no virtualised list here — result count is bounded by k≤50).
 * INVARIANT I2: never imports graph layout algorithms.
 *
 * i18n: all display strings via useTranslation() (F16).
 * Light design: --syn-* tokens; Lucide named imports (F1).
 */

import { useState, useEffect, useRef, useCallback, type ChangeEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Search, X } from "lucide-react";
import { searchWiki } from "../../api/searchClient";
import type { SearchResultItem } from "../../api/searchClient";
import { useGraphStore, selectVaultId, selectSelectPage, selectSetActiveSection } from "../../store/graphStore";

// ─── Constants ────────────────────────────────────────────────────────────────

const DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 2;

// ─── Type badge ───────────────────────────────────────────────────────────────

/** Render a small pill badge using the --syn-type-* chip convention. */
function TypeBadge({ type }: { type: string }) {
  // Map page-type slug → CSS var. Unknown types fall back to the muted palette.
  const cssVar = `var(--syn-type-${type.toLowerCase()}, var(--syn-text-dim))`;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "1px 6px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.03em",
        textTransform: "uppercase",
        color: cssVar,
        background: `color-mix(in srgb, ${cssVar} 12%, transparent 88%)`,
        border: `1px solid color-mix(in srgb, ${cssVar} 20%, transparent 80%)`,
        flexShrink: 0,
      }}
    >
      {type}
    </span>
  );
}

// ─── Result row ───────────────────────────────────────────────────────────────

interface ResultRowProps {
  item: SearchResultItem;
  onSelect: (id: string) => void;
}

function ResultRow({ item, onSelect }: ResultRowProps) {
  const { t } = useTranslation();

  // Derive a readable "type" from the phase field for display.
  // The SearchResultItem shape has no `type` field (it's a citation projection);
  // we use `phase` to indicate vector vs expansion provenance.
  const phaseLabel = item.phase === "vector" ? t("search.phaseVector") : t("search.phaseExpansion");
  const scoreDisplay = (item.score * 100).toFixed(0);

  const handleClick = useCallback(() => {
    onSelect(item.id);
  }, [item.id, onSelect]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(item.id);
      }
    },
    [item.id, onSelect],
  );

  return (
    <button
      type="button"
      data-testid="search-result-row"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "10px 16px",
        border: "none",
        borderBottom: "1px solid var(--syn-border)",
        background: "transparent",
        cursor: "pointer",
        textAlign: "left",
        width: "100%",
        transition: "background 0.1s ease",
      }}
      className="search-result-row"
    >
      {/* Title row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {item.title}
        </span>

        <TypeBadge type={phaseLabel} />

        <span
          aria-label={`${t("search.score")}: ${scoreDisplay}%`}
          style={{
            fontSize: 10,
            color: "var(--syn-text-dim)",
            flexShrink: 0,
          }}
        >
          {scoreDisplay}%
        </span>
      </div>

      {/* Slug as context hint (no snippet field on SearchResultItem) */}
      <span
        style={{
          fontSize: 12,
          color: "var(--syn-text-dim)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontFamily: "monospace",
        }}
      >
        {item.slug}
      </span>
    </button>
  );
}

// ─── SearchView ───────────────────────────────────────────────────────────────

export function SearchView() {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);
  const selectPage = useGraphStore(selectSelectPage);
  const setActiveSection = useGraphStore(selectSetActiveSection);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const debounceRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Debounced search effect
  useEffect(() => {
    // Clear pending debounce
    if (debounceRef.current !== null) {
      globalThis.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }

    const q = query.trim();

    if (q.length < MIN_QUERY_LENGTH) {
      // Abort any in-flight request
      abortRef.current?.abort();
      abortRef.current = null;
      if (q.length === 0) {
        // Reset when input is cleared
        setResults([]);
        setHasSearched(false);
        setError(null);
        setLoading(false);
      }
      return;
    }

    // Schedule the fetch
    debounceRef.current = globalThis.setTimeout(() => {
      // Abort previous request
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      setLoading(true);
      setError(null);

      void (async () => {
        try {
          const data = await searchWiki(q, {
            vault_id: vaultId,
            signal: ctrl.signal,
          });

          if (!ctrl.signal.aborted) {
            setResults(data.results);
            setHasSearched(true);
          }
        } catch (err) {
          if (!ctrl.signal.aborted) {
            setError(err instanceof Error ? err.message : t("common.unknown"));
            setResults([]);
            setHasSearched(true);
          }
        } finally {
          if (!ctrl.signal.aborted) {
            setLoading(false);
          }
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current !== null) {
        globalThis.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [query, vaultId, t]);

  // Cleanup abort controller on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleInputChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
  }, []);

  const handleClear = useCallback(() => {
    setQuery("");
    setResults([]);
    setHasSearched(false);
    setError(null);
    setLoading(false);
    abortRef.current?.abort();
    abortRef.current = null;
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      handleClear();
    }
  }, [handleClear]);

  const handleSelectResult = useCallback(
    (pageId: string) => {
      // Navigate to the wiki/pages section and select the page in the tree
      selectPage(pageId, "tree");
      setActiveSection("pages");
    },
    [selectPage, setActiveSection],
  );

  const hasQuery = query.trim().length >= MIN_QUERY_LENGTH;

  return (
    <div
      data-testid="search-view"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        background: "var(--syn-bg)",
      }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
        }}
      >
        <h2
          style={{
            margin: "0 0 10px",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
          }}
        >
          {t("search.title")}
        </h2>

        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            background: "var(--syn-bg)",
            border: "1px solid var(--syn-border)",
            borderRadius: 6,
          }}
        >
          <Search
            size={14}
            color="var(--syn-text-dim)"
            aria-hidden="true"
            style={{ flexShrink: 0 }}
          />
          <input
            ref={inputRef}
            type="search"
            data-testid="search-input"
            value={query}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={t("search.placeholder")}
            aria-label={t("search.inputLabel")}
            autoComplete="off"
            autoFocus
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              color: "var(--syn-text)",
              fontSize: 13,
              minWidth: 0,
            }}
          />
          {query.length > 0 && (
            <button
              type="button"
              aria-label={t("search.clear")}
              onClick={handleClear}
              style={{
                display: "flex",
                alignItems: "center",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: 2,
                color: "var(--syn-text-dim)",
                flexShrink: 0,
              }}
            >
              <X size={12} aria-hidden="true" />
            </button>
          )}
        </div>

        {/* Hint: min chars */}
        {query.length > 0 && query.trim().length < MIN_QUERY_LENGTH && (
          <p
            style={{
              margin: "4px 0 0",
              fontSize: 11,
              color: "var(--syn-text-dim)",
            }}
          >
            {t("search.minCharsHint")}
          </p>
        )}
      </div>

      {/* ── Results area ───────────────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          minHeight: 0,
        }}
      >
        {/* Loading */}
        {loading && (
          <div
            data-testid="search-loading"
            style={{
              padding: "24px 16px",
              textAlign: "center",
              color: "var(--syn-text-dim)",
              fontSize: 12,
            }}
          >
            {t("common.loading")}
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div
            role="alert"
            data-testid="search-error"
            style={{
              padding: "12px 16px",
              fontSize: 12,
              color: "var(--syn-red)",
              background: "color-mix(in srgb, var(--syn-red) 6%, white 94%)",
              borderBottom: "1px solid var(--syn-border)",
            }}
          >
            {t("search.error")}: {error}
          </div>
        )}

        {/* No results */}
        {!loading && !error && hasSearched && hasQuery && results.length === 0 && (
          <div
            data-testid="search-no-results"
            style={{
              padding: "32px 16px",
              textAlign: "center",
              color: "var(--syn-text-dim)",
              fontSize: 13,
            }}
          >
            <div style={{ marginBottom: 4, fontWeight: 500, color: "var(--syn-text-muted)" }}>
              {t("search.noResults")}
            </div>
            <div style={{ fontSize: 12 }}>
              {t("search.noResultsHint")}
            </div>
          </div>
        )}

        {/* Results list */}
        {!loading && !error && results.length > 0 && (
          <div
            data-testid="search-results"
            role="list"
            aria-label={t("search.resultsLabel")}
          >
            {results.map((item) => (
              <div key={item.id} role="listitem">
                <ResultRow item={item} onSelect={handleSelectResult} />
              </div>
            ))}
          </div>
        )}

        {/* Empty initial state (no query yet) */}
        {!loading && !hasSearched && query.trim().length < MIN_QUERY_LENGTH && (
          <div
            data-testid="search-empty-state"
            style={{
              padding: "32px 16px",
              textAlign: "center",
              color: "var(--syn-text-dim)",
              fontSize: 12,
            }}
          >
            <Search
              size={28}
              color="var(--syn-border)"
              aria-hidden="true"
              style={{ marginBottom: 12, display: "block", margin: "0 auto 12px" }}
            />
            <div style={{ fontWeight: 500, fontSize: 13, color: "var(--syn-text-muted)", marginBottom: 4 }}>
              {t("search.emptyTitle")}
            </div>
            <div>{t("search.emptyBody")}</div>
          </div>
        )}
      </div>
    </div>
  );
}
