/**
 * MessageList.tsx — virtualized message list (ADR-0019 §3 / I3 / I4 / AC-F6-6).
 *
 * INVARIANT I4 / AC-F6-6: ≤30 mounted DOM rows regardless of message count.
 *   TanStack Virtual (useVirtualizer) is used exclusively — no windowing library swap.
 *
 * INVARIANT I3 / AC-G3-4:
 *   - This component subscribes to `messages` (settled, immutable array) via useMessages().
 *   - It does NOT subscribe to streamingContent or streamingThink.
 *   - Adding a new token to the streaming buffer does NOT re-render this component.
 *   - Only StreamingMessage reads the streaming buffers.
 *
 * Layout:
 *   - Settled messages: role-labelled rows, rendered by MarkdownView.
 *   - The in-flight assistant turn: rendered by StreamingMessage (appended below settled list).
 *   - Auto-scroll to bottom on new message / streaming append (scroll-to-last-row).
 *   - Save-to-wiki button on assistant messages (F6 AC-F6-5): enabled (M5).
 *     On click: POST /ingest/from-text → shows success (page_title + wikilink) or error.
 *   - Cost display per turn (I7): 4dp from total_cost_usd on the done event.
 *   - Regenerate button on the last assistant message (AC-F6-4).
 */

import {
  useRef,
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  memo,
  type ReactNode,
} from "react";
import synapseLogo from "../../assets/synapse-logo.svg";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import {
  useChatStore,
  useMessages,
  selectIsStreaming,
  selectLastUsage,
  selectActiveConversationId,
} from "../../store/chatStore";
import type { ChatMessage, WebCitationRef } from "../../store/chatStore";
import {
  useGraphStore,
  selectVaultId,
  selectSelectPage,
  selectSetActiveSection,
} from "../../store/graphStore";
import { saveToWikiV2 } from "../../api/chatClient";
import { fetchPageBySlug } from "../../api/pagesClient";
import { showToast } from "../common/Toast";
import { MarkdownView } from "./MarkdownView";
import { StreamingMessage } from "./StreamingMessage";

interface MessageListProps {
  onRegenerate?: (() => void) | undefined;
  /** Called when an example-question chip is clicked (uses same send path as MessageInput). */
  onSend?: ((text: string) => void) | undefined;
}

