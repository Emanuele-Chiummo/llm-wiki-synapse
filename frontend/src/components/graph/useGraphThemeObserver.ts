/**
 * useGraphThemeObserver.ts — Custom hook: watch data-theme attribute on <html> and
 * re-read sigma render properties when the resolved theme changes.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. ADR-0048 §T1.
 *
 * INVARIANT I2: only reads CSS vars for render-property updates (color/label).
 *   No layout or coordinate mutation whatsoever.
 */

import { useEffect } from "react";
import { readSigmaThemeColors } from "./graphViewerShared";
import type { SigmaThemeColors } from "./graphViewerShared";
import type { GraphTheme } from "../graphPalette";

/**
 * Observes data-theme on <html>; on change, reads the new CSS vars and calls the
 * provided setters so sigma can be re-instantiated with the correct colors (ADR-0048 §T1).
 * This is a render-property update ONLY — no layout/coords touched (I2).
 */
export function useGraphThemeObserver(
  setSigmaThemeColors: (colors: SigmaThemeColors) => void,
  setGraphTheme: (theme: GraphTheme) => void,
): void {
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setSigmaThemeColors(readSigmaThemeColors());
      setGraphTheme(
        document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light",
      );
    });
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [setSigmaThemeColors, setGraphTheme]);
}
