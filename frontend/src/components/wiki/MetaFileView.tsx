/**
 * MetaFileView.tsx — read-only viewer for vault meta-files (WS-D8 / K1 / I5).
 *
 * Renders schema.md or purpose.md content as sanitised HTML using the shared
 * renderMarkdown() utility.  Displayed in a right-side PanelDrawer so it does
 * not touch NoteView or the center-panel router (decoupled by design — the
 * NoteView refactor is owned by a parallel agent).
 *
 * Invariants:
 *   I3: renderMarkdown is called ONCE per content string, memoised on the
 *       immutable content prop.  Never called per token (no streaming here).
 *   I4: no virtualization needed — meta files are short (< 200 lines).
 *
 * No edit / delete actions: meta files are schema-defining vault artefacts;
 * they must be human-authored (K1 / I5 — Obsidian compatibility).
 */

import { useMemo, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { FileText, X } from "lucide-react";
import { PanelDrawer } from "../panels/PanelDrawer";
import { renderMarkdown, stripLeadingFrontmatter } from "../chat/renderMarkdown";
import type { VaultMetaFile } from "../../api/vaultMetaClient";

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface MetaFileViewProps {
  /** The meta file to display; null = drawer closed. */
  file: VaultMetaFile | null;
  /** Close callback. */
  onClose: () => void;
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

const BADGE_STYLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  color: "var(--syn-text-dim)",
  background: "var(--syn-surface-sunken)",
  border: "1px solid var(--syn-border-subtle)",
  borderRadius: 4,
  padding: "1px 6px",
  flexShrink: 0,
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

// Markdown body — reuse the shared prose class that markdown.css already styles
// (headings, lists, tables, code) for the wiki reader and chat. The previous
// component-local class had no CSS, so schema.md/purpose.md rendered unstyled.
const PROSE_CLASS = "synapse-markdown__body";

// ─── Component ─────────────────────────────────────────────────────────────────

/**
 * MetaFileView — shown when the user clicks a meta node (Schema / Purpose) in
 * the NavTree Vault section.
 *
 * Rendered via PanelDrawer (right side) so it layers over the existing panels
 * without modifying the center-panel router or NoteView.
 */
export function MetaFileView({ file, onClose }: MetaFileViewProps) {
  const { t } = useTranslation();

  // I3: renderMarkdown called ONCE per content string, memoised on file.content.
  const html = useMemo(() => {
    if (!file?.content) return "";
    const stripped = stripLeadingFrontmatter(file.content);
    return renderMarkdown(stripped);
  }, [file?.content]);

  const open = file !== null;

  return (
    <PanelDrawer
      open={open}
      side="right"
      onClose={onClose}
      label={file ? `${file.title} — ${t("meta.readOnly")}` : t("meta.drawer")}
    >
      {/* Header */}
      <div style={HEADER_STYLE}>
        <FileText size={15} aria-hidden="true" style={{ color: "var(--syn-text-muted)", flexShrink: 0 }} />
        <span style={TITLE_STYLE} data-testid="meta-file-title">
          {file?.title ?? ""}
        </span>
        <span style={BADGE_STYLE} aria-label={t("meta.readOnly")}>
          {t("meta.readOnly")}
        </span>
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

      {/* Body — rendered markdown, read-only */}
      <div
        data-testid="meta-file-body"
        style={BODY_STYLE}
        className={PROSE_CLASS}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </PanelDrawer>
  );
}
