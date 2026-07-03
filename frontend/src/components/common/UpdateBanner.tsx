/**
 * UpdateBanner.tsx — slim dismissible update-available banner (ADR-0049 §U4).
 *
 * Rendered below the Header when useDesktopUpdater() exposes a non-null update.
 * Uses --syn-* CSS vars for theming consistency.
 *
 * Behaviour:
 *   - "Later" (desktop.update.later): dismiss() — session-scoped, no persistence.
 *     The banner re-appears on the next app start (ADR-0049 §U4 posture).
 *   - "Update now" (desktop.update.now): startInstall() →
 *       while installing: shows desktop.update.installing progress text.
 *       on error: shows desktop.update.error inline; buttons remain (retry possible).
 *       on success: relaunch() is called by the hook; banner unmounts with the app.
 *
 * i18n keys: desktop.update.available ({{version}}), desktop.update.now,
 *   desktop.update.later, desktop.update.installing, desktop.update.error.
 *
 * INVARIANT I3: no markdown/LaTeX parsed here; plain text only.
 */

import { useTranslation } from "react-i18next";
import type { DesktopUpdaterState } from "../../hooks/useDesktopUpdater";

// ─── Props ────────────────────────────────────────────────────────────────────

interface UpdateBannerProps {
  state: DesktopUpdaterState;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function UpdateBanner({ state }: UpdateBannerProps) {
  const { t } = useTranslation();
  const { update, installing, installError, dismiss, startInstall } = state;

  // Nothing to show when no update is available (dismissed or not yet checked).
  if (update === null) return null;

  return (
    <div
      data-testid="update-banner"
      role="status"
      aria-live="polite"
      style={{
        // Gradient accent border on the bottom edge (visual indicator, not a full border).
        borderBottom: "2px solid transparent",
        backgroundImage:
          "linear-gradient(var(--syn-surface, #1e1e2e), var(--syn-surface, #1e1e2e))," +
          "linear-gradient(90deg, var(--syn-accent, #7c3aed), var(--syn-accent2, #2563eb))",
        backgroundOrigin: "border-box",
        backgroundClip: "padding-box, border-box",
        // Layout
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "6px 16px",
        fontSize: "13px",
        lineHeight: "1.4",
        color: "var(--syn-text, #e0e0e0)",
        background: "var(--syn-surface, #1e1e2e)",
        flexShrink: 0,
      }}
    >
      {/* ── Message ─────────────────────────────────────────────────────────── */}
      <span style={{ flex: 1, minWidth: 0 }}>
        {installing ? (
          <span data-testid="update-installing-text">
            {t("desktop.update.installing")}
          </span>
        ) : installError !== null ? (
          <span data-testid="update-error-text" style={{ color: "var(--syn-error, #f87171)" }}>
            {t("desktop.update.error")}
          </span>
        ) : (
          <span data-testid="update-available-text">
            {t("desktop.update.available", { version: update.version })}
          </span>
        )}
      </span>

      {/* ── Actions ─────────────────────────────────────────────────────────── */}
      {!installing && (
        <>
          {/* "Update now" — triggers download + install + relaunch */}
          <button
            data-testid="update-now-btn"
            onClick={() => void startInstall()}
            disabled={installing}
            style={{
              padding: "3px 12px",
              borderRadius: "4px",
              border: "1px solid var(--syn-accent, #7c3aed)",
              background: "var(--syn-accent, #7c3aed)",
              color: "#fff",
              fontSize: "12px",
              cursor: "pointer",
              fontWeight: 500,
              whiteSpace: "nowrap",
            }}
          >
            {t("desktop.update.now")}
          </button>

          {/* "Later" — dismiss for this session */}
          <button
            data-testid="update-later-btn"
            onClick={dismiss}
            style={{
              padding: "3px 10px",
              borderRadius: "4px",
              border: "1px solid var(--syn-border, #333)",
              background: "transparent",
              color: "var(--syn-text-muted, #888)",
              fontSize: "12px",
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {t("desktop.update.later")}
          </button>
        </>
      )}
    </div>
  );
}
