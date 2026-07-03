/**
 * VersionMismatchBanner.tsx — non-blocking dismissible banner shown when the
 * backend version is BEHIND the frontend build version [F15][F16][R12-3][ADR-0054 §6].
 *
 * Show condition (R12-3 AC-R12-3-5):
 *   - backendVersion is present AND !== undefined
 *   - backendVersion !== "dev" (local dev build → no banner)
 *   - backendVersion !== __APP_VERSION__ (mismatch)
 *
 * Dismiss: sessionStorage flag "synapse:versionBannerDismissed" — survives
 * component re-renders but not browser restarts (session-scoped per AC-R12-3-5).
 *
 * INVARIANT I3: no heavy work; reads two scalars and renders plain text.
 * No new poller — version is received from the existing /status poll via statusStore.
 */

import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { useStatusStore, selectBackendVersion } from "../../store/statusStore";

// ─── Constants ─────────────────────────────────────────────────────────────────

const DISMISS_KEY = "synapse:versionBannerDismissed";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function isMismatch(backendVersion: string | undefined, appVersion: string): boolean {
  if (!backendVersion) return false;
  if (backendVersion === "dev") return false;
  return backendVersion !== appVersion;
}

function getSessionDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

function setSessionDismissed(): void {
  try {
    sessionStorage.setItem(DISMISS_KEY, "1");
  } catch {
    // sessionStorage may be unavailable — non-fatal
  }
}

// ─── Module-level global declaration ─────────────────────────────────────────
// Injected by Vite define at build time (vite.config.ts). Declared here at
// module scope so TypeScript can see it without an inner `declare const` (which
// TS 5.x disallows inside function bodies with strict modifiers).

declare const __APP_VERSION__: string;

// ─── Component ────────────────────────────────────────────────────────────────

export function VersionMismatchBanner() {
  const { t } = useTranslation();
  const backendVersion = useStatusStore(selectBackendVersion);
  const [dismissed, setDismissed] = useState<boolean>(getSessionDismissed);

  const appVersion = typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";

  // If dismissed this session, also watch for a version change (new backend deployed
  // mid-session) — re-surface the banner for the new version.
  const [lastSeenVersion, setLastSeenVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (backendVersion && backendVersion !== lastSeenVersion) {
      setLastSeenVersion(backendVersion);
      // If the version changed, clear the session dismiss so the new version shows.
      if (dismissed && isMismatch(backendVersion, appVersion)) {
        const stillDismissed = getSessionDismissed();
        if (!stillDismissed) setDismissed(false);
      }
    }
  }, [backendVersion, lastSeenVersion, dismissed, appVersion]);

  if (dismissed) return null;
  if (!isMismatch(backendVersion, appVersion)) return null;

  const handleDismiss = () => {
    setSessionDismissed();
    setDismissed(true);
  };

  return (
    <div
      data-testid="version-mismatch-banner"
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "5px 14px",
        fontSize: 12,
        lineHeight: 1.4,
        color: "var(--syn-text-muted)",
        background: "color-mix(in srgb, var(--syn-accent) 6%, var(--syn-bg-soft) 94%)",
        borderBottom: "1px solid color-mix(in srgb, var(--syn-accent) 25%, var(--syn-border) 75%)",
        flexShrink: 0,
      }}
    >
      {/* Message with both version strings */}
      <span data-testid="version-mismatch-text" style={{ flex: 1, minWidth: 0 }}>
        {t("home.versionBanner.message", {
          backendVersion: backendVersion ?? "",
          appVersion,
        })}
      </span>

      {/* Deploy docs link */}
      <a
        href="/docs/DEPLOY.md#updating-synapse"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          fontSize: 11,
          color: "var(--syn-accent)",
          textDecoration: "none",
          flexShrink: 0,
        }}
      >
        {t("home.versionBanner.deployLink")}
      </a>

      {/* Dismiss */}
      <button
        data-testid="version-mismatch-dismiss"
        onClick={handleDismiss}
        aria-label={t("home.versionBanner.dismiss")}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 20,
          height: 20,
          border: "none",
          background: "transparent",
          color: "var(--syn-text-dim)",
          cursor: "pointer",
          padding: 0,
          flexShrink: 0,
          borderRadius: 3,
        }}
      >
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  );
}