export function MessageList({ onRegenerate, onSend }: MessageListProps): ReactNode {
  const { t } = useTranslation();
  // Settled messages only — NOT subscribing to streaming buffers (AC-G3-4)
  const messages = useMessages();
  const isStreaming = useChatStore(selectIsStreaming);
  const lastUsage = useChatStore(selectLastUsage);
  const activeConversationId = useChatStore(selectActiveConversationId);
  const vaultId = useGraphStore(selectVaultId);
  // R8-6: navigation actions for citation click-through (AC-R8-6-2)
  const selectPage = useGraphStore(selectSelectPage);
  const setActiveSection = useGraphStore(selectSetActiveSection);

  // R8-6: stable citation navigation handler — opens the cited page in the preview panel.
  // Slug from the citation → match against page nodes, then navigate (AC-R8-6-2).
  // Called at most once per click (not per token — I3 compliant).
  const handleCitationClick = useCallback(
    (slug: string, pageId?: string) => {
      // v1.3.3: the selection key is the page UUID, but citations expose a
      // DERIVED slug (slugify(title)) — feeding it into selectPage made
      // /pages/{id}/content 422. Navigate by id when the citation carries it;
      // otherwise resolve slug → page via GET /pages/by-slug (legacy messages).
      if (pageId) {
        selectPage(pageId, "tree");
        setActiveSection("pages");
        return;
      }
      void (async () => {
        try {
          const page = await fetchPageBySlug(slug);
          selectPage(page.id, "tree");
          setActiveSection("pages");
        } catch {
          showToast(t("chat.citationNotFound"), "error");
        }
      })();
    },
    [selectPage, setActiveSection, t],
  );

  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    // AC-R11-4-BUG3: estimateSize returns a non-zero default (120px) so that
    // getTotalSize() > 0 on initial render with a non-empty message list.
    estimateSize: () => 120,
    overscan: 5,
  });

  // AC-R11-4-BUG3: remeasure on mount so the virtualizer reflects the
  // container's actual height as soon as it is first painted. The built-in
  // ResizeObserver inside @tanstack/react-virtual fires asynchronously; this
  // synchronous layout effect ensures the first render is correct even when
  // the browser/JSDOM hasn't dispatched a resize event yet.
  useLayoutEffect(() => {
    const el = parentRef.current;
    if (!el) return;

    virtualizer.measure();

    if (typeof ResizeObserver === "undefined") return;

    let measured = false;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      const h = entry?.contentRect.height ?? el.clientHeight;
      if (h > 0 && !measured) {
        measured = true;
        virtualizer.measure();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll to bottom when messages change or streaming starts
  useEffect(() => {
    if (!parentRef.current) return;
    const el = parentRef.current;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, isStreaming]);

  return (
    <div
      ref={parentRef}
      style={{
        flex: 1,
        overflowY: "auto",
        overflowX: "hidden",
        padding: "0 0 8px 0",
      }}
      data-testid="message-list"
    >
      {messages.length === 0 && !isStreaming && <ChatEmptyState onSend={onSend} t={t} />}

      {/* Virtualized settled messages */}
      {messages.length > 0 && (
        <div
          style={{
            height: virtualizer.getTotalSize(),
            width: "100%",
            position: "relative",
          }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const msg = messages[virtualItem.index];
            if (!msg) return null;
            const isLast = virtualItem.index === messages.length - 1;
            return (
              <div
                key={virtualItem.key}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualItem.start}px)`,
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--syn-border)",
                }}
              >
                <MessageRow
                  msg={msg}
                  msgIndex={virtualItem.index}
                  isLast={isLast}
                  onRegenerate={isLast && msg.role === "assistant" ? onRegenerate : undefined}
                  showCost={isLast && msg.role === "assistant" && lastUsage !== null}
                  costUsd={
                    isLast ? (lastUsage?.totalCostUsd ?? msg.total_cost_usd) : msg.total_cost_usd
                  }
                  vaultId={vaultId}
                  conversationId={activeConversationId}
                  onCitationClick={handleCitationClick}
                  t={t}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* In-flight streaming turn — NOT inside the virtualizer (AC-G3-4) */}
      {isStreaming && (
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--syn-border)",
          }}
        >
          <MessageRoleLabel role="assistant" t={t} />
          <StreamingMessage />
        </div>
      )}
    </div>
  );
}

// ─── Save-to-wiki state ───────────────────────────────────────────────────────

type SaveState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; pageId: string; filePath: string }
  | { kind: "error"; message: string };

/**
 * Derive a page title from the user message that prompted this assistant reply.
 * Walks backward from `msgIndex` in `allMessages` to find the nearest "user" role
 * message. Falls back to the first line of the assistant content, then to a generic
 * fallback string. Trims to 80 chars max.
 */
function deriveSaveTitle(msg: ChatMessage, msgIndex: number, allMessages: ChatMessage[]): string {
  // Search backwards for a user message preceding this assistant message
  for (let i = msgIndex - 1; i >= 0; i--) {
    const candidate = allMessages[i];
    if (candidate?.role === "user") {
      const trimmed = candidate.content.trim().replace(/\s+/g, " ");
      return trimmed.length > 80 ? trimmed.slice(0, 80) : trimmed;
    }
  }
  // Fallback: first line of the assistant content (strip <think> preamble)
  const firstLine =
    msg.content
      .replace(/<think>[\s\S]*?<\/think>/gi, "")
      .trim()
      .split("\n")[0]
      ?.trim() ?? "";
  return firstLine.length > 80 ? firstLine.slice(0, 80) : firstLine || "Saved answer";
}

// ─── MessageRow — memoized per settled message ────────────────────────────────

interface MessageRowProps {
  msg: ChatMessage;
  /** Index of this message in the settled messages array — used to walk back to user question. */
  msgIndex: number;
  isLast: boolean;
  onRegenerate?: (() => void) | undefined;
  showCost: boolean;
  costUsd: number;
  vaultId: string | null | undefined;
  conversationId: string | null | undefined;
  /**
   * R8-6: citation click-through handler (AC-R8-6-2).
   * Receives the page slug from the citation; navigates to pages section + selects page.
   * Always provided from MessageList — never undefined in this context.
   */
  onCitationClick: (slug: string, pageId?: string) => void;
  t: ReturnType<typeof useTranslation>["t"];
}

const MessageRow = memo(function MessageRow({
  msg,
  msgIndex,
  isLast,
  onRegenerate,
  showCost,
  costUsd,
  vaultId,
  conversationId,
  onCitationClick,
  t,
}: MessageRowProps): ReactNode {
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const handleSaveToWiki = useCallback(async () => {
    if (saveState.kind === "loading") return;
    setSaveState({ kind: "loading" });
    try {
      // Derive title from the user question preceding this assistant message (AC-F6-5).
      // Read the settled messages from the store at click time instead of taking the array
      // as a prop — a new `messages` reference on every appended token would otherwise bust
      // this row's React.memo (and recreate this callback) for all visible rows (I3).
      const allMessages = useChatStore.getState().messages;
      const title = deriveSaveTitle(msg, msgIndex, allMessages);
      // Collect source page-ids from citations if available
      const sources =
        msg.citations && msg.citations.length > 0 ? msg.citations.map((c) => c.id) : undefined;
      const result = await saveToWikiV2({
        title,
        content: msg.content,
        vault_id: vaultId ?? null,
        sources,
        conversation_id: conversationId ?? null,
      });
      setSaveState({ kind: "success", pageId: result.page_id, filePath: result.file_path });
      // Success toast — i18n IT/EN (F16)
      showToast(t("chat.saveToWikiSavedToast", { path: result.file_path }), "success");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : t("chat.saveToWikiError");
      setSaveState({ kind: "error", message });
      showToast(t("chat.saveToWikiErrorToast"), "error");
    }
  }, [saveState.kind, msg, msgIndex, vaultId, conversationId, t]);

  return (
    <div>
      <MessageRoleLabel role={msg.role} t={t} />
      {/* Pass citations to MarkdownView for [n] and [Wn] decoration (ADR-0022 §2.4 / B2).
          R8-6: onCitationClick is wired — clicking [n] opens the cited page via
          setActiveSection("pages") + selectPage(slug, "tree") (AC-R8-6-2).
          Web citations [Wn] open the source URL in a new tab. */}
      <MarkdownView
        content={msg.content}
        citations={msg.citations}
        {...(msg.web_citations !== undefined ? { webCitations: msg.web_citations } : {})}
        onCitationClick={onCitationClick}
      />

      {/* B2: Web sources panel — shown only when web_citations is non-empty */}
      {msg.web_citations && msg.web_citations.length > 0 && (
        <WebSourcesPanel webCitations={msg.web_citations} t={t} />
      )}

      {/* Metadata footer — cost + actions */}
      {(showCost || isLast) && msg.role === "assistant" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
            marginTop: 8,
            fontSize: 11,
            color: "var(--syn-text-dim)",
          }}
        >
          {/* I7: cost displayed at 4dp */}
          {costUsd > 0 && (
            <span aria-label={t("chat.cost")}>
              {t("chat.costLabel", { cost: costUsd.toFixed(4) })}
            </span>
          )}

          {/* Save to wiki (AC-F6-5) — wired to POST /chat/save-to-wiki (v0.6) */}
          {saveState.kind === "idle" || saveState.kind === "error" ? (
            <button
              type="button"
              onClick={() => void handleSaveToWiki()}
              data-testid="save-to-wiki-btn"
              className="syn-btn syn-btn--secondary syn-btn--sm"
              title={t("chat.saveToWiki")}
            >
              {t("chat.saveToWiki")}
            </button>
          ) : saveState.kind === "loading" ? (
            <button
              type="button"
              data-testid="save-to-wiki-btn"
              disabled
              className="syn-btn syn-btn--secondary syn-btn--sm"
            >
              {t("chat.saveToWikiSaving")}
            </button>
          ) : (
            /* success */
            <span
              data-testid="save-to-wiki-success"
              style={{ color: "var(--syn-green)", fontSize: 11 }}
              title={saveState.filePath}
            >
              {t("chat.saveToWikiSaved", { path: saveState.filePath })}
            </span>
          )}

          {/* Inline error — shown below the button on next render */}
          {saveState.kind === "error" && (
            <span
              data-testid="save-to-wiki-error"
              style={{ color: "var(--syn-red)", fontSize: 11 }}
            >
              {saveState.message}
            </span>
          )}

          {/* Regenerate (AC-F6-4) — only on last assistant message */}
          {isLast && onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              className="syn-btn syn-btn--secondary syn-btn--sm"
              title={t("chat.regenerate")}
            >
              {t("chat.regenerate")}
            </button>
          )}
        </div>
      )}
    </div>
  );
});

// ─── Chat empty state ─────────────────────────────────────────────────────────

/**
 * Branded empty state shown when a conversation has no messages and is not streaming.
 * Renders: Synapse logo (72px, subtle opacity) + short title + 3 example-question chips.
 * Chip click uses the same onSend path as MessageInput (ADR-0048 §2.3 / T3).
 * INVARIANT I3: no markdown/LaTeX parse here; no per-token work.
 * INVARIANT: must NOT render while streaming — caller gates on !isStreaming.
 */
interface ChatEmptyStateProps {
  onSend: ((text: string) => void) | undefined;
  t: ReturnType<typeof useTranslation>["t"];
}

function ChatEmptyState({ onSend, t }: ChatEmptyStateProps): ReactNode {
  const chips = [t("chat.examples.q1"), t("chat.examples.q2"), t("chat.examples.q3")] as const;

  return (
    <div
      className="chat-empty-state"
      data-testid="chat-empty-state"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        padding: "32px 24px",
        gap: 16,
        textAlign: "center",
      }}
    >
      {/* Brand logo — subtle opacity so it doesn't overpower the chips */}
      <img
        src={synapseLogo}
        alt="Synapse"
        width={72}
        height={72}
        style={{ opacity: 0.25 }}
        aria-hidden="true"
      />

      {/* Short title */}
      <p
        style={{
          margin: 0,
          fontSize: 15,
          fontWeight: 600,
          color: "var(--syn-text-muted)",
          lineHeight: 1.4,
        }}
      >
        {t("chat.emptyTitle")}
      </p>

      {/* Example-question chips */}
      <div
        className="chat-empty-prompts"
        data-testid="chat-example-chips"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          width: "100%",
          maxWidth: 420,
        }}
      >
        {chips.map((chip) => (
          <button
            key={chip}
            type="button"
            className="syn-quick-prompt"
            data-testid="chat-example-chip"
            onClick={() => onSend?.(chip)}
            style={{
              background: "transparent",
              border: "1px solid var(--syn-border)",
              borderRadius: "var(--syn-radius-pill, 9999px)",
              color: "var(--syn-text-muted)",
              cursor: onSend ? "pointer" : "default",
              fontSize: 13,
              lineHeight: 1.4,
              padding: "8px 16px",
              textAlign: "left",
              transition: "background-color 0.12s ease, border-color 0.12s ease",
            }}
            onMouseEnter={(e) => {
              if (!onSend) return;
              (e.currentTarget as HTMLButtonElement).style.backgroundColor =
                "var(--syn-accent-soft)";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.backgroundColor = "transparent";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
            }}
          >
            {chip}
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Web sources panel (B2) ───────────────────────────────────────────────────

/**
 * WebSourcesPanel — compact "Fonti web" section rendered below assistant messages
 * that carry web_citations from a SearXNG-backed web search.
 *
 * INVARIANT I3: static render from settled data — no per-token work.
 * Each citation link opens in a new tab (noopener,noreferrer).
 */
interface WebSourcesPanelProps {
  webCitations: WebCitationRef[];
  t: ReturnType<typeof useTranslation>["t"];
}

function WebSourcesPanel({ webCitations, t }: WebSourcesPanelProps): ReactNode {
  return (
    <div
      data-testid="web-sources-panel"
      style={{
        marginTop: 10,
        padding: "8px 10px",
        background: "var(--syn-surface-sunken)",
        borderRadius: 6,
        border: "1px solid var(--syn-border)",
        fontSize: 11,
      }}
    >
      <div
        style={{
          fontWeight: 600,
          color: "var(--syn-text-dim)",
          marginBottom: 4,
          letterSpacing: "0.04em",
          fontSize: 10,
        }}
      >
        {t("chat.webSources")}
      </div>
      <ol
        style={{
          margin: 0,
          padding: "0 0 0 16px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {webCitations.map((wc) => (
          <li key={wc.index} style={{ color: "var(--syn-text-muted)" }}>
            <a
              href={wc.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: "var(--syn-accent)",
                textDecoration: "none",
                fontSize: 11,
              }}
              title={wc.url}
            >
              [W{wc.index}] {wc.title}
            </a>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ─── Role label ───────────────────────────────────────────────────────────────

function MessageRoleLabel({
  role,
  t,
}: {
  role: string;
  t: ReturnType<typeof useTranslation>["t"];
}): ReactNode {
  const isUser = role === "user";
  // UXA-08: use the shared .syn-role-label class (9px, text-dim, uppercase) so role
  // labels are visually subordinate to message content. Border-left stripe provides the
  // primary visual differentiator between turns (CVD-safe: doesn't rely on color alone).
  // marginBottom and paddingLeft kept inline (layout, not appearance).
  return (
    <div
      className="syn-role-label"
      style={{
        marginBottom: 4,
        borderLeft: isUser
          ? "3px solid var(--syn-accent-soft)"
          : "3px solid var(--syn-notice-success-bg)",
        paddingLeft: 4,
      }}
    >
      {isUser ? t("chat.roleUser") : t("chat.roleAssistant")}
    </div>
  );
}
