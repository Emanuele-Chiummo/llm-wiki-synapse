/**
 * TokenGate.tsx — web (non-Tauri) authentication overlay (ADR-0052 §4.5).
 *
 * Shown when: !isTauri() && authRequired in settingsStore
 * (i.e. apiFetch received a 401 response on a protected endpoint).
 *
 * The server URL is always known in the web build (same-origin / VITE_API_BASE),
 * so only the token field needs to be actionable. The URL field is read-only context.
 *
 * Flow:
 *   1. User pastes token into the password field.
 *   2. On submit: call setAuthToken(token); then probe GET /provider/config.
 *   3. If 200 → call onSuccess() (clears authRequired; app re-renders normally).
 *   4. If 401 again → show inline error, clear token from localStorage again.
 *
 * ADR-0052 Do-NOT:
 *   - token never in Zustand
 *   - Authorization header constructed only in base.ts (through apiFetch)
 *   - token never logged
 */

import { useState, useCallback, useRef, useEffect, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Eye, EyeOff } from "lucide-react";
import logoUrl from "../../assets/synapse-logo.svg";
import { setAuthToken, clearAuthToken, apiBase, apiFetch } from "../../api/base";

interface TokenGateProps {
  /** Called after a successful protected probe — signals the shell to remove the gate. */
  onSuccess: () => void;
}

const PROBE_TIMEOUT_MS = 6_000;

export function TokenGate({ onSuccess }: TokenGateProps) {
  const { t } = useTranslation();
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tokenInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Auto-focus token field on mount (the actionable control)
  useEffect(() => {
    tokenInputRef.current?.focus();
  }, []);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = token.trim();
      if (!trimmed) {
        setError(t("connect.errors.authRequired"));
        return;
      }

      setSubmitting(true);
      setError(null);

      // Persist the token BEFORE the probe so apiFetch sends it
      setAuthToken(trimmed);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const timeoutId = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);

      try {
        const res = await apiFetch(`${apiBase()}/provider/config`, {
          signal: controller.signal,
        });
        window.clearTimeout(timeoutId);

        if (res.status === 401) {
          clearAuthToken();
          setError(t("connect.errors.authRequired"));
          return;
        }
        if (!res.ok) {
          // Non-401 error means the token was accepted (or auth is off) but the
          // endpoint returned something else — treat as success (token is valid).
        }
        // Token accepted — signal gate removal
        onSuccess();
      } catch (err: unknown) {
        window.clearTimeout(timeoutId);
        clearAuthToken();
        if (err instanceof DOMException && err.name === "AbortError") {
          setError(t("connect.errors.unreachable"));
        } else {
          setError(t("connect.errors.unreachable"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [token, t, onSuccess],
  );

  return (
    <div
      data-testid="token-gate"
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
      <div style={{ width: "100%", maxWidth: 420, padding: "0 16px" }}>
        {/* Card */}
        <div
          style={{
            position: "relative",
            overflow: "hidden",
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

          {/* Heading */}
          <h1
            style={{
              fontSize: 22,
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
            {t("connect.tokenGateSubtitle")}
          </p>

          {/* Form */}
          <form
            onSubmit={(e) => { void handleSubmit(e); }}
            style={{ width: "100%", display: "flex", flexDirection: "column", gap: 12 }}
          >
            <label
              htmlFor="token-gate-token"
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--syn-text, #0f172a)",
              }}
            >
              {t("connect.tokenLabel")}
            </label>

            {/* Token field with show/hide toggle */}
            <div style={{ position: "relative" }}>
              <input
                ref={tokenInputRef}
                id="token-gate-token"
                type={showToken ? "text" : "password"}
                value={token}
                onChange={(e) => {
                  setToken(e.target.value);
                  setError(null);
                }}
                placeholder={t("connect.tokenPlaceholder")}
                disabled={submitting}
                autoComplete="current-password"
                style={{
                  width: "100%",
                  padding: "10px 40px 10px 14px",
                  fontSize: 14,
                  fontFamily:
                    "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
                  border: error
                    ? "1.5px solid var(--syn-red, #ef4444)"
                    : "1.5px solid var(--syn-border, #e2e8f0)",
                  borderRadius: 8,
                  background: "var(--syn-input-bg, #f8fafc)",
                  color: "var(--syn-text, #0f172a)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
              <button
                type="button"
                onClick={() => setShowToken((v) => !v)}
                aria-label={showToken ? t("connect.hideToken") : t("connect.showToken")}
                tabIndex={0}
                style={{
                  position: "absolute",
                  right: 10,
                  top: "50%",
                  transform: "translateY(-50%)",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 2,
                  color: "var(--syn-text-dim, #64748b)",
                  display: "flex",
                  alignItems: "center",
                }}
              >
                {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>

            {/* Error message */}
            {error !== null && (
              <p
                role="alert"
                data-testid="token-gate-error"
                style={{
                  fontSize: 13,
                  color: "var(--syn-red, #ef4444)",
                  margin: 0,
                }}
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting || token.trim().length === 0}
              className="syn-btn syn-btn--gradient"
              style={{
                marginTop: 4,
                width: "100%",
                padding: "11px 0",
                fontSize: 15,
                fontWeight: 700,
                letterSpacing: "-0.01em",
                borderRadius: 8,
                background: submitting ? "var(--syn-border)" : undefined,
                color: submitting ? "var(--syn-text-dim)" : "#ffffff",
                opacity: submitting ? 0.7 : 1,
              }}
            >
              {submitting ? t("connect.connecting") : t("connect.tokenSubmit")}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
