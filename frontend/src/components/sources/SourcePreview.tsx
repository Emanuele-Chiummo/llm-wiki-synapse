/**
 * SourcePreview.tsx — content-type dispatcher for raw source file preview [F11 / v0.6].
 *
 * Fetches GET /sources/content on selection, then renders by category:
 *   image    → <img src={rawUrl}> (I3: URL only, never load bytes into JS)
 *   pdf      → <embed src={rawUrl} type="application/pdf">
 *   text / markdown / code / data → renderMarkdown or <pre>
 *   av       → <audio> / <video controls>
 *   other    → "no preview" placeholder + raw-open link
 *
 * Header: filename · type label · size · ingested badge + derived-page links.
 * Derived pages open the wiki page via selectPage + setActiveSection("pages").
 *
 * INVARIANT I3: fetch content ONCE per selection change (AbortController on path change).
 *               Raw bytes are NEVER loaded into JS — sourceRawUrl() is used as element src.
 * INVARIANT I3: selectors + useShallow, no unrelated store subscriptions.
 */

import { useState, useEffect, useCallback, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Image, FileVideo, File, Download } from "lucide-react";
import { getSourceContent, getSourceDerivedPages, sourceRawUrl } from "../../api/sourcesClient";
import type {
  SourceContentResponse,
  SourceDerivedPage,
  SourceCategory,
  SourceRoot,
} from "../../api/sourcesClient";
import { useGraphStore, selectSelectPage, selectSetActiveSection } from "../../store/graphStore";
import { renderMarkdown } from "../chat/renderMarkdown";

// ─── Category icon + label helpers ───────────────────────────────────────────

