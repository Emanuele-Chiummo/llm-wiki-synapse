/**
 * graphViewerShared.ts — helpers shared between GraphViewer.tsx and its
 * extracted subcomponents (components/graph/*). Move-only extraction from
 * GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 *
 * INVARIANT I2: readSigmaThemeColors / draw helpers below are RENDER-ONLY.
 *   They never compute node/edge positions — those come exclusively from the
 *   server (GET /graph precomputed FA2 coords).
 */

import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import {
  GRAPH_PAGE_TYPE_ORDER,
  PAGE_TYPE_VISUALS,
  pageTypeGraphColor,
} from "../../utils/pageTypeVisuals";

// ─── Reduced-motion detection ─────────────────────────────────────────────────

export const reducedMotion: boolean =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── Resolved theme helpers (ADR-0048 §T1) ───────────────────────────────────
// Read render-only sigma properties from resolved CSS custom properties.
// sigma.js cannot resolve CSS vars at canvas draw time, so we read them via
// getComputedStyle on the document root and pass concrete values to sigma.
// graphPalette.ts (node type/community palette) is NOT touched — ADR-0015 §CVD-SAFE.

export interface SigmaThemeColors {
  /** Stage background (sigma container bg) — --syn-bg resolved value */
  bg: string;
  /** Label text color — --syn-text resolved value */
  labelColor: string;
  /** Halo stroke (contrasting surface behind labels) — #ffffff light, #0d1117 dark */
  haloColor: string;
  /** Hover ring stroke — --syn-text resolved value */
  hoverRingColor: string;
}

export function readSigmaThemeColors(): SigmaThemeColors {
  try {
    const style = getComputedStyle(document.documentElement);
    const bg = style.getPropertyValue("--syn-bg").trim() || "#ffffff";
    const labelColor = style.getPropertyValue("--syn-text").trim() || "#1f2328";
    // halo: use bg as the contrasting backing stroke so it's visible on the canvas
    const haloColor = bg;
    return { bg, labelColor, haloColor, hoverRingColor: labelColor };
  } catch {
    return {
      bg: "#ffffff",
      labelColor: "#1f2328",
      haloColor: "#ffffff",
      hoverRingColor: "#1f2328",
    };
  }
}

// ─── CVD-safe type palette (spec §CVD-SAFE) ──────────────────────────────────
// Color alone MUST NOT be the only differentiator (WCAG 1.4.1).
// Redundant encoding: legend shows swatch + type NAME; tooltip also shows type text.
//
// Node type palette — aligned to llm_wiki 0.6.0 (Tailwind -400 shades), used for BOTH
// themes exactly as the reference does. sigma.js cannot resolve CSS custom properties at
// canvas draw time, so concrete hex strings are required here. These intentionally do NOT
// track the --syn-type-* badge tokens (which stay tuned for text contrast in lint/wiki
// badges); the GRAPH mirrors the reference palette. Redundant encoding (legend swatch +
// type name + tooltip text) keeps it CVD-safe (WCAG 1.4.1).
//   entity #60a5fa · concept #c084fc · source #fb923c · synthesis #f87171
//   comparison #2dd4bf (teal) · query #4ade80 · overview #facc15 · other #94a3b8 (slate-400)

export const TYPE_COLORS: Record<string, string> = Object.fromEntries(
  GRAPH_PAGE_TYPE_ORDER.map((type) => [type, PAGE_TYPE_VISUALS[type].graphColor]),
);

export const DEFAULT_NODE_COLOR = PAGE_TYPE_VISUALS.other.graphColor;

export function colorForType(type: string | null): string {
  return pageTypeGraphColor(type);
}

/**
 * Deepen a hex color by mixing it 30% toward black (#000000).
 * Used in nodeReducer to make neighbor nodes pop more visibly on the light background
 * against the washed-out dimmed nodes.
 * Handles both 3-char (#rgb) and 6-char (#rrggbb) hex; falls back to input on parse error.
 */
export function deepenColor(hex: string): string {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  const full = clean.length === 3 ? clean.replace(/./g, (c) => c + c) : clean;
  if (full.length !== 6) return hex;

  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);

  if (isNaN(r) || isNaN(g) || isNaN(b)) return hex;

  const mix = 0.3; // 30% toward black
  const dr = Math.round(r * (1 - mix));
  const dg = Math.round(g * (1 - mix));
  const db = Math.round(b * (1 - mix));

  return `#${dr.toString(16).padStart(2, "0")}${dg.toString(16).padStart(2, "0")}${db.toString(16).padStart(2, "0")}`;
}

// ─── Halo label drawer (accessible, AAA contrast) ────────────────────────────
// sigma v3 has no built-in halo; we override defaultDrawNodeLabel.
// Halo color = bg (canvas background for readability); fill = --syn-text.
// Colors are read once per sigma instantiation via readSigmaThemeColors().

export type LabelDrawData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;

/**
 * Build a halo label drawer that uses the provided theme colors.
 * Called once per sigma instantiation, not per frame.
 */
