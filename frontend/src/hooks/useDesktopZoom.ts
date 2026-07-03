/**
 * useDesktopZoom.ts — Cmd/Ctrl +/-/0 zoom control for the Tauri desktop shell (ADR-0048 §T4b).
 *
 * Adjusts document.documentElement.style.zoom between 0.8 and 1.4 (step 0.1).
 * Persists the value to localStorage["synapse.zoom"]; restores it on load.
 *
 * ACTIVE ONLY WHEN isTauri() — CSS zoom is deliberately chosen over a root
 * font-size scale because the app styles use px units, not rem. CSS zoom works
 * in the target WKWebView/WebView2 webviews (and Chromium browsers).
 * In a standard browser isTauri() is false and this hook is entirely inert.
 *
 * ADR-0048 §T4b: "+"/"-"/"0" with Cmd/Ctrl are safe in inputs (they do not conflict
 * with common text-editing shortcuts), so the "ignore while typing" guard from T2
 * is NOT applied here.
 *
 * INVARIANT I3: the keydown listener is registered once on mount, never per token.
 */

import { useEffect } from "react";
import { isTauri } from "../api/base";

// ─── Constants ────────────────────────────────────────────────────────────────

const LS_ZOOM = "synapse.zoom";
const ZOOM_MIN = 0.8;
const ZOOM_MAX = 1.4;
const ZOOM_STEP = 0.1;
const ZOOM_DEFAULT = 1.0;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function clampZoom(value: number): number {
  // Round to one decimal to avoid floating-point drift (0.8999... etc.)
  const rounded = Math.round(value * 10) / 10;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, rounded));
}

function readPersistedZoom(): number {
  try {
    const raw = localStorage.getItem(LS_ZOOM);
    if (raw === null) return ZOOM_DEFAULT;
    const parsed = parseFloat(raw);
    if (isNaN(parsed)) return ZOOM_DEFAULT;
    return clampZoom(parsed);
  } catch {
    return ZOOM_DEFAULT;
  }
}

function applyZoom(value: number): void {
  try {
    document.documentElement.style.zoom = value === ZOOM_DEFAULT ? "" : String(value);
  } catch {
    // ignore — no DOM in test env
  }
}

function persistZoom(value: number): void {
  try {
    if (value === ZOOM_DEFAULT) {
      localStorage.removeItem(LS_ZOOM);
    } else {
      localStorage.setItem(LS_ZOOM, String(value));
    }
  } catch {
    // ignore — storage unavailable
  }
}

function getCurrentZoom(): number {
  try {
    const raw = document.documentElement.style.zoom;
    if (!raw || raw === "") return ZOOM_DEFAULT;
    const parsed = parseFloat(raw);
    return isNaN(parsed) ? ZOOM_DEFAULT : parsed;
  } catch {
    return ZOOM_DEFAULT;
  }
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useDesktopZoom — register Cmd/Ctrl +/-/0 zoom shortcuts in Tauri shell.
 *
 * Wire this once in a component that is always mounted (e.g. Header).
 * The hook is a no-op when not in Tauri.
 */
export function useDesktopZoom(): void {
  useEffect(() => {
    // Only active in Tauri (ADR-0048 §T4b)
    if (!isTauri()) return;

    // Restore persisted zoom on startup
    const persisted = readPersistedZoom();
    applyZoom(persisted);

    const handleKeyDown = (e: KeyboardEvent): void => {
      const isMod = e.ctrlKey || e.metaKey;
      if (!isMod) return;

      // Cmd/Ctrl+= or Cmd/Ctrl++ → zoom in
      if (e.key === "=" || e.key === "+") {
        e.preventDefault();
        const next = clampZoom(getCurrentZoom() + ZOOM_STEP);
        applyZoom(next);
        persistZoom(next);
        return;
      }

      // Cmd/Ctrl+- → zoom out
      if (e.key === "-") {
        e.preventDefault();
        const next = clampZoom(getCurrentZoom() - ZOOM_STEP);
        applyZoom(next);
        persistZoom(next);
        return;
      }

      // Cmd/Ctrl+0 → reset to default
      if (e.key === "0") {
        e.preventDefault();
        applyZoom(ZOOM_DEFAULT);
        persistZoom(ZOOM_DEFAULT);
        return;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);
}
