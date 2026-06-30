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
 */

import { useEffect, useRef } from "react";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
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
}

export function CodeMirrorEditor({ initialContent, handleRef }: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const view = new EditorView({
      state: EditorState.create({
        doc: initialContent,
        extensions: [
          markdown(),
          oneDark,
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
        ],
      }),
      parent: containerRef.current,
    });

    // Expose getContent to parent via handleRef
    handleRef.current = {
      getContent: () => view.state.doc.toString(),
    };

    return () => {
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