export function makeDrawHaloNodeLabel(themeColors: SigmaThemeColors) {
  return function drawHaloNodeLabel(
    context: CanvasRenderingContext2D,
    data: LabelDrawData,
    settings: Settings<Attributes, Attributes, Attributes>,
  ): void {
    if (!data.label) return;

    const size = settings.labelSize;
    const font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
    const x = data.x;
    const y = data.y - data.size - 3;

    context.font = font;
    context.textAlign = "center";
    context.textBaseline = "bottom";

    // Halo stroke (bg color) — improves readability on the graph canvas background
    context.strokeStyle = themeColors.haloColor;
    context.lineWidth = 3;
    context.lineJoin = "round";
    context.strokeText(data.label, x, y);

    // Label fill (--syn-text resolved value)
    context.fillStyle = themeColors.labelColor;
    context.fillText(data.label, x, y);
  };
}

/**
 * Build a pill-style hover drawer that uses the provided theme colors.
 * Draws a highlight ring around the hovered node, then a dark rounded-rect
 * "pill" label (reference: nashsu/llm_wiki 0.6.0 pill style).
 *
 * Pill uses rgba(15,20,30,0.88) background with a light border and #f1f5f9 text
 * so it's legible on BOTH light and dark canvas themes without needing CSS vars
 * (sigma draws on an HTMLCanvasRenderingContext2D — no CSS resolution at draw time).
 * The hover ring uses themeColors.hoverRingColor for theme-aware contrast.
 *
 * roundRect: available Chrome 99+, Firefox 112+, Safari 15.4+ (Tauri v2 = WebView2,
 * always Chrome-based). Manual fallback for older Safari.
 */
export function makeDrawHaloNodeHover(themeColors: SigmaThemeColors) {
  return function drawHaloNodeHover(
    context: CanvasRenderingContext2D,
    data: LabelDrawData,
    settings: Settings<Attributes, Attributes, Attributes>,
  ): void {
    // Hover ring around the node (render-only; no layout change)
    context.beginPath();
    context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2);
    context.lineWidth = 2;
    context.strokeStyle = themeColors.hoverRingColor;
    context.stroke();

    if (!data.label) return;

    // ── Pill label ────────────────────────────────────────────────────────────
    const fontSize = settings.labelSize ?? 13;
    const font = `${settings.labelWeight ?? "600"} ${fontSize}px ${settings.labelFont ?? "Inter, system-ui, sans-serif"}`;
    context.font = font;
    const textWidth = context.measureText(data.label).width;

    const padX = 8;
    const padY = 4;
    const pillR = 5; // border-radius
    const boxW = textWidth + padX * 2;
    const boxH = fontSize + padY * 2;
    // Position pill above the node with a gap
    const boxX = data.x - boxW / 2;
    const boxY = data.y - data.size - boxH - 6;

    // Draw pill background (dark in both themes → high contrast with light text)
    context.beginPath();
    if (
      typeof (context as CanvasRenderingContext2D & { roundRect?: (...a: unknown[]) => void })
        .roundRect === "function"
    ) {
      (
        context as CanvasRenderingContext2D & {
          roundRect: (x: number, y: number, w: number, h: number, r: number) => void;
        }
      ).roundRect(boxX, boxY, boxW, boxH, pillR);
    } else {
      // Manual rounded-rect fallback for Safari < 15.4
      const r = Math.min(pillR, boxW / 2, boxH / 2);
      context.moveTo(boxX + r, boxY);
      context.arcTo(boxX + boxW, boxY, boxX + boxW, boxY + boxH, r);
      context.arcTo(boxX + boxW, boxY + boxH, boxX, boxY + boxH, r);
      context.arcTo(boxX, boxY + boxH, boxX, boxY, r);
      context.arcTo(boxX, boxY, boxX + boxW, boxY, r);
      context.closePath();
    }
    context.fillStyle = "rgba(15, 20, 30, 0.88)";
    context.fill();

    // Subtle border (light in both themes — overlaid on dark pill)
    context.strokeStyle = "rgba(255, 255, 255, 0.15)";
    context.lineWidth = 1;
    context.stroke();

    // Label text — always light on the dark pill for maximum readability
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillStyle = "#f1f5f9";
    context.font = font; // reset after stroke (some browsers reset font on stroke)
    context.fillText(data.label, data.x, boxY + boxH / 2);
  };
}

// ─── Edge key helper ──────────────────────────────────────────────────────────
// Canonical order-independent key for an edge pair, used by normalizedWeightMap.
// The server may return edges in either direction; we always normalise to
// sorted order so the map lookup is consistent regardless of (src, tgt) order.
export function edgeKey(a: string, b: string): string {
  return a < b ? `${a}__${b}` : `${b}__${a}`;
}

// ─── Meta node types (GI-2 "Hide index / overview / log" filter) ─────────────
// Matches the isMetaNode helper in graphInsights.ts (type-based check, fast in reducers).
export const META_NODE_TYPES = new Set(["index", "log", "overview"]);

// ─── Graph node type constants (shared with header filter) ───────────────────
// Must stay in sync with TYPE_COLORS keys above.
export const ALL_NODE_TYPES = [...GRAPH_PAGE_TYPE_ORDER, "other"] as const;
