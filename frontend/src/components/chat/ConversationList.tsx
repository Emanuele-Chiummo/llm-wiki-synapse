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
 *
 * R7-3:
 * - Inline rename via hover/kebab edit icon → PATCH /conversations/{id} (optimistic + rollback).
 * - Filter/search input (client-side, debounced 200ms, AC-R7-3-2).
 * - Virtualized list stays I4-compliant regardless of filter.
 */

import {
  useEffect,
  useRef,
  useCallback,
  useState,
  useMemo,
  type ReactNode,
  type MouseEvent,
  type KeyboardEvent,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { Pencil, Check, X } from "lucide-react";
import {
  useChatStore,
  useConversations,
  selectActiveConversationId,
  selectSetActiveConversationId,
  selectSetConversations,
  selectAddConversation,
  selectRemoveConversation,
  selectUpdateConversation,
  selectConversationsLoading,
  selectSetConversationsLoading,
  selectSetConversationsError,
  selectSetMessages,
  selectSetMessagesLoading,
  selectSetMessagesError,
  selectConversationsNeedRefresh,
  selectClearConversationsNeedRefresh,
} from "../../store/chatStore";
import type { ConversationSummary } from "../../store/chatStore";
import {
  fetchConversations,
  createConversation,
  deleteConversation,
  renameConversation,
  fetchMessages,
} from "../../api/chatClient";
import { selectVaultId, useAppStore } from "../../store/appStore";
import { showToast } from "../common/Toast";

// Virtualize when list exceeds this threshold (I4)
const VIRTUAL_THRESHOLD = 50;

// AC-R7-3-3: debounce for the filter input (ms)
const FILTER_DEBOUNCE_MS = 200;

export function ConversationList({
  onConversationSelected,
}: {
  /** Called after a conversation is selected or created — the mobile drawer
   * uses this to close itself (ADR-0057 §3: selection closes the drawer). */
  onConversationSelected?: () => void;
} = {}): ReactNode {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);

  const conversations = useConversations();
  const activeId = useChatStore(selectActiveConversationId);
  const loading = useChatStore(selectConversationsLoading);

  const setConversations = useChatStore(selectSetConversations);
  const setActiveId = useChatStore(selectSetActiveConversationId);
  const addConversation = useChatStore(selectAddConversation);
  const removeConversation = useChatStore(selectRemoveConversation);
  const updateConversation = useChatStore(selectUpdateConversation);
  const setLoading = useChatStore(selectSetConversationsLoading);
  const setError = useChatStore(selectSetConversationsError);
  const setMessages = useChatStore(selectSetMessages);
  const setMessagesLoading = useChatStore(selectSetMessagesLoading);
  const setMessagesError = useChatStore(selectSetMessagesError);
  // UXB-1: refresh trigger
  const conversationsNeedRefresh = useChatStore(selectConversationsNeedRefresh);
  const clearConversationsNeedRefresh = useChatStore(selectClearConversationsNeedRefresh);

  // R7-3: filter state
  const [filterRaw, setFilterRaw] = useState("");
  const [filter, setFilter] = useState("");
  const filterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Debounce filter (AC-R7-3-3)
  const handleFilterChange = useCallback((value: string) => {
    setFilterRaw(value);
    if (filterTimerRef.current) clearTimeout(filterTimerRef.current);
    filterTimerRef.current = setTimeout(() => setFilter(value), FILTER_DEBOUNCE_MS);
  }, []);

  useEffect(() => {
    return () => {
      if (filterTimerRef.current) clearTimeout(filterTimerRef.current);
    };
  }, []);

  // AC-R7-3-2: client-side title filter (no backend round-trip)
  const filteredConversations = useMemo(() => {
    const q = filter.toLowerCase().trim();
    if (!q) return conversations;
    return conversations.filter((c) => (c.title ?? "").toLowerCase().includes(q));
  }, [conversations, filter]);

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
      // AC-F6-1: restore last active conversation (most-recently updated).
      // F5 fix: read activeConversationId from the store at execution time via
      // getState() — NOT from the closure captured at callback-creation time.
      // The closure only has [vaultId] in deps, so `activeId` (from the outer render)
      // would be stale after a completed turn sets it; that stale null would cause
      // every UXB-1 refresh to yank selection back to items[0].
      const currentActiveId = useChatStore.getState().activeConversationId;
      if (!currentActiveId && res.items.length > 0) {
        const first = res.items[0];
        if (first) {
          setActiveId(first.id);
          void loadMessages(first.id);
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

  // UXB-1: re-fetch conversation list when a stream finishes so the auto-generated
  // title and preview are reflected (AC-UXB1-4).
  useEffect(() => {
    if (!conversationsNeedRefresh) return;
    clearConversationsNeedRefresh();
    void loadConversations();
  }, [conversationsNeedRefresh, clearConversationsNeedRefresh, loadConversations]);

  const handleSelect = useCallback(
    (conv: ConversationSummary) => {
      // F2: abort any in-flight stream before switching conversations. Without this,
      // the stream's finalizeTurn would target the new conversation's messages list.
      // store.abortStream() also clears isStreaming so the UI updates immediately.
      useChatStore.getState().abortStream();
      setActiveId(conv.id);
      setMessages([]);
      void loadMessages(conv.id);
      onConversationSelected?.();
    },
    [setActiveId, setMessages, loadMessages, onConversationSelected],
  );

  const handleNew = useCallback(async () => {
    try {
      const conv = await createConversation({ vault_id: vaultId });
      addConversation(conv);
      setActiveId(conv.id);
      setMessages([]);
      onConversationSelected?.();
    } catch (err) {
      showToast(t("chat.newConvError"), "error");
      console.error("[chat] create conversation error", err);
    }
  }, [vaultId, addConversation, setActiveId, setMessages, t, onConversationSelected]);

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

  // R7-3: Rename handler — optimistic update with rollback on error (AC-R7-3-1)
  const handleRename = useCallback(
    async (convId: string, newTitle: string) => {
      const conv = conversations.find((c) => c.id === convId);
      if (!conv) return;
      const prevTitle = conv.title;

      // Optimistic update
      updateConversation(convId, { title: newTitle });
      try {
        const updated = await renameConversation(convId, newTitle);
        updateConversation(convId, { title: updated.title });
      } catch (err) {
        // Rollback
        updateConversation(convId, { title: prevTitle });
        showToast(t("chat.renameError"), "error");
        console.error("[chat] rename conversation error", err);
      }
    },
    [conversations, updateConversation, t],
  );

  const shouldVirtualize = filteredConversations.length > VIRTUAL_THRESHOLD;

  return (
    <div
      className="chat-section__conversations"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--syn-bg-soft)",
        borderRight: "1px solid var(--syn-border)",
        minWidth: 0,
      }}
      data-testid="conversation-list"
    >
      {/* Header + New button */}
      <div
        style={{
          padding: "12px 12px 8px",
          borderBottom: "1px solid var(--syn-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}
      >
        <span className="syn-eyebrow">{t("chat.conversations")}</span>
        <button
          type="button"
          onClick={() => void handleNew()}
          aria-label={t("chat.newConversation")}
          title={t("chat.newConversation")}
          style={{
            background: "var(--syn-surface-hover)",
            border: "1px solid var(--syn-border)",
            borderRadius: 4,
            color: "var(--syn-text)",
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

      {/* R7-3: Search/filter input (AC-R7-3-2) */}
      <div style={{ padding: "6px 8px", flexShrink: 0 }}>
        <input
          type="search"
          data-testid="conversation-filter-input"
          value={filterRaw}
          onChange={(e) => handleFilterChange(e.target.value)}
          placeholder={t("chat.searchConversations")}
          aria-label={t("chat.searchConversations")}
          style={{
            width: "100%",
            boxSizing: "border-box",
            fontSize: 11,
            padding: "4px 8px",
            border: "1px solid var(--syn-border)",
            borderRadius: 4,
            background: "var(--syn-bg)",
            color: "var(--syn-text)",
          }}
        />
      </div>

      {/* Loading state */}
      {loading && (
        <div style={{ padding: "12px", color: "var(--syn-text-dim)", fontSize: 12 }}>
          {t("common.loading")}
        </div>
      )}

      {/* List */}
      {!loading && !shouldVirtualize && (
        <div style={{ flex: 1, overflowY: "auto" }}>
          {filteredConversations.map((conv) => (
            <ConvItem
              key={conv.id}
              conv={conv}
              isActive={conv.id === activeId}
              onSelect={handleSelect}
              onDelete={handleDelete}
              onRename={handleRename}
            />
          ))}
          {filteredConversations.length === 0 && !loading && (
            <div style={{ padding: "16px 12px", color: "var(--syn-text-dim)", fontSize: 12 }}>
              {filter ? t("chat.noMatchingConversations") : t("chat.noConversations")}
            </div>
          )}
        </div>
      )}

      {/* Virtualized list for >50 items (I4) */}
      {!loading && shouldVirtualize && (
        <VirtualConvList
          conversations={filteredConversations}
          activeId={activeId}
          onSelect={handleSelect}
          onDelete={handleDelete}
          onRename={handleRename}
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
  onRename,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (c: ConversationSummary) => void;
  onDelete: (id: string, e: MouseEvent) => Promise<void>;
  onRename: (id: string, title: string) => Promise<void>;
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
                onRename={onRename}
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
  onRename,
}: {
  conv: ConversationSummary;
  isActive: boolean;
  onSelect: (c: ConversationSummary) => void;
  onDelete: (id: string, e: MouseEvent) => Promise<void>;
  onRename: (id: string, title: string) => Promise<void>;
}): ReactNode {
  const { t } = useTranslation();
  const displayTitle = conv.title ?? t("chat.untitled");
  const date = new Date(conv.updated_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

  // R7-3: inline rename state
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(displayTitle);
  const [hovered, setHovered] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const startRename = useCallback(
    (e: MouseEvent) => {
      e.stopPropagation();
      setRenameValue(displayTitle);
      setIsRenaming(true);
    },
    [displayTitle],
  );

  const commitRename = useCallback(async () => {
    const newTitle = renameValue.trim();
    if (newTitle && newTitle !== displayTitle) {
      await onRename(conv.id, newTitle);
    }
    setIsRenaming(false);
  }, [renameValue, displayTitle, onRename, conv.id]);

  const cancelRename = useCallback(() => {
    setRenameValue(displayTitle);
    setIsRenaming(false);
  }, [displayTitle]);

  const handleRenameKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        void commitRename();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        cancelRename();
      }
    },
    [commitRename, cancelRename],
  );

  // Focus the input when rename mode starts
  useEffect(() => {
    if (isRenaming) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isRenaming]);

  if (isRenaming) {
    return (
      <div
        data-testid="conv-item-renaming"
        style={{
          padding: "6px 8px",
          background: isActive ? "var(--syn-accent-soft)" : "var(--syn-surface)",
          borderLeft: isActive ? "2px solid var(--syn-accent)" : "2px solid transparent",
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          data-testid="conv-rename-input"
          type="text"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={handleRenameKeyDown}
          aria-label={t("chat.renameConversation")}
          style={{
            flex: 1,
            fontSize: 12,
            padding: "3px 6px",
            border: "1px solid var(--syn-accent)",
            borderRadius: 3,
            background: "var(--syn-bg)",
            color: "var(--syn-text)",
            outline: "none",
            minWidth: 0,
          }}
        />
        <button
          type="button"
          data-testid="conv-rename-commit-btn"
          onClick={() => {
            void commitRename();
          }}
          title={t("chat.renameCommit")}
          aria-label={t("chat.renameCommit")}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--syn-accent)",
            padding: "2px",
          }}
        >
          <Check size={12} aria-hidden="true" />
        </button>
        <button
          type="button"
          data-testid="conv-rename-cancel-btn"
          onClick={cancelRename}
          title={t("chat.renameCancel")}
          aria-label={t("chat.renameCancel")}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--syn-text-dim)",
            padding: "2px",
          }}
        >
          <X size={12} aria-hidden="true" />
        </button>
      </div>
    );
  }

  return (
    <div
      className="conversation-list__item"
      role="button"
      tabIndex={0}
      onClick={() => onSelect(conv)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(conv);
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-current={isActive ? "true" : undefined}
      style={{
        padding: "8px 12px",
        background: isActive ? "var(--syn-accent-soft)" : "transparent",
        borderLeft: isActive ? "2px solid var(--syn-accent)" : "2px solid transparent",
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
            color: isActive ? "var(--syn-text)" : "var(--syn-text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {displayTitle}
        </div>
        {/* UXB-1: preview snippet (AC-UXB1-3) */}
        {conv.preview ? (
          <div
            data-testid="conv-preview"
            style={{
              fontSize: 9,
              color: "var(--syn-text-dim)",
              marginTop: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {conv.preview}
          </div>
        ) : null}
        <div
          style={{
            fontSize: 11,
            color: "var(--syn-text-dim)",
            marginTop: 2,
            fontFamily: "var(--syn-font-mono)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {date}
        </div>
      </div>

      {/* Actions: rename + delete (visible on hover or active; always visible on
          touch devices via .conv-item__actions CSS override — no hover exists there) */}
      <div
        className="conv-item__actions"
        style={{ display: "flex", gap: 2, flexShrink: 0, opacity: hovered || isActive ? 1 : 0 }}
      >
        {/* Rename button (R7-3) */}
        <button
          type="button"
          data-testid="conv-rename-btn"
          onClick={startRename}
          aria-label={t("chat.renameConversation")}
          title={t("chat.renameConversation")}
          style={{
            background: "none",
            border: "none",
            color: "var(--syn-text-dim)",
            cursor: "pointer",
            fontSize: 12,
            padding: "2px 4px",
            flexShrink: 0,
            lineHeight: 1,
            display: "flex",
            alignItems: "center",
          }}
        >
          <Pencil size={11} aria-hidden="true" />
        </button>

        {/* Delete button */}
        <button
          type="button"
          onClick={(e) => void onDelete(conv.id, e)}
          aria-label={t("chat.deleteConversation")}
          title={t("chat.deleteConversation")}
          style={{
            background: "none",
            border: "none",
            color: "var(--syn-text-dim)",
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
    </div>
  );
}
