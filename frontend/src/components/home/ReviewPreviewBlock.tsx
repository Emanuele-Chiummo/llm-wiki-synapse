/**
 * ReviewPreviewBlock.tsx — top 3-5 pending review items with compact actions [F18][F9][v1.5].
 * Fetches ONCE on mount; AbortController cleanup on unmount (I3).
 * Renders null while loading with no items, or when the queue is empty.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ClipboardList } from "lucide-react";
import {
  fetchReviewQueue,
  createReviewItem,
  skipReviewItem,
  deepResearchReviewItem,
} from "../../api/reviewClient";
import type { ReviewItem } from "../../api/types";
import { reviewTypeColor } from "./homeUtils";
import type { Section } from "../../store/appStore";

interface ReviewPreviewBlockProps {
  vaultId: string;
  /** Total pending count from overview KPI — used for the "see all" label. */
  reviewTotal: number;
  setActiveSection: (section: Section) => void;
}

export function ReviewPreviewBlock({
  vaultId,
  reviewTotal,
  setActiveSection,
}: ReviewPreviewBlockProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionStates, setActionStates] = useState<Record<string, "idle" | "loading" | "done">>({});
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);

    void (async () => {
      try {
        const result = await fetchReviewQueue({ vaultId, limit: 5, status: "pending" }, ac.signal);
        if (ac.signal.aborted) return;
        setItems(result?.items ?? []);
      } catch {
        if (!ac.signal.aborted) setItems([]);
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    })();

    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [vaultId]);

  const handleAction = useCallback((itemId: string, action: "create" | "research" | "skip") => {
    setActionStates((prev) => ({ ...prev, [itemId]: "loading" }));
    void (async () => {
      try {
        if (action === "create") await createReviewItem(itemId);
        else if (action === "research") await deepResearchReviewItem(itemId);
        else await skipReviewItem(itemId);
        setActionStates((prev) => ({ ...prev, [itemId]: "done" }));
        setItems((prev) => prev.filter((i) => i.id !== itemId));
      } catch {
        setActionStates((prev) => ({ ...prev, [itemId]: "idle" }));
      }
    })();
  }, []);

  if (loading && items.length === 0) return null;
  if (!loading && items.length === 0) return null;

  return (
    <section
      aria-label={t("home.reviewPreview.ariaLabel")}
      data-testid="home-review-preview"
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
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <ClipboardList
            size={12}
            aria-hidden="true"
            style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
          />
          <span className="syn-eyebrow">{t("home.reviewPreview.title")}</span>
        </div>
        <button
          data-testid="home-review-preview-see-all"
          onClick={() => setActiveSection("review")}
          style={{
            fontSize: 11,
            color: "var(--syn-accent)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            padding: "2px 4px",
            flexShrink: 0,
          }}
        >
          {t("home.reviewPreview.seeAll", { count: reviewTotal })}
        </button>
      </div>

      {/* Item list */}
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {items.map((item) => {
          const state = actionStates[item.id] ?? "idle";
          const title = item.proposed_title || t("home.reviewPreview.noTitle");
          return (
            <li key={item.id} data-testid={`home-review-item-${item.id}`}>
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  padding: "8px 10px",
                  borderRadius: "var(--syn-radius-md)",
                  background: "var(--syn-surface-sunken)",
                }}
              >
                {/* Title + type */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      color: "var(--syn-text)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {title}
                  </div>
                  <div style={{ marginTop: 3 }}>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        fontSize: 10,
                        fontWeight: 600,
                        letterSpacing: "0.02em",
                        padding: "1px 7px",
                        borderRadius: 999,
                        color: reviewTypeColor(item.item_type),
                        background: `color-mix(in srgb, ${reviewTypeColor(item.item_type)} 12%, transparent)`,
                      }}
                    >
                      {item.item_type}
                    </span>
                  </div>
                </div>
                {/* Action buttons */}
                {state !== "done" && (
                  <div style={{ display: "flex", gap: 4, flexShrink: 0, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      data-testid={`home-review-action-create-${item.id}`}
                      disabled={state === "loading"}
                      onClick={() => handleAction(item.id, "create")}
                      style={{
                        fontSize: 10,
                        fontWeight: 600,
                        padding: "2px 9px",
                        borderRadius: 4,
                        border: "1px solid var(--syn-accent)",
                        background:
                          state === "loading" ? "var(--syn-accent-soft)" : "var(--syn-accent)",
                        color: state === "loading" ? "var(--syn-accent)" : "#fff",
                        cursor: state === "loading" ? "default" : "pointer",
                      }}
                    >
                      {state === "loading"
                        ? t("home.reviewPreview.creating")
                        : t("home.reviewPreview.create")}
                    </button>
                    <button
                      type="button"
                      data-testid={`home-review-action-research-${item.id}`}
                      disabled={state === "loading"}
                      onClick={() => handleAction(item.id, "research")}
                      style={{
                        fontSize: 10,
                        padding: "2px 7px",
                        borderRadius: 4,
                        border: "1px solid var(--syn-border)",
                        background: "transparent",
                        color: "var(--syn-text-muted)",
                        cursor: state === "loading" ? "default" : "pointer",
                      }}
                    >
                      {t("home.reviewPreview.deepResearch")}
                    </button>
                    <button
                      type="button"
                      data-testid={`home-review-action-skip-${item.id}`}
                      disabled={state === "loading"}
                      onClick={() => handleAction(item.id, "skip")}
                      style={{
                        fontSize: 10,
                        padding: "2px 7px",
                        borderRadius: 4,
                        border: "1px solid var(--syn-border)",
                        background: "transparent",
                        color: "var(--syn-text-dim)",
                        cursor: state === "loading" ? "default" : "pointer",
                      }}
                    >
                      {t("home.reviewPreview.skip")}
                    </button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
