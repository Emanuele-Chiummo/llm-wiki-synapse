/**
 * WikiThesisBlock.tsx — fetches overview.md + purpose.md to display the wiki's
 * central thesis and up to 3 key-question chips [F18][v1.5].
 * Fetches ONCE on mount; AbortController cleanup on unmount (I3).
 * Renders nothing (null) when overview.md is missing or thesis can't be parsed.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen } from "lucide-react";
import { fetchPageBySlug, fetchPageContent } from "../../api/pagesClient";

export function WikiThesisBlock() {
  const { t } = useTranslation();
  const [thesis, setThesis] = useState<string | null>(null);
  const [keyQuestions, setKeyQuestions] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    void (async () => {
      // ── Thesis from overview.md ───────────────────────────────────────────
      try {
        const overviewPage = await fetchPageBySlug("overview", ac.signal);
        if (ac.signal.aborted) return;
        const overviewContent = await fetchPageContent(overviewPage.id, ac.signal);
        if (ac.signal.aborted) return;
        const raw = overviewContent.content;
        const match = raw.match(/\*\*(?:Tesi centrale|Central thesis)\*\*:\s*(.+)/);
        let parsed: string | null = match ? (match[1] ?? "").trim() || null : null;
        if (!parsed) {
          for (const line of raw.split("\n")) {
            const trimmed = line.trim();
            if (
              trimmed.length >= 30 &&
              !trimmed.startsWith("#") &&
              !trimmed.startsWith("---") &&
              !/^[a-z_]+:\s/.test(trimmed)
            ) {
              parsed = trimmed.replace(/^\*+|\*+$/g, "").trim();
              break;
            }
          }
        }
        if (parsed && !ac.signal.aborted) setThesis(parsed);
      } catch {
        /* overview.md unavailable — block stays hidden */
      }

      // ── Key questions from purpose.md (best-effort, independent) ─────────
      try {
        const purposePage = await fetchPageBySlug("purpose", ac.signal);
        if (ac.signal.aborted) return;
        const purposeContent = await fetchPageContent(purposePage.id, ac.signal);
        if (ac.signal.aborted) return;
        const raw = purposeContent.content;
        const sectionMatch = raw.match(
          /##\s*(?:Key questions|Domande chiave|Domande)\s*\n([\s\S]*?)(?=\n##|$)/i,
        );
        if (sectionMatch) {
          const bullets = (sectionMatch[1] ?? "").match(/^\s*[-*]\s*(.+)$/gm) ?? [];
          const questions = bullets
            .slice(0, 3)
            .map((b) => b.replace(/^\s*[-*]\s*/, "").trim())
            .filter(Boolean);
          if (questions.length > 0 && !ac.signal.aborted) setKeyQuestions(questions);
        }
      } catch {
        /* purpose.md unavailable — omit key-question chips */
      }
    })();

    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  if (!thesis) return null;

  return (
    <section
      aria-label={t("home.wikiThesis.ariaLabel")}
      data-testid="home-wiki-thesis"
      style={{
        padding: "16px 18px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid color-mix(in srgb, var(--syn-accent) 20%, var(--syn-border) 80%)",
        background: "var(--syn-bg-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <BookOpen
          size={12}
          aria-hidden="true"
          style={{ color: "var(--syn-accent)", flexShrink: 0 }}
        />
        <span className="syn-eyebrow">{t("home.wikiThesis.title")}</span>
      </div>

      <p
        data-testid="home-wiki-thesis-text"
        style={{
          margin: 0,
          fontSize: 14,
          fontWeight: 500,
          color: "var(--syn-text)",
          lineHeight: 1.55,
          fontStyle: "italic",
        }}
      >
        {thesis}
      </p>

      {keyQuestions.length > 0 && (
        <div
          data-testid="home-wiki-thesis-questions"
          style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 2 }}
        >
          <span
            style={{
              fontSize: 10,
              color: "var(--syn-text-dim)",
              alignSelf: "center",
              flexShrink: 0,
            }}
          >
            {t("home.wikiThesis.keyQuestionsLabel")}:
          </span>
          {keyQuestions.map((q, i) => (
            <span
              key={i}
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 10,
                border: "1px solid var(--syn-border)",
                background: "var(--syn-surface-sunken)",
                color: "var(--syn-text-muted)",
              }}
            >
              {q}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