function categoryIcon(cat: SourceCategory, size = 14) {
  switch (cat) {
    case "image":
      return <Image size={size} aria-hidden="true" />;
    case "pdf":
      return <FileText size={size} aria-hidden="true" />;
    case "av":
      return <FileVideo size={size} aria-hidden="true" />;
    case "markdown":
      return <FileText size={size} aria-hidden="true" />;
    case "text":
      return <FileText size={size} aria-hidden="true" />;
    case "code":
      return <FileText size={size} aria-hidden="true" />;
    case "data":
      return <FileText size={size} aria-hidden="true" />;
    default:
      return <File size={size} aria-hidden="true" />;
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface SourcePreviewProps {
  /**
   * Selected source path (relative to the active root).
   * null = no selection.
   */
  path: string | null;
  /**
   * Which root to fetch from: "sources" (default, raw/sources/) or "wiki" (wiki/).
   * Threaded from SourcesView so that wiki-tab files preview via the correct endpoint.
   */
  root?: SourceRoot;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function SourcePreview({ path, root = "sources" }: SourcePreviewProps) {
  const { t } = useTranslation();

  const selectPage = useGraphStore(selectSelectPage);
  const setActiveSection = useGraphStore(selectSetActiveSection);

  const [content, setContent] = useState<SourceContentResponse | null>(null);
  const [derivedPages, setDerivedPages] = useState<SourceDerivedPage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch content + derived-pages whenever path or root changes.
  // AbortController guards stale responses from in-flight requests.
  useEffect(() => {
    if (!path) {
      setContent(null);
      setDerivedPages([]);
      setError(null);
      return;
    }

    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    // Wiki root: derived-pages are not applicable (wiki pages ARE the wiki).
    // We still call getSourceDerivedPages for sources root to show page links.
    const derivedPagesPromise =
      root === "wiki"
        ? Promise.resolve([] as SourceDerivedPage[])
        : getSourceDerivedPages(path, ctrl.signal);

    Promise.all([getSourceContent(path, ctrl.signal, root), derivedPagesPromise])
      .then(([c, dp]) => {
        setContent(c);
        setDerivedPages(dp);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });

    return () => ctrl.abort();
  }, [path, root]);

  // Navigate to a derived wiki page
  const handleDerivedPageClick = useCallback(
    (pageId: string) => {
      selectPage(pageId, "tree");
      setActiveSection("pages");
    },
    [selectPage, setActiveSection],
  );

  // Empty state
  if (!path) {
    return (
      <div data-testid="source-preview" style={EMPTY_CONTAINER_STYLE}>
        <File
          size={32}
          aria-hidden="true"
          style={{ color: "var(--syn-text-dim)", marginBottom: 8 }}
        />
        <span style={{ color: "var(--syn-text-dim)", fontSize: 13 }}>{t("sources.empty")}</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div data-testid="source-preview" style={EMPTY_CONTAINER_STYLE}>
        <span style={{ color: "var(--syn-text-dim)", fontSize: 13 }}>{t("common.loading")}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="source-preview" style={EMPTY_CONTAINER_STYLE}>
        <span style={{ color: "var(--syn-red)", fontSize: 13 }}>{error}</span>
      </div>
    );
  }

  if (!content) return null;

  const rawUrl = sourceRawUrl(content.path, root);

  return (
    <div data-testid="source-preview" style={CONTAINER_STYLE}>
      {/* ── Header ── */}
      <PreviewHeader
        content={content}
        derivedPages={derivedPages}
        onDerivedPageClick={handleDerivedPageClick}
      />

      {/* ── Body ── */}
      <div style={BODY_STYLE}>
        <PreviewBody content={content} rawUrl={rawUrl} />
      </div>
    </div>
  );
}

// ─── Header ───────────────────────────────────────────────────────────────────

interface PreviewHeaderProps {
  content: SourceContentResponse;
  derivedPages: SourceDerivedPage[];
  onDerivedPageClick: (id: string) => void;
}

function PreviewHeader({ content, derivedPages, onDerivedPageClick }: PreviewHeaderProps) {
  const { t } = useTranslation();
  return (
    <div style={HEADER_STYLE}>
      {/* Filename + category */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <span style={{ color: "var(--syn-text-muted)", flexShrink: 0 }}>
          {categoryIcon(content.category, 16)}
        </span>
        <span
          style={{
            fontWeight: 600,
            fontSize: 14,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
          }}
          title={content.path}
        >
          {content.name}
        </span>
        <span style={TYPE_BADGE_STYLE}>{content.category}</span>
      </div>

      {/* Size */}
      <span style={{ fontSize: 11, color: "var(--syn-text-dim)", marginTop: 2 }}>
        {formatBytes(content.size_bytes)}
      </span>

      {/* Ingested badge */}
      {content.ingested ? (
        <span data-testid="source-ingested-badge" style={INGESTED_BADGE_STYLE}>
          {t("sources.ingested")} · {derivedPages.length} {t("sources.derivedPages")}
        </span>
      ) : (
        <span data-testid="source-ingested-badge" style={NOT_INGESTED_BADGE_STYLE}>
          {t("sources.notIngested")}
        </span>
      )}

      {/* Derived page links */}
      {derivedPages.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
          {derivedPages.map((dp) => (
            <button
              key={dp.id}
              onClick={() => onDerivedPageClick(dp.id)}
              style={DERIVED_PAGE_LINK_STYLE}
              title={dp.file_path}
            >
              {dp.title ?? dp.file_path}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Body dispatcher ──────────────────────────────────────────────────────────

interface PreviewBodyProps {
  content: SourceContentResponse;
  rawUrl: string;
}

function PreviewBody({ content, rawUrl }: PreviewBodyProps) {
  const { t } = useTranslation();

  switch (content.category) {
    case "image":
      return (
        <img
          data-testid="source-preview-image"
          src={rawUrl}
          alt={content.name}
          style={{
            maxWidth: "100%",
            maxHeight: "70vh",
            objectFit: "contain",
            display: "block",
            margin: "0 auto",
          }}
        />
      );

    case "pdf":
      return (
        <embed
          src={rawUrl}
          type="application/pdf"
          style={{ width: "100%", height: "70vh", border: "none" }}
          title={content.name}
        />
      );

    case "av": {
      // Audio vs video — crude heuristic: audio/* extensions lack video track
      const isAudio = /\.(mp3|wav|ogg|flac|aac|m4a)$/i.test(content.name);
      return isAudio ? (
        <audio controls src={rawUrl} style={{ width: "100%", marginTop: 8 }}>
          {content.name}
        </audio>
      ) : (
        <video
          controls
          src={rawUrl}
          style={{ maxWidth: "100%", maxHeight: "60vh", display: "block", margin: "0 auto" }}
        >
          {content.name}
        </video>
      );
    }

    case "markdown": {
      const html = content.text !== undefined ? renderMarkdown(content.text) : "";
      return (
        <div
          data-testid="source-preview-text"
          className="note-view__body"
          dangerouslySetInnerHTML={{ __html: html }}
          style={{ padding: "0 4px" }}
        />
      );
    }

    case "text":
    case "code":
    case "data":
      return (
        <pre data-testid="source-preview-text" style={PRE_STYLE}>
          {content.text ?? ""}
        </pre>
      );

    default:
      // other / no text
      return (
        <div style={NO_PREVIEW_STYLE}>
          <File
            size={32}
            aria-hidden="true"
            style={{ color: "var(--syn-text-dim)", marginBottom: 8 }}
          />
          <span style={{ color: "var(--syn-text-dim)", fontSize: 13, marginBottom: 12 }}>
            {t("sources.noPreview")}
          </span>
          <a href={rawUrl} target="_blank" rel="noopener noreferrer" style={OPEN_RAW_LINK_STYLE}>
            <Download size={13} aria-hidden="true" />
            {t("sources.file")}
          </a>
        </div>
      );
  }
}

// ─── Inline styles ────────────────────────────────────────────────────────────

const CONTAINER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  overflow: "hidden",
  background: "var(--syn-bg)",
};

const EMPTY_CONTAINER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  gap: 4,
  background: "var(--syn-bg-soft)",
};

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "12px 16px",
  borderBottom: "1px solid var(--syn-border)",
  background: "var(--syn-bg-soft)",
  flexShrink: 0,
};

const BODY_STYLE: CSSProperties = {
  flex: 1,
  overflow: "auto",
  padding: 16,
};

const TYPE_BADGE_STYLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  padding: "2px 6px",
  borderRadius: "var(--syn-radius-sm, 4px)",
  background: "var(--syn-surface-sunken)",
  border: "1px solid var(--syn-border-subtle)",
  color: "var(--syn-text-dim)",
  flexShrink: 0,
};

const INGESTED_BADGE_STYLE: CSSProperties = {
  display: "inline-block",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--syn-green)",
  background: "color-mix(in srgb, var(--syn-green) 12%, var(--syn-mix-base) 88%)",
  border: "1px solid color-mix(in srgb, var(--syn-green) 30%, transparent)",
  borderRadius: "var(--syn-radius-sm)",
  padding: "2px 7px",
  width: "fit-content",
};

const NOT_INGESTED_BADGE_STYLE: CSSProperties = {
  display: "inline-block",
  fontSize: 11,
  color: "var(--syn-text-dim)",
  background: "var(--syn-surface-sunken)",
  border: "1px solid var(--syn-border-subtle)",
  borderRadius: "var(--syn-radius-sm, 4px)",
  padding: "2px 7px",
  width: "fit-content",
};

const DERIVED_PAGE_LINK_STYLE: CSSProperties = {
  fontSize: 11,
  color: "var(--syn-accent)",
  background: "var(--syn-accent-soft)",
  border: "none",
  borderRadius: "var(--syn-radius-sm, 4px)",
  padding: "2px 7px",
  cursor: "pointer",
  textDecoration: "none",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 200,
};

const PRE_STYLE: CSSProperties = {
  fontFamily: "var(--syn-font-mono)",
  fontSize: 12,
  lineHeight: 1.6,
  color: "var(--syn-text)",
  background: "var(--syn-surface-sunken)",
  border: "1px solid var(--syn-border-subtle)",
  borderRadius: "var(--syn-radius-md, 6px)",
  padding: 16,
  overflowX: "auto",
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const NO_PREVIEW_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  height: "50%",
  gap: 4,
};

const OPEN_RAW_LINK_STYLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 12,
  color: "var(--syn-accent)",
  textDecoration: "none",
  padding: "5px 12px",
  border: "1px solid var(--syn-accent)",
  borderRadius: "var(--syn-radius-md, 6px)",
};

export { SourcePreview as default };
