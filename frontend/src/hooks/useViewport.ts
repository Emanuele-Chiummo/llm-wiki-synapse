/**
 * useViewport.ts — viewport tier hook (ADR-0057 §2).
 *
 * Uses useSyncExternalStore over two matchMedia listeners:
 *   - No resize listeners, no per-pixel re-renders (I3).
 *   - Re-renders ONLY when the viewport crosses a breakpoint.
 *   - SSR/jsdom safe: guards window.matchMedia absence with "desktop" fallback.
 *
 * Returns: "mobile" | "tablet" | "desktop"
 */

import { useSyncExternalStore } from "react";
import { MOBILE_QUERY, TABLET_QUERY } from "../utils/viewport";

export type ViewportTier = "mobile" | "tablet" | "desktop";

// Stable server-side (and SSR/test fallback) snapshot — always "desktop".
function getServerSnapshot(): ViewportTier {
  return "desktop";
}

function getSnapshot(): ViewportTier {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "desktop";
  }
  if (window.matchMedia(MOBILE_QUERY).matches) return "mobile";
  if (window.matchMedia(TABLET_QUERY).matches) return "tablet";
  return "desktop";
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }
  const mqMobile = window.matchMedia(MOBILE_QUERY);
  const mqTablet = window.matchMedia(TABLET_QUERY);
  mqMobile.addEventListener("change", onChange);
  mqTablet.addEventListener("change", onChange);
  return () => {
    mqMobile.removeEventListener("change", onChange);
    mqTablet.removeEventListener("change", onChange);
  };
}

export function useViewport(): ViewportTier {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
