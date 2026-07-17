/**
 * CodeMirrorEditor.tsx — thin wrapper that owns the CodeMirror 6 EditorView lifecycle.
 *
 * Extracted into its own module so tests can mock the entire file path
 * (vi.mock('../components/wiki/CodeMirrorEditor')) without loading the real
 * CodeMirror bundle (~4 MB) into the jsdom vitest worker.
 *
 * INVARIANT I3: the editor is created ONCE on mount with `initialContent`.
 *   - The caller reads the final doc via `editorRef.current.state.doc.toString()`
 *     only at Save time — never on each keystroke.
 *   - initialContent is intentionally excluded from the useEffect deps: the
 *     editor owns its state after mount.
 *
 * INVARIANT I4: CodeMirror 6 ONLY — no ProseMirror / Milkdown.
 *
 * DARK MODE (ADR-0048 §T1):
 *   A Compartment holds the theme extension so we can reconfigure it on resolved-
 *   theme change without destroying/recreating the editor.
 *   The swap is triggered by a MutationObserver on document.documentElement's
 *   dataset.theme attribute — the same attribute written by settingsStore's applier.
 *   This is NOT per-keystroke and NOT per-token (I3/I4 hold).
 */

import { useEffect, useRef } from "react";
import { EditorView } from "@codemirror/view";
import { EditorState, Compartment, type Extension } from "@codemirror/state";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";

export interface CodeMirrorEditorHandle {
  /** Return the current editor document as a string (called at Save time only). */
  getContent: () => string;
}

interface CodeMirrorEditorProps {
  initialContent: string;
  /** Ref exposed to the parent so it can call getContent() at Save time. */
  handleRef: { current: CodeMirrorEditorHandle | null };
  /**
   * R7-4: Called whenever the editor document changes (on each CodeMirror transaction).
   * The parent uses this to compute isDirty without storing the live content in state
   * (I3: no per-keystroke React re-renders from the content itself).
   */
  onContentChange?: (content: string) => void;
}

/** Returns the currently resolved theme from the document element attribute. */
function getResolvedTheme(): "light" | "dark" {
  try {
    return (document.documentElement.dataset["theme"] as "light" | "dark" | undefined) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

/** Build the CodeMirror theme extension for the given resolved theme. */
function buildThemeExtension(resolved: "light" | "dark"): Extension {
  return resolved === "dark" ? oneDark : [];
}

export function CodeMirrorEditor({
  initialContent,
  handleRef,
  onContentChange,
}: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // ── Theme compartment (ADR-0048 §T1) ──────────────────────────────────────
    // Holds the active theme extension so we can swap it without recreating the editor.
    const themeCompartment = new Compartment();
    const initialResolved = getResolvedTheme();

    // R7-4: update listener — fires on document change, calls onContentChange with the
    // new doc string so the parent can compute isDirty without per-keystroke state.
    const updateListener = onContentChange
      ? EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onContentChange(update.state.doc.toString());
          }
        })
      : ([] as Extension);

    const view = new EditorView({
      state: EditorState.create({
        doc: initialContent,
        extensions: [
          markdown(),
          themeCompartment.of(buildThemeExtension(initialResolved)),
          EditorView.lineWrapping,
          EditorView.theme({
            "&": {
              height: "100%",
              fontSize: "14px",
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            },
            ".cm-scroller": { overflow: "auto" },
            ".cm-content": { padding: "16px 20px" },
          }),
          updateListener,
        ],
      }),
      parent: containerRef.current,
    });

    // ── Watch for resolved-theme changes via MutationObserver ─────────────────
    // Observes dataset.theme on <html>; when it changes, reconfigure the compartment.
    // This is event-driven (never per-keystroke, never per-token) — I3/I4 hold.
    let lastResolved = initialResolved;
    const observer = new MutationObserver(() => {
      const next = getResolvedTheme();
      if (next !== lastResolved) {
        lastResolved = next;
        view.dispatch({
          effects: themeCompartment.reconfigure(buildThemeExtension(next)),
        });
      }
    });
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });

    // Expose getContent to parent via handleRef
    handleRef.current = {
      getContent: () => view.state.doc.toString(),
    };

    return () => {
      observer.disconnect();
      view.destroy();
      handleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — editor owns state after mount (I3)

  return (
    <div
      ref={containerRef}
      data-testid="codemirror-editor"
      style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}
    />
  );
}
