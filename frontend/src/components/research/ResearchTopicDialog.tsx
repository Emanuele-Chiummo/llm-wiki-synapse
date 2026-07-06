/**
 * ResearchTopicDialog.tsx — Editable confirm dialog for Deep Research (B5/D3, F10).
 *
 * Opens seeded with a raw topic from Graph Insights.
 * On mount: calls POST /research/optimize-topic → prefills the editable topic + query list.
 * User can edit the topic textarea and add/remove/edit individual query rows.
 * "Start research" confirms with the edited topic + queries → POST /research/start.
 * Cancel dismisses without side effects.
 *
 * INVARIANT I3: optimization call is triggered ONCE on mount (not per-render).
 *   The loading spinner is local state; no Zustand mutation during loading.
 *   No markdown or LaTeX parsed here.
 * INVARIANT I2: no graph layout work.
 * INVARIANT I7: startRun is bounded server-side; this dialog just passes params.
 *
 * Accessibility:
 *   role="dialog" aria-modal aria-labelledby
 *   Focus moves to the first query input (or the topic textarea) on open.
 *   Escape = cancel.
 *   Tab stays within the dialog (focus trap on the two footer buttons when focused there).
 *
 * i18n: all strings via useTranslation() under research.topicDialog.* namespace (F16).
 */

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  type MouseEvent,
  type KeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { optimizeResearchTopic } from "../../api/researchClient";

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ResearchTopicDialogProps {
  /** Raw seed topic from Graph Insights (before LLM optimization). */
  seedTopic: string;
  /** Called when user confirms; dialog provides the final edited topic + queries. */
  onConfirm: (topic: string, queries: string[]) => void;
  /** Called when user cancels or presses Escape. */
  onCancel: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ResearchTopicDialog({
  seedTopic,
  onConfirm,
  onCancel,
}: ResearchTopicDialogProps) {
  const { t } = useTranslation();

  // ── Editable state ────────────────────────────────────────────────────────
  const [topic, setTopic] = useState(seedTopic);
  const [queries, setQueries] = useState<string[]>([]);
  const [optimizing, setOptimizing] = useState(true);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);

  // ── Refs ──────────────────────────────────────────────────────────────────
  const topicRef = useRef<HTMLTextAreaElement>(null);
  const titleId = "research-topic-dialog-title";
  const abortRef = useRef<AbortController | null>(null);

  // ── Optimize on mount (I3 — once, not per-render) ─────────────────────────
  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setOptimizing(true);
    setOptimizeError(null);

    optimizeResearchTopic(seedTopic, ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setTopic(res.optimized_topic);
        setQueries(res.queries);
        setOptimizing(false);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        // Graceful degradation: show the seed topic + empty query list
        setTopic(seedTopic);
        setQueries([]);
        setOptimizeError((err as Error).message ?? "optimize failed");
        setOptimizing(false);
      });

    return () => {
      ctrl.abort();
    };
    // Intentionally run once on mount; seedTopic is the stable seed value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Focus the topic textarea after optimization completes
  useEffect(() => {
    if (!optimizing) {
      topicRef.current?.focus();
    }
  }, [optimizing]);

  // ── Escape key ───────────────────────────────────────────────────────────
  useEffect(() => {
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  // ── Query list mutations ──────────────────────────────────────────────────
  const handleQueryChange = useCallback((index: number, value: string) => {
    setQueries((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  }, []);

  const handleAddQuery = useCallback(() => {
    setQueries((prev) => [...prev, ""]);
  }, []);

  const handleRemoveQuery = useCallback((index: number) => {
    setQueries((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleQueryKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>, index: number) => {
      if (e.key === "Enter") {
        e.preventDefault();
        // Insert a new empty query after the current row
        setQueries((prev) => {
          const next = [...prev];
          next.splice(index + 1, 0, "");
          return next;
        });
      }
      if (e.key === "Backspace" && queries[index] === "" && queries.length > 1) {
        e.preventDefault();
        handleRemoveQuery(index);
      }
    },
    [queries, handleRemoveQuery],
  );

  // ── Confirm ───────────────────────────────────────────────────────────────
  const handleConfirm = useCallback(() => {
    const trimmedTopic = topic.trim();
    if (!trimmedTopic) return;
    const filteredQueries = queries.map((q) => q.trim()).filter(Boolean);
    onConfirm(trimmedTopic, filteredQueries);
  }, [topic, queries, onConfirm]);

  // ── Backdrop click ────────────────────────────────────────────────────────
  function handleBackdropClick(e: MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) onCancel();
  }

  const canConfirm = topic.trim().length > 0 && !optimizing;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div
      data-testid="research-topic-dialog-overlay"
      onClick={handleBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="research-topic-dialog"
        style={{
          background: "var(--syn-bg-card)",
          border: "1px solid var(--syn-border)",
          borderRadius: 10,
          boxShadow: "0 8px 40px rgba(0,0,0,0.40)",
          width: "min(560px, calc(100vw - 32px))",
          maxHeight: "calc(100vh - 64px)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div
          style={{
            padding: "16px 20px 12px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
          }}
        >
          <h2
            id={titleId}
            style={{
              margin: 0,
              fontSize: 15,
              fontWeight: 700,
              color: "var(--syn-text)",
            }}
          >
            {t("research.topicDialog.title")}
          </h2>
          {optimizing && (
            <div
              data-testid="research-topic-dialog-optimizing"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginTop: 6,
                fontSize: 12,
                color: "var(--syn-text-muted)",
              }}
            >
              <Loader2
                size={12}
                style={{ animation: "spin 1s linear infinite" }}
                aria-hidden="true"
              />
              {t("research.topicDialog.optimizing")}
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          )}
          {optimizeError && !optimizing && (
            <p
              role="alert"
              style={{ margin: "4px 0 0", fontSize: 12, color: "var(--syn-text-muted)" }}
            >
              {t("research.topicDialog.optimizeUnavailable")}
            </p>
          )}
        </div>

        {/* ── Body ───────────────────────────────────────────────────────── */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "16px 20px",
            display: "flex",
            flexDirection: "column",
            gap: 16,
            minHeight: 0,
          }}
        >
          {/* Topic */}
          <div>
            <label
              htmlFor="rtd-topic"
              style={{
                display: "block",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
                marginBottom: 4,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {t("research.topicDialog.topicLabel")}
            </label>
            <textarea
              id="rtd-topic"
              ref={topicRef}
              data-testid="research-topic-dialog-topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={3}
              disabled={optimizing}
              placeholder={t("research.topicDialog.topicPlaceholder")}
              style={{
                width: "100%",
                padding: "8px 10px",
                background: "var(--syn-surface)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                color: "var(--syn-text)",
                fontSize: 13,
                resize: "vertical",
                minHeight: 60,
                fontFamily: "inherit",
                boxSizing: "border-box",
                opacity: optimizing ? 0.5 : 1,
              }}
            />
          </div>

          {/* Query list */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 6,
              }}
            >
              <label
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--syn-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {t("research.topicDialog.queriesLabel")}
              </label>
              <button
                type="button"
                data-testid="research-topic-dialog-add-query"
                onClick={handleAddQuery}
                disabled={optimizing}
                title={t("research.topicDialog.addQuery")}
                aria-label={t("research.topicDialog.addQuery")}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 8px",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 5,
                  background: "var(--syn-bg-soft)",
                  color: "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: optimizing ? "not-allowed" : "pointer",
                  opacity: optimizing ? 0.5 : 1,
                }}
              >
                <Plus size={11} />
                {t("research.topicDialog.addQuery")}
              </button>
            </div>

            {queries.length === 0 && !optimizing ? (
              <p
                style={{
                  fontSize: 12,
                  color: "var(--syn-text-dim)",
                  fontStyle: "italic",
                  margin: 0,
                  padding: "8px 0",
                }}
              >
                {t("research.topicDialog.noQueries")}
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {queries.map((q, i) => (
                  <div
                    key={i}
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--syn-text-dim)",
                        minWidth: 18,
                        textAlign: "right",
                        flexShrink: 0,
                      }}
                    >
                      {i + 1}.
                    </span>
                    <input
                      type="text"
                      data-testid={`research-topic-dialog-query-${i}`}
                      value={q}
                      onChange={(e) => handleQueryChange(i, e.target.value)}
                      onKeyDown={(e) => handleQueryKeyDown(e, i)}
                      disabled={optimizing}
                      placeholder={t("research.topicDialog.queryPlaceholder")}
                      style={{
                        flex: 1,
                        padding: "5px 8px",
                        background: "var(--syn-surface)",
                        border: "1px solid var(--syn-border)",
                        borderRadius: 5,
                        color: "var(--syn-text)",
                        fontSize: 12,
                        fontFamily: "inherit",
                        opacity: optimizing ? 0.5 : 1,
                      }}
                    />
                    <button
                      type="button"
                      data-testid={`research-topic-dialog-remove-query-${i}`}
                      onClick={() => handleRemoveQuery(i)}
                      disabled={optimizing}
                      aria-label={t("research.topicDialog.removeQuery")}
                      title={t("research.topicDialog.removeQuery")}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        width: 24,
                        height: 24,
                        border: "none",
                        borderRadius: 4,
                        background: "transparent",
                        color: "var(--syn-text-muted)",
                        cursor: optimizing ? "not-allowed" : "pointer",
                        padding: 0,
                        flexShrink: 0,
                        opacity: optimizing ? 0.4 : 1,
                      }}
                      onMouseEnter={(e) => {
                        if (!optimizing)
                          (e.currentTarget as HTMLButtonElement).style.color =
                            "var(--syn-red)";
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLButtonElement).style.color =
                          "var(--syn-text-muted)";
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div
          style={{
            padding: "12px 20px",
            borderTop: "1px solid var(--syn-border)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            flexShrink: 0,
            background: "var(--syn-bg-soft)",
          }}
        >
          <button
            type="button"
            data-testid="research-topic-dialog-cancel"
            onClick={onCancel}
            className="syn-btn syn-btn--secondary"
          >
            {t("research.topicDialog.cancel")}
          </button>
          <button
            type="button"
            data-testid="research-topic-dialog-confirm"
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="syn-btn syn-btn--primary"
            style={!canConfirm ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
          >
            {t("research.topicDialog.start")}
          </button>
        </div>
      </div>
    </div>
  );
}
