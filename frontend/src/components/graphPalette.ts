/**
 * graphPalette.ts — Community and domain color palettes for the knowledge graph viewer.
 *
 * Extracted into its own pure module so it can be unit-tested without importing
 * sigma.js (which requires WebGL2 and cannot run in jsdom environments).
 *
 * INVARIANT I2: this module contains ONLY read-only palette lookups.
 * No layout algorithm, no Louvain, no community computation.
 * Community ids are always provided by the server (GET /graph response).
 * Domain names are always provided by the server (GraphNode.domain field).
 *
 * LIGHT THEME NOTE:
 *   All hex values are concrete strings — sigma cannot resolve CSS custom properties
 *   at canvas draw time, so this is the documented exception to token-only usage
 *   (ADR-0015 §CVD-SAFE). If the light theme changes, update these values in sync.
 */

// ─── Community color palette (spec §COMMUNITY-PALETTE) ────────────────────────
// 12-color categorical set, light-theme-friendly (high contrast on white).
// Cycles for >12 communities. Each entry is a 7-char hex string (#rrggbb).
//
// Palette tuned for light backgrounds:
//   0  #1f77b4  steel blue
//   1  #e07700  burnt orange
//   2  #2ca02c  medium green
//   3  #d62728  brick red
//   4  #7b35b0  violet purple
//   5  #8c564b  brown
//   6  #e377c2  pink
//   7  #7f7f7f  mid grey
//   8  #bdae00  dark yellow
//   9  #17becf  teal
//  10  #0a6640  dark forest green
//  11  #a52a2a  deep red (maroon)
//
// Unassigned nodes (community === -1) use COMMUNITY_UNASSIGNED_COLOR.
// INVARIANT I2: this is a read-only constant — no community detection here.

export const COMMUNITY_PALETTE: readonly string[] = [
  "#1f77b4", // 0 — steel blue
  "#e07700", // 1 — burnt orange
  "#2ca02c", // 2 — medium green
  "#d62728", // 3 — brick red
  "#7b35b0", // 4 — violet purple
  "#8c564b", // 5 — brown
  "#e377c2", // 6 — pink
  "#7f7f7f", // 7 — mid grey
  "#bdae00", // 8 — dark yellow
  "#17becf", // 9 — teal
  "#0a6640", // 10 — dark forest green
  "#a52a2a", // 11 — deep red (maroon)
] as const;

/**
 * Dark-theme community palette (W4 audit FE-GRAPH-1).
 * Same 12-color CVD-safe hue set as COMMUNITY_PALETTE, lightened to the same
 * degree as the --syn-type-* dark overrides in theme.css so each color stays
 * legible on the navy dark-theme canvas (--syn-bg: #0b1120) instead of
 * washing out like the light-theme tones do.
 */
export const COMMUNITY_PALETTE_DARK: readonly string[] = [
  "#58a6ff", // 0 — steel blue (lightened; matches --syn-accent dark)
  "#ffa657", // 1 — burnt orange (lightened)
  "#56d364", // 2 — medium green (lightened; matches --syn-green dark)
  "#ff7b72", // 3 — brick red (lightened)
  "#b088f9", // 4 — violet purple (lightened; matches --syn-type-concept dark)
  "#cf9e8a", // 5 — brown (lightened)
  "#f2a5dd", // 6 — pink (lightened)
  "#a8a8a8", // 7 — mid grey (lightened)
  "#e6d84a", // 8 — dark yellow (lightened)
  "#56d4e0", // 9 — teal (lightened; matches --syn-type-source dark)
  "#2ea56f", // 10 — dark forest green (lightened)
  "#d16b6b", // 11 — deep red / maroon (lightened)
] as const;

/**
 * Color for unassigned nodes (community === -1 or any negative id) — light theme.
 * Matches --syn-type-other in theme.css.
 */
export const COMMUNITY_UNASSIGNED_COLOR = "#6e7781";

/**
 * Color for unassigned nodes — dark theme.
 * Matches --syn-type-other / --syn-text-dim dark override in theme.css.
 */
export const COMMUNITY_UNASSIGNED_COLOR_DARK = "#7d8590";

/**
 * Low-cohesion threshold.
 * Communities with cohesion strictly below this value are flagged with a
 * warning indicator in the legend (llm_wiki pattern).
 */
export const LOW_COHESION_THRESHOLD = 0.1;

/**
 * Color-mode discriminant.
 *   "type"      — colors nodes by page type (concept, entity, source, …).
 *   "community" — colors nodes by Louvain community id (server-computed).
 *                 One distinct color per cluster from COMMUNITY_PALETTE (12-color cycle).
 *                 Community legend rows are labeled with communityDisplayName(c):
 *                 "{dominant_domain} · {top_page_subtopic}" — unique per cluster.
 */
export type ColorMode = "type" | "community";

/** Resolved app theme — read from document.documentElement.dataset.theme. */
export type GraphTheme = "light" | "dark";

/**
 * Returns the color for a given server-provided community id.
 *
 * - Negative ids (unassigned, -1) → COMMUNITY_UNASSIGNED_COLOR (#6e7781, or the
 *   dark-theme equivalent when theme === "dark")
 * - 0–11                           → COMMUNITY_PALETTE[id] (or _DARK)
 * - ≥12                            → COMMUNITY_PALETTE[id % 12] (cycle)
 *
 * INVARIANT I2: the `communityId` argument MUST come from the server
 * (GraphNode.community field in the GET /graph response). Never pass a
 * client-computed value here.
 *
 * `theme` (W4 audit FE-GRAPH-1): pass "dark" when
 * document.documentElement.dataset.theme === "dark" so the palette stays
 * legible on the dark canvas instead of washing out. Defaults to "light" for
 * existing call sites.
 */
