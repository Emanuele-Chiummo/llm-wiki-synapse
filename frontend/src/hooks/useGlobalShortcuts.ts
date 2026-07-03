/**
 * useGlobalShortcuts.ts — single global keydown listener for app-wide shortcuts (ADR-0048 §T2).
 *
 * Shortcuts:
 *   Cmd/Ctrl+K     → toggle command palette (ALWAYS active, even in inputs/CodeMirror)
 *   Cmd/Ctrl+N     → new conversation (ignored while typing in an input)
 *   Cmd/Ctrl+1..5  → switch to the first 5 sections (ignored while typing in an input)
 *
 * "Ignored while typing" discrimination: checks event.target tagName and
 * isContentEditable. Cmd+K is the sole exception — it must remain reachable
 * from a focused editor (ADR-0048 §2.2 last bullet).
 *
 * INVARIANT I3: the listener is registered once on mount; no per-token work.
 */

import { useEffect, useCallback } from "react";
import { useGraphStore, selectSetActiveSection } from "../store/graphStore";
import type { Section } from "../store/graphStore";
import { createConversation } from "../api/chatClient";
import {
  useChatStore,
  selectAddConversation,
  selectSetActiveConversationId,
  selectSetMessages,
} from "../store/chatStore";
import { useGraphStore as _useGraphStore, selectVaultId } from "../store/graphStore";
import { showToast } from "../components/common/Toast";

// First 5 sections in NavRail order (Cmd+1..5 mapping).
const SECTION_SHORTCUTS: Section[] = ["chat", "pages", "sources", "search", "graph"];

/**
 * Returns true when the event target is a text-editable element that should
 * consume keyboard shortcuts (input, textarea, contenteditable, CodeMirror).
 * Cmd+K is NOT passed through this guard — it is always active.
 */
function isTypingTarget(e: KeyboardEvent): boolean {
  const target = e.target;
  // Guard: only check Element nodes (not window, document, etc.).
  if (!(target instanceof Element)) return false;

  const tag = (target as HTMLElement).tagName?.toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  // isContentEditable may be undefined in older jsdom / non-standard envs; fall back
  // to checking the contentEditable property directly (browsers return "true" string;
  // jsdom returns boolean true — handle both).
  const ce = (target as HTMLElement).contentEditable;
  if (ce === "true" || ce === "plaintext-only" || (ce as unknown) === true) return true;

  // CodeMirror 6 renders its editable surface as a div with role="textbox" or
  // the .cm-content class. Check for either.
  if (target.classList?.contains("cm-content")) return true;
  if (target.getAttribute("role") === "textbox") return true;

  return false;
}

interface UseGlobalShortcutsOptions {
  /** Whether the command palette is currently open. */
  paletteOpen: boolean;
  /** Toggle the command palette open/closed. */
  onTogglePalette: () => void;
}

export function useGlobalShortcuts({
  paletteOpen,
  onTogglePalette,
}: UseGlobalShortcutsOptions): void {
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const vaultId = _useGraphStore(selectVaultId);
  const addConversation = useChatStore(selectAddConversation);
  const setActiveConversationId = useChatStore(selectSetActiveConversationId);
  const setMessages = useChatStore(selectSetMessages);

  const handleNewConversation = useCallback(async () => {
    try {
      const conv = await createConversation({ vault_id: vaultId });
      addConversation(conv);
      setActiveConversationId(conv.id);
      setMessages([]);
      // Switch to chat section so the new conversation is visible.
      setActiveSection("chat");
    } catch (err) {
      console.error("[shortcuts] new conversation failed", err);
      // Use the same error toast path as ConversationList.
      showToast("Failed to create conversation.", "error");
    }
  }, [vaultId, addConversation, setActiveConversationId, setMessages, setActiveSection]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMod = e.ctrlKey || e.metaKey;
      if (!isMod) return;

      // ── Cmd/Ctrl+K — toggle palette (ALWAYS, ignores typing check) ─────────
      if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        onTogglePalette();
        return;
      }

      // All remaining shortcuts are ignored while the user is typing.
      if (isTypingTarget(e)) return;

      // ── Cmd/Ctrl+N — new conversation ──────────────────────────────────────
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        void handleNewConversation();
        return;
      }

      // ── Cmd/Ctrl+1..5 — switch to first 5 sections ─────────────────────────
      const digit = parseInt(e.key, 10);
      if (digit >= 1 && digit <= 5) {
        const section = SECTION_SHORTCUTS[digit - 1];
        if (section) {
          e.preventDefault();
          setActiveSection(section);
        }
        return;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [paletteOpen, onTogglePalette, handleNewConversation, setActiveSection]);
}
