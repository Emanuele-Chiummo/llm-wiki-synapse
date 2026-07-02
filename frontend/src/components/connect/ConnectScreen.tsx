/**
 * ConnectScreen.tsx — first-launch backend-binding gate for the Tauri desktop app.
 *
 * Shown when: isTauri() && no serverUrl saved (ADR-0047 §2.3 / C3).
 *
 * Flow:
 *   1. User enters backend URL (e.g. http://truenas:8000).
 *   2. Validates scheme — rejects non-http(s) immediately (ADR-0047 §2.7.1).
 *   3. Probes GET {url}/status with ~6s AbortController timeout (ADR-0047 §2.7.2).
 *   4. On 2xx → calls store.setServerUrl(url) → gate disappears, app renders.
 *   5. On failure → shows i18n error, stays on gate (ADR-0047 §6 Do-NOT #4).
 *
 * Uses only --syn-* CSS variables (light theme, accent #2563eb).
 * No Tauri IPC/commands called here — isTauri() check is in AppShell (ADR-0047 §6 Do-NOT #5).
 */

import { useState, useCallback, useRef, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import logoUrl from "../../assets/synapse-logo.svg";
import { useSettingsStore, selectSetServerUrl } from "../../store/settingsStore";

const PROBE_TIMEOUT_MS = 6_000;

export function ConnectScreen() {
  const { t } = useTranslation();
  const storeSetServerUrl = useSettingsStore(selectSetServerUrl);

  const [url, setUrl] = useState("http://");
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      setError(null);

      const trimmed = url.trim().replace(/\/+$/, "");

      // --- scheme validation (ADR-0047 §2.7.1) ---
      let parsed: URL;
      try {
        parsed = new URL(trimmed);
      } catch {
        setError(t("connect.errors.invalidUrl"));
        return;
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        setError(t("connect.errors.scheme"));
        return;
      }

      // --- probe GET {url}/status (ADR-0047 §2.7.2) ---
      setConnecting(true);
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const timeoutId = window.setTimeout(() => {
        controller.abort();
      }, PROBE_TIMEOUT_MS);

      try {
        const probeUrl = `${trimmed}/status`;
        const res = await fetch(probeUrl, { signal: controller.signal });
        window.clearTimeout(timeoutId);

        if (!res.ok) {
          setError(t("connect.errors.status", { status: String(res.status) }));
          return;
        }

        // --- 2xx: persist and let app render ---
        // storeSetServerUrl delegates to base.ts setServerUrl (validates + stores),
        // then updates the Zustand store → AppShell re-renders without the gate.
        storeSetServerUrl(trimmed);
      } catch (err: unknown) {
        window.clearTimeout(timeoutId);
        if (err instanceof DOMException && err.name === "AbortError") {
          setError(t("connect.errors.unreachable"));
        } else {
          setError(t("connect.errors.unreachable"));
        }
      } finally {
        setConnecting(false);
      }
    },
    [url, t, storeSetServerUrl],
  );

  return (
    <div
      data-testid="connect-screen"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100vw",
        height: "100vh",
        background: "var(--syn-bg, #f8fafc)",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 420,
          padding: "0 16px",
        }}
      >
        {/* Card */}
        <div
          style={{
            background: "var(--syn-surface, #ffffff)",
            borderRadius: 16,
            padding: "40px 36px 36px",
            boxShadow:
              "var(--syn-shadow-pop, 0 8px 32px 0 rgba(37,99,235,0.10), 0 2px 8px 0 rgba(0,0,0,0.08))",
            border: "1px solid var(--syn-border, #e2e8f0)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 0,
          }}
        >
          {/* Gradient accent bar */}
          <div
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: 3,
              borderRadius: "16px 16px 0 0",
              background: "linear-gradient(90deg, #2563eb 0%, #8250df 100%)",
              pointerEvents: "none",
            }}
          />

          {/* Logo */}
          <img
            src={logoUrl}
            alt="Synapse"
            width={64}
            height={64}
            style={{ marginBottom: 16 }}
          />

          {/* App name */}
          <h1
            style={{
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: "-0.03em",
              color: "var(--syn-text, #0f172a)",
              marginBottom: 6,
              textAlign: "center",
            }}
          >
            {t("connect.title")}
          </h1>

          {/* Subtitle */}
          <p
            style={{
              fontSize: 14,
              color: "var(--syn-text-dim, #64748b)",
              textAlign: "center",
              marginBottom: 32,
              lineHeight: 1.5,
            }}
          >
            {t("connect.subtitle")}
          </p>

          {/* Form */}
          <form
            onSubmit={(e) => { void handleSubmit(e); }}
            style={{ width: "100%", display: "flex", flexDirection: "column", gap: 12 }}
          >
            <label
              htmlFor="connect-url"
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--syn-text, #0f172a)",
              }}
            >
              {t("connect.urlLabel")}
            </label>

            <input
              id="connect-url"
              type="text"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setError(null);
              }}
              placeholder={t("connect.placeholder")}
              disabled={connecting}
              autoFocus
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              style={{
                width: "100%",
                padding: "10px 14px",
                fontSize: 14,
                fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
                border: error
                  ? "1.5px solid #ef4444"
                  : "1.5px solid var(--syn-border, #e2e8f0)",
                borderRadius: 8,
                background: "var(--syn-input-bg, #f8fafc)",
                color: "var(--syn-text, #0f172a)",
                outline: "none",
                transition: "border-color 0.15s",
              }}
            />

            {/* Error message */}
            {error !== null && (
              <p
                role="alert"
                data-testid="connect-error"
                style={{
                  fontSize: 13,
                  color: "#ef4444",
                  margin: 0,
                }}
              >
                {error}
              </p>
            )}

            {/* Connect button */}
            <button
              type="submit"
              disabled={connecting || url.trim().length === 0}
              style={{
                marginTop: 4,
                width: "100%",
                padding: "11px 0",
                fontSize: 15,
                fontWeight: 700,
                letterSpacing: "-0.01em",
                border: "none",
                borderRadius: 8,
                cursor: connecting ? "wait" : "pointer",
                background: connecting
                  ? "var(--syn-border, #e2e8f0)"
                  : "linear-gradient(135deg, #2563eb 0%, #5b4de0 100%)",
                color: connecting ? "var(--syn-text-dim, #64748b)" : "#ffffff",
                transition: "opacity 0.15s",
                opacity: connecting ? 0.7 : 1,
              }}
            >
              {connecting ? t("connect.connecting") : t("connect.connect")}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