export function colorForCommunity(communityId: number, theme: GraphTheme = "light"): string {
  const palette = theme === "dark" ? COMMUNITY_PALETTE_DARK : COMMUNITY_PALETTE;
  const unassigned =
    theme === "dark" ? COMMUNITY_UNASSIGNED_COLOR_DARK : COMMUNITY_UNASSIGNED_COLOR;
  if (communityId < 0) return unassigned;
  return palette[communityId % palette.length] ?? unassigned;
}

// ─── Domain color palette ──────────────────────────────────────────────────────
// 16-color categorical set distinct from the community palette.
// Used exclusively in colorMode === "domain".
// Same null/untagged domain → DOMAIN_UNTAGGED_COLOR (neutral gray).
// Same domain name → same color everywhere (deterministic hash → index).
//
// Colors are light-theme-friendly and CVD-safe (shape+name used redundantly in legend).

const DOMAIN_PALETTE: readonly string[] = [
  "#0969da", // 0 — github blue
  "#cf222e", // 1 — github red
  "#1a7f37", // 2 — github green
  "#9a6700", // 3 — amber/gold
  "#6639ba", // 4 — purple
  "#c4432b", // 5 — terra cotta
  "#0550ae", // 6 — navy
  "#116329", // 7 — forest green
  "#a40e26", // 8 — crimson
  "#24292f", // 9 — near black
  "#006eaa", // 10 — cerulean
  "#8a4b08", // 11 — brown
  "#5a3e8e", // 12 — deep violet
  "#0e7a6e", // 13 — dark teal
  "#b35900", // 14 — burnt sienna
  "#2d6a4f", // 15 — dark sage
] as const;

/**
 * Dark-theme domain palette (W4 audit FE-GRAPH-1).
 * Lightened counterpart of DOMAIN_PALETTE, same lightening approach as the
 * --syn-type-* dark overrides in theme.css. Index 9 in the light palette
 * ("#24292f", near-black) is invisible against the dark canvas background
 * (--syn-bg: #0b1120) — replaced here with a light periwinkle/lavender tone
 * that stays distinct from every other entry instead of a near-black swap.
 */
const DOMAIN_PALETTE_DARK: readonly string[] = [
  "#58a6ff", // 0 — github blue (lightened)
  "#ff7b72", // 1 — github red (lightened)
  "#56d364", // 2 — github green (lightened)
  "#d3a24a", // 3 — amber/gold (lightened)
  "#a78bfa", // 4 — purple (lightened; matches --syn-type-concept dark)
  "#e58a63", // 5 — terra cotta (lightened; matches --syn-type-comparison dark)
  "#79c0ff", // 6 — navy (lightened)
  "#56d379", // 7 — forest green (lightened)
  "#ff9492", // 8 — crimson (lightened)
  "#c4b5fd", // 9 — light lavender (was near-black #24292f — invisible on dark; brand-compliant swap, never black)
  "#4fb3ff", // 10 — cerulean (lightened)
  "#daa657", // 11 — brown (lightened)
  "#b399e8", // 12 — deep violet (lightened)
  "#2cc3b9", // 13 — dark teal (lightened; matches --syn-type-source dark)
  "#e6934d", // 14 — burnt sienna (lightened)
  "#4f9d75", // 15 — dark sage (lightened)
] as const;

/**
 * Color for nodes with no domain tag (domain === null or absent) — light theme.
 * Neutral gray — visually distinct from all domain colors above.
 * Matches --syn-type-other / COMMUNITY_UNASSIGNED_COLOR.
 */
export const DOMAIN_UNTAGGED_COLOR = "#8b949e";

/**
 * Color for untagged nodes — dark theme.
 * Matches --syn-type-other dark override (#8b949e stays legible enough, but
 * this slightly lighter tone keeps it distinct from DOMAIN_PALETTE_DARK[9]).
 */
export const DOMAIN_UNTAGGED_COLOR_DARK = "#9198a1";

/**
 * Returns a STABLE, DETERMINISTIC color for a domain name.
 *
 * Algorithm: djb2 hash of the domain string → index into DOMAIN_PALETTE.
 * Properties:
 *   - Same string → same color everywhere in the session and across sessions.
 *   - No external state — pure function (I3 compliant).
 *   - null / undefined → DOMAIN_UNTAGGED_COLOR (neutral gray).
 *
 * INVARIANT I2: the `domain` argument MUST come from the server
 * (GraphNode.domain field in the GET /graph response).
 *
 * `theme` (W4 audit FE-GRAPH-1): pass "dark" when
 * document.documentElement.dataset.theme === "dark" to use the lightened
 * dark-theme palette. Defaults to "light" for existing call sites.
 */
export function colorForDomain(
  domain: string | null | undefined,
  theme: GraphTheme = "light",
): string {
  const palette = theme === "dark" ? DOMAIN_PALETTE_DARK : DOMAIN_PALETTE;
  const untagged = theme === "dark" ? DOMAIN_UNTAGGED_COLOR_DARK : DOMAIN_UNTAGGED_COLOR;
  if (domain === null || domain === undefined || domain.trim() === "") {
    return untagged;
  }
  // djb2 hash (non-cryptographic, fast, well-distributed for short strings)
  let hash = 5381;
  for (let i = 0; i < domain.length; i++) {
    // hash * 33 + charCode
    hash = ((hash << 5) + hash) ^ domain.charCodeAt(i);
    // Keep in 32-bit signed integer range via bitwise OR 0
    hash = hash | 0;
  }
  // Map to a non-negative palette index
  const index = Math.abs(hash) % palette.length;
  return palette[index] ?? untagged;
}
