/**
 * MetaFileView.tsx — viewer + editor for vault meta-files (WS-D8 / K1 / I5 / v1.5 P1).
 *
 * Renders schema.md or purpose.md and — since v1.5 P1 (ADR-0066, llm_wiki parity) — lets the
 * user EDIT them in place: an Edit button swaps the rendered prose for the shared CodeMirror
 * editor; Save persists via PUT /vault/meta/{name} (saveVaultMeta) and notifies the parent so
 * the NavTree cache updates. Mirrors LLM Wiki, where purpose/schema are ordinary editable pages.
 *
 * Invariants:
 *   I3: renderMarkdown is called ONCE per content string, memoised on the content prop; the
 *       editor never drives per-keystroke React state (getContent() is read only at Save).
 *   I4: editing uses CodeMirror 6 (no WYSIWYG). Meta files are short — no virtualization needed.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Pencil, Check, X } from "lucide-react";
import { PanelDrawer } from "../panels/PanelDrawer";
import { renderMarkdown, stripLeadingFrontmatter } from "../chat/renderMarkdown";
import { CodeMirrorEditor, type CodeMirrorEditorHandle } from "./CodeMirrorEditor";
import { saveVaultMeta, type VaultMetaFile } from "../../api/vaultMetaClient";

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface MetaFileViewProps {
  /** The meta file to display; null = drawer closed. */
  file: VaultMetaFile | null;
  /** Close callback. */
  onClose: () => void;
  /**
   * v1.5 P1: called after a successful save with the persisted file, so the parent
   * (NavTree) can update its cached content and refresh the tree.
   */
  onSaved?: (updated: VaultMetaFile) => void;
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "12px 16px",
  borderBottom: "1px solid var(--syn-border)",
  flexShrink: 0,
  background: "var(--syn-bg-soft)",
};

const TITLE_STYLE: CSSProperties = {
  flex: 1,
  fontSize: 14,
  fontWeight: 600,
  color: "var(--syn-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const ICON_BTN_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 5,
  padding: "4px 8px",
  border: "1px solid var(--syn-border)",
  background: "var(--syn-surface)",
  cursor: "pointer",
  color: "var(--syn-text-muted)",
  borderRadius: 5,
  fontSize: 12,
  fontWeight: 500,
  flexShrink: 0,
};

const PRIMARY_BTN_STYLE: CSSProperties = {
  ...ICON_BTN_STYLE,
  color: "#ffffff",
  background: "var(--syn-accent)",
  border: "1px solid var(--syn-accent)",
};

const CLOSE_BTN_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 4,
  border: "none",
  background: "transparent",
  cursor: "pointer",
  color: "var(--syn-text-dim)",
  borderRadius: 4,
  flexShrink: 0,
};

const BODY_STYLE: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: "16px 20px",
};

const EDITOR_WRAP_STYLE: CSSProperties = {
  flex: 1,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
};

const ERROR_STYLE: CSSProperties = {
  padding: "8px 16px",
  fontSize: 12,
  color: "var(--syn-red)",
  background: "var(--syn-notice-danger-bg, #ffebe9)",
  borderBottom: "1px solid var(--syn-border)",
  flexShrink: 0,
};

// Markdown body — reuse the shared prose class that markdown.css already styles
// (headings, lists, tables, code) for the wiki reader and chat.
const PROSE_CLASS = "synapse-markdown__body";

// ─── Component ─────────────────────────────────────────────────────────────────

/**
 * MetaFileView — shown when the user clicks a meta node (Schema / Purpose) in the NavTree
 * Vault section. Rendered via PanelDrawer (right side). Read view by default; Edit toggles the
 * CodeMirror editor + Save/Cancel.
 */
export function MetaFileView({ file, onClose, onSaved }: MetaFileViewProps) {
  const { t } = useTranslation();

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const editorHandle = useRef<CodeMirrorEditorHandle | null>(null);

  // Reset edit state whenever the open file changes (switch meta file / close / reopen).
  useEffect(() => {
    setEditing(false);
    setSaving(false);
    setSaveError(null);
  }, [file?.path]);

  // I3: renderMarkdown called ONCE per content string, memoised on file.content.
  const html = useMemo(() => {
    if (!file?.content) return "";
    const stripped = stripLeadingFrontmatter(file.content);
    return renderMarkdown(stripped);
  }, [file?.content]);

  const handleSave = useCallback(async () => {
    if (!file || saving) return;
    const content = editorHandle.current?.getContent() ?? file.content;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await saveVaultMeta(file.name, content);
      onSaved?.(updated);
      setEditing(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [file, saving, onSaved]);

  const handleCancel = useCallback(() => {
    setEditing(false);
    setSaveError(null);
  }, []);

  const open = file !== null;

  return (
    <PanelDrawer
      open={open}
      side="right"
      onClose={onClose}
      label={file ? file.title : t("meta.drawer")}
    >
      {/* Header */}
      <div style={HEADER_STYLE}>
        <FileText
          size={15}
          aria-hidden="true"
          style={{ color: "var(--syn-text-muted)", flexShrink: 0 }}
        />
        <span style={TITLE_STYLE} data-testid="meta-file-title">
          {file?.title ?? ""}
        </span>

        {editing ? (
          <>
            <button
              type="button"
              data-testid="meta-file-cancel"
              onClick={handleCancel}
              disabled={saving}
              style={ICON_BTN_STYLE}
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              data-testid="meta-file-save"
              onClick={() => void handleSave()}
              disabled={saving}
              style={PRIMARY_BTN_STYLE}
            >
              <Check size={13} aria-hidden="true" />
              {saving ? t("meta.saving") : t("common.save")}
            </button>
          </>
        ) : (
          <button
            type="button"
            data-testid="meta-file-edit"
            onClick={() => setEditing(true)}
            style={ICON_BTN_STYLE}
          >
            <Pencil size={13} aria-hidden="true" />
            {t("common.edit")}
          </button>
        )}

        <button
          type="button"
          aria-label={t("common.close")}
          data-testid="meta-file-close"
          onClick={onClose}
          style={CLOSE_BTN_STYLE}
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {saveError && (
        <div style={ERROR_STYLE} data-testid="meta-file-error">
          {saveError}
        </div>
      )}

      {/* Body — rendered markdown (read) or CodeMirror (edit) */}
      {editing && file ? (
        <div style={EDITOR_WRAP_STYLE} data-testid="meta-file-editor">
          <CodeMirrorEditor initialContent={file.content} handleRef={editorHandle} />
        </div>
      ) : (
        <div
          data-testid="meta-file-body"
          style={BODY_STYLE}
          className={PROSE_CLASS}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
    </PanelDrawer>
  );
}
