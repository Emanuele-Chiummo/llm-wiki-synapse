/**
 * ProjectLauncher.tsx — multi-vault launcher (v1.5 P2 slice 4, ADR-0067).
 *
 * Mirrors LLM Wiki's ⇄ Project Launcher: title + New Project / Open Project + Recent Projects.
 * Each project is a vault folder. Activating one switches the running service's active vault
 * (POST /projects/{id}/activate); on success we hard-reload so every store re-reads the new
 * vault (the active_vault_epoch changed server-side).
 *
 * Folder paths are SERVER-SIDE (Synapse is self-hosted) → text inputs, not a browser file picker.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { FolderPlus, FolderOpen, Check, Copy } from "lucide-react";
import {
  fetchProjects,
  openProject,
  activateProject,
  type Project,
} from "../../api/projectsClient";
import { ErrorState } from "../common/ErrorState";
import { NewProjectWizard } from "./NewProjectWizard";

const WRAP: CSSProperties = {
  height: "100%",
  overflowY: "auto",
  display: "flex",
  justifyContent: "center",
  padding: "48px 24px",
};

const INNER: CSSProperties = { width: "100%", maxWidth: 640 };

const CARD: CSSProperties = {
  border: "1px solid var(--syn-border)",
  borderRadius: 10,
  background: "var(--syn-surface)",
  padding: 16,
  marginBottom: 16,
};

const INPUT: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  background: "var(--syn-bg)",
  color: "var(--syn-text)",
  fontSize: 13,
  boxSizing: "border-box",
};

// Button constants consolidated into CSS classes (F1 slice):
//   BTN       → .syn-btn.syn-btn--primary (filled accent, white text)
//   GHOST_BTN → .syn-btn.syn-btn--secondary (ghost with border, muted text)

const ROW: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "10px 12px",
  border: "1px solid var(--syn-border)",
  borderRadius: 8,
  marginBottom: 8,
  cursor: "pointer",
  background: "var(--syn-surface)",
};

export function ProjectLauncher() {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [showNewWizard, setShowNewWizard] = useState(false);
  const mounted = useRef(true);

  // "Open project folder": Synapse is self-hosted (the vault often lives on a remote server), so
  // the universal action is to copy the server-side path for the user to open in their own file
  // manager. The Tauri desktop build can wire a real reveal-in-Finder later (TODO).
  const handleOpenFolder = useCallback(async (id: string, path: string) => {
    try {
      await navigator.clipboard.writeText(path);
    } catch {
      // clipboard may be unavailable (insecure context) — still flash feedback
    }
    if (!mounted.current) return;
    setCopiedId(id);
    window.setTimeout(() => {
      if (mounted.current) setCopiedId((c) => (c === id ? null : c));
    }, 1500);
  }, []);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchProjects();
      if (!mounted.current) return;
      setProjects(data.projects);
      setActiveId(data.active_id);
    } catch (err) {
      if (mounted.current) setError(err);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  const handleActivate = useCallback(async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      await activateProject(id);
      // The active vault changed server-side — reload so every store re-reads it.
      window.location.reload();
    } catch (err) {
      setBusyId(null);
      setError(err);
    }
  }, []);

  const handleOpen = useCallback(async () => {
    if (!openPath.trim()) return;
    setError(null);
    try {
      await openProject(openPath.trim());
      setOpenPath("");
      await refresh();
    } catch (err) {
      setError(err);
    }
  }, [openPath, refresh]);

  return (
    <>
      {showNewWizard && <NewProjectWizard onClose={() => setShowNewWizard(false)} />}
      <div style={WRAP} data-testid="project-launcher">
        <div style={INNER}>
          <h1
            style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.02em", margin: "0 0 4px" }}
          >
            {t("launcher.title")}
          </h1>
          <p style={{ color: "var(--syn-text-muted)", margin: "0 0 24px", fontSize: 14 }}>
            {t("launcher.subtitle")}
          </p>

          {error != null && (
            <div data-testid="launcher-error" style={{ marginBottom: 16 }}>
              <ErrorState
                title={t("projects.loadError")}
                onRetry={() => {
                  void refresh();
                }}
                error={error}
              />
            </div>
          )}

          {/* New Project */}
          <div style={CARD}>
            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
              {t("launcher.newTitle")}
            </div>
            <button
              type="button"
              data-testid="launcher-new-project-btn"
              className="syn-btn syn-btn--primary"
              style={{ fontSize: 13, padding: "8px 14px" }}
              onClick={() => setShowNewWizard(true)}
            >
              <FolderPlus size={14} aria-hidden="true" />
              {t("wizard.newProjectTitle")}
            </button>
          </div>

          {/* Open Project */}
          <div style={CARD}>
            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
              {t("launcher.openTitle")}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                data-testid="launcher-open-path"
                style={INPUT}
                placeholder={t("launcher.pathPlaceholder")}
                value={openPath}
                onChange={(e) => setOpenPath(e.target.value)}
              />
              <button
                type="button"
                data-testid="launcher-open"
                className="syn-btn syn-btn--secondary"
                style={{ fontSize: 13, padding: "8px 14px", flexShrink: 0 }}
                onClick={() => void handleOpen()}
                disabled={!openPath.trim()}
              >
                <FolderOpen size={14} aria-hidden="true" />
                {t("launcher.open")}
              </button>
            </div>
          </div>

          {/* Recent Projects */}
          <div
            style={{
              fontWeight: 600,
              margin: "20px 0 10px",
              fontSize: 13,
              color: "var(--syn-text-muted)",
            }}
          >
            {t("launcher.recent")}
          </div>
          {projects.length === 0 ? (
            <div style={{ color: "var(--syn-text-dim)", fontSize: 13 }}>{t("launcher.empty")}</div>
          ) : (
            projects.map((p) => {
              const isActive = p.id === activeId;
              return (
                <div
                  key={p.id}
                  data-testid="launcher-project-row"
                  style={{
                    ...ROW,
                    borderColor: isActive ? "var(--syn-accent)" : "var(--syn-border)",
                    opacity: busyId && busyId !== p.id ? 0.6 : 1,
                  }}
                  onClick={() => !isActive && void handleActivate(p.id)}
                  role="button"
                  tabIndex={0}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 14, color: "var(--syn-text)" }}>
                      {p.name}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--syn-text-dim)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {p.path}
                    </div>
                  </div>
                  <button
                    type="button"
                    data-testid="launcher-open-folder"
                    title={t("launcher.openFolder")}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleOpenFolder(p.id, p.path);
                    }}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "4px 8px",
                      border: "1px solid var(--syn-border)",
                      background: "var(--syn-surface)",
                      color: "var(--syn-text-muted)",
                      borderRadius: 5,
                      fontSize: 12,
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    {copiedId === p.id ? (
                      <>
                        <Check size={12} aria-hidden="true" />
                        {t("launcher.pathCopied")}
                      </>
                    ) : (
                      <>
                        <Copy size={12} aria-hidden="true" />
                        {t("launcher.openFolder")}
                      </>
                    )}
                  </button>
                  {isActive ? (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        fontSize: 12,
                        fontWeight: 600,
                        color: "var(--syn-accent)",
                        flexShrink: 0,
                      }}
                    >
                      <Check size={13} aria-hidden="true" />
                      {t("launcher.active")}
                    </span>
                  ) : (
                    <span style={{ fontSize: 12, color: "var(--syn-text-muted)", flexShrink: 0 }}>
                      {busyId === p.id ? t("launcher.switching") : t("launcher.switch")}
                    </span>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </>
  );
}
