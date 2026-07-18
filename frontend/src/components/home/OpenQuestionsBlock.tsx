/**
 * OpenQuestionsBlock.tsx — lists up to 5 pages of type "query" [F18][v1.5].
 * Fetches ONCE on mount with server-side type=query&limit=5 filter (FE-PERF-2).
 * Renders null when no query pages exist.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircle } from "lucide-react";
import { fetchPages } from "../../api/pagesClient";
import type { PageListItem } from "../../api/types";

interface OpenQuestionsBlockProps {
  vaultId: string;
  onOpenPage: (pageId: string) => void;
}

export function OpenQuestionsBlock({ vaultId, onOpenPage }: OpenQuestionsBlockProps) {
  const { t } = useTranslation();
  const [queryPages, setQueryPages] = useState<PageListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);

    void (async () => {
      try {
        const result = await fetchPages(vaultId, { limit: 5, type: "query" }, ac.signal);
        if (ac.signal.aborted) return;
        // Defensive client-side filter kept as a belt-and-suspenders guard (FE-PERF-2).
        const queries = (result?.items ?? []).filter((p) => p.type === "query").slice(0, 5);
        setQueryPages(queries);
      } catch {
        if (!ac.signal.aborted) setQueryPages([]);
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    })();

    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [vaultId]);

  if (loading && queryPages.length === 0) return null;
  if (!loading && queryPages.length === 0) return null;

  return (
    <section
      aria-label={t("home.openQuestions.ariaLabel")}
      data-testid="home-open-questions"
      style={{
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid var(--syn-border)",
        background: "var(--syn-bg-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <HelpCircle
          size={12}
          aria-hidden="true"
          style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
        />
        <span className="syn-eyebrow">{t("home.openQuestions.title")}</span>
      </div>

      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {queryPages.map((page) => (
          <li key={page.id}>
            <button
              type="button"
              data-testid={`home-open-question-${page.id}`}
              onClick={() => onOpenPage(page.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
                padding: "5px 8px",
                borderRadius: 6,
                border: "none",
                background: "transparent",
                cursor: "pointer",
                textAlign: "left",
                transition: "background 0.1s ease",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background =
                  "var(--syn-surface-hover)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = "transparent";
              }}
            >
              <HelpCircle
                size={10}
                aria-hidden="true"
                style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
              />
              <span
                style={{
                  fontSize: 12,
                  color: "var(--syn-text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {page.title}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
