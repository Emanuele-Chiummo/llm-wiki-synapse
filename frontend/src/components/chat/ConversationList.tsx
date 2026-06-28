/**
 * ConversationList.tsx — conversation sidebar (ADR-0019 §3 / F6 / AC-F6-1/2).
 *
 * - Fetches GET /conversations on mount and when vaultId changes.
 * - Create button: POST /conversations → select new conversation.
 * - Switch <100ms: just sets activeConversationId in the store, messages load async.
 * - Delete: DELETE /conversations/{id} (soft-delete on server).
 * - Virtualized for >50 conversations (I4).
 * - AC-F6-1: page refresh restores last active conversation (persisted in store → localStorage
 *   via the settingsStore pattern; the Postgres updated_at DESC sort ensures the right one
 *   is first in the list on reload).
 */

import { useEffect, useRef, useCallback, type ReactNode, type MouseEvent } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import {
  useChatStore,
  useConversations,
  selectActiveConversationId,
  selectSetActiveConversationId,
  selectSetConversations,
  selectAddConversation,
  selectRemoveConversation,
  selectConversationsLoading,
  selectSetConversationsLoading,
  selectSetConversationsError,
  selectSetMessages,
  selectSetMessagesLoading,
  selectSetMessagesError,
} from "../../store/chatStore";
import type { ConversationSummary } from "../../store/chatStore";
import {
  fetchConversations,
  createConversation,
  deleteConversation,
  fetchMessages,
} from "../../api/chatClient";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { showToast } from "../common/Toast";

// Virtualize when list exceeds this threshold (I4)
const VIRTUAL_THRESHOLD = 50;

export function ConversationList(): ReactNode {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);

  const conversations = useConversations();
  const activeId = useChatStore(selectActiveConversationId);
  const loading = useChatStore(selectConversationsLoading);

  const setConversations = useChatStore(selectSetConversations);
  const setActiveId = useChatStore(selectSetActiveConversationId);
  const addConversation = useChatStore(selectAddConversation);
  const removeConversation = useChatStore(selectRemoveConversation);
  const setLoading = useChatStore(selectSetConversationsLoading);
  const setError = useChatStore(selectSetConversationsError);
  const setMessages = useChatStore(selectSetMessages);
  const setMessagesLoading = useChatStore(selectSetMessagesLoading);
  const setMessagesError = useChatStore(selectSetMessagesError);

  const abortRef = useRef<AbortController | null>(null);

  // Fetch conversation list
  const loadConversations = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchConversations({ vault_id: vaultId }, ctrl.signal);
      setConversations(res.items);
      // AC-F6-1: restore last active conversation (most-recently updated)
      if (!activeId && res.items.length > 0) {
        const first = res.items[0];
        if (first) {
          setActiveId(first.id);
          loadMessages(first.id);
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vaultId]);

  const loadMessages = useCallback(
    async (convId: string) => {
      setMessagesLoading(true);
      setMessagesError(null);
      try {
        const res = await fetchMessages(convId);
        setMessages(res.items);
      } catch (err) {
        setMessagesError((err as Error).message);
      } finally {
        setMessagesLoading(false);
      }
    },
    [setMessages, setMessagesLoading, setMessagesError],
  );

  useEffect(() => {
    void loadConversations();
    return () => abortRef.current?.abort();
  }, [loadConversations]);

  const handleSelect = useCallback(
    (conv: ConversationSummary) => {
      setActiveId(conv.id);
      setMessages([]);
      void loadMessages(conv.id);
    },
    [setActiveId, setMessages, loadMessages],
  );

  const handleNew = useCallback(async () => {
    try {
      const conv = await createConversation({ vault_id: vaultId });
      addConversation(conv);
      setActiveId(conv.id);
      setMessages([]);
    } catch (err) {
      showToast(t("chat.newConvError"), "error");
      console.error("[chat] create conversation error", err);
    }
  }, [vaultId, addConversation, setActiveId, setMessages, t]);

  const handleDelete = useCallback(
    async (convId: string, e: MouseEvent) => {
      e.stopPropagation();
      try {
        await deleteConversation(convId);
        removeConversation(convId);
        if (activeId === convId) {
          setMessages([]);
        }
      } catch (err) {
        showToast(t("chat.deleteConvError"), "error");
        console.error("[chat] delete conversation error", err);
      }
    },
    [activeId, removeConversation, setMessages, t],
  );

  const shouldVirtualize = conversations.length > VIRTUAL_THRESHOLD;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#161b22",
        borderRight: "1px solid #21262d",
        minWidth: 0,
      }}
      data-testid="conversation-list"
    >
      {/* Header + New button */}
      <div
        style={{
          padding: "12px 12px 8px",
          borderBottom: "1px solid #21262d",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          {t("chat.conversations")}
        </span>
        <button
          type="button"
          onClick={() => void handleNew()}
          aria-label={t("chat.newConversation")}
          title={t("chat.newConversation")}
          style={{
            background: "#21262d",
            border: "1px solid #30363d",
            borderRadius: 4,
            color: "#e6edf3",
            cursor: "pointer",
            fontSize: 16,
            lineHeight: 1,
            padding: "2px 8px",
            display: "flex",
            alignItems: "center",
          }}
        >
          +
        </button>
      </div>

      {/* Loading state */}
      {loading && (
        <div style={{ padding: "12px", color: "#484f58", fontSize: 12 }}>
          {t("common.loading")}
        </div>
      )}

      {/* List */}
      {!loading && !shouldVirtualize && (
        <div style={{ flex: 1, overflowY: "auto" }}>
          {conversations.map((conv) => (
            <ConvItem
              key={conv.id}
              conv={conv}
              isActive={conv.id === activeId}
              onSelect={handleSelect}
              onDelete={handleDelete}
            />
          ))}
          {conversations.length === 0 && (
            <div style={{ padding: "16px 12px", color: "#484f58", fontSize: 12 }}>
              {t("chat.noConversations")}
            </div>
          )}
        </div>
      )}

      {/* Virtualized list for >50 items (I4) */}
      {!loading && shouldVirtualize && (
        <VirtualConvList
          conversations={conversations}
          activeId={activeId}
          onSelect={handleSelect}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}

// ─── VirtualConvList ──────────────────────────────────────────────────────────

function VirtualConvList({
  conversations,
  activeId,
  onSelect,
  onDelete,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (c: ConversationSummary) => void;
  onDelete: (id: string, e: MouseEvent) => Promise<void>;
}): ReactNode {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: conversations.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 52,
    overscan: 5,
  });

  return (
    <div ref={parentRef} style={{ flex: 1, overflowY: "auto" }}>
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((vi) => {
          const conv = conversations[vi.index];
          if (!conv) return null;
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vi.start}px)`,
              }}
            >
              <ConvItem
                conv={conv}
                isActive={conv.id === activeId}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── ConvItem ─────────────────────────────────────────────────────────────────

function ConvItem({
  conv,
  isActive,
  onSelect,
  onDelete,
}: {
  conv: ConversationSummary;
  isActive: boolean;
  onSelect: (c: ConversationSummary) => void;
  onDelete: (id: string, e: MouseEvent) => Promise<void>;
}): ReactNode {
  const { t } = useTranslation();
  const title = conv.title ?? t("chat.untitled");
  const date = new Date(conv.updated_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(conv)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(conv);
      }}
      aria-current={isActive ? "true" : undefined}
      style={{
        padding: "8px 12px",
        background: isActive ? "#1f2937" : "transparent",
        borderLeft: isActive ? "2px solid #1f6feb" : "2px solid transparent",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 4,
        transition: "background 0.1s ease",
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontSize: 13,
            color: isActive ? "#e6edf3" : "#c9d1d9",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 11, color: "#484f58", marginTop: 2 }}>{date}</div>
      </div>
      <button
        type="button"
        onClick={(e) => void onDelete(conv.id, e)}
        aria-label={t("chat.deleteConversation")}
        title={t("chat.deleteConversation")}
        style={{
          background: "none",
          border: "none",
          color: "#484f58",
          cursor: "pointer",
          fontSize: 14,
          padding: "2px 4px",
          flexShrink: 0,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
