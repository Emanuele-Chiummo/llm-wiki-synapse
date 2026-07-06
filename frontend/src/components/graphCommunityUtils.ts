/**
 * graphCommunityUtils.ts — Pure centroid helpers for the graph viewer.
 *
 * Extracted into its own pure module so it can be unit-tested without importing
 * sigma.js (which requires WebGL2 and cannot run in jsdom environments).
 *
 * INVARIANT I2: this module contains ONLY read-only computations.
 *   It reads server-provided node coordinates (x, y) and averages them.
 *   It NEVER mutates node positions, never runs a layout algorithm, and
 *   never calls Math.random() or any non-deterministic function.
 *
 * INVARIANT I3: all exported functions are pure and intended to be called
 *   via useMemo — they run once per (nodes, communities/domain) change, NOT
 *   per sigma render frame.
 */

import type { GraphNode, GraphCommunity } from "../api/types";
import { colorForCommunity, colorForDomain } from "./graphPalette";

// ─── CommunityCentroid result type ────────────────────────────────────────────

export interface CommunityCentroid {
  /** Graph-space x coordinate (average of member node x values). I2: server coords only. */
  x: number;
  /** Graph-space y coordinate (average of member node y values). I2: server coords only. */
  y: number;
  /**
   * Display label for the centroid overlay.
   * Community mode: community.label (server name) → "C{id}" fallback.
   * Domain mode: domain name string.
   */
  label: string;
  /** Color from the active palette (community or domain). */
  color: string;
}

// ─── computeCommunityCentroids ────────────────────────────────────────────────

/**
 * Compute graph-space centroids for all multi-member communities.
 *
 * Singletons (communities whose size === 1 in the communities list) are excluded
 * to avoid cluttering the overlay with labels on isolated nodes.
 *
 * Unassigned nodes (community === -1 or negative) are always skipped.
 *
 * INVARIANT I2: reads server-provided x/y from GraphNode[] without mutation.
 * INVARIANT I3: pure function — no side effects; intended for useMemo.
 *
 * @param nodes      GraphNode[] from the store (server-provided coords).
 * @param communities GraphCommunity[] from the store (server-provided Louvain result).
 * @returns Map from community id → CommunityCentroid.
 */
export function computeCommunityCentroids(
  nodes: GraphNode[],
  communities: GraphCommunity[],
): Map<number, CommunityCentroid> {
  // Build a Set of community ids whose size > 1 (skip singletons for cleaner overlay)
  const multiMemberIds = new Set<number>();
  for (const c of communities) {
    if (c.size > 1) multiMemberIds.add(c.id);
  }

  // Accumulate sum of x/y per community id
  const sums = new Map<number, { sumX: number; sumY: number; count: number }>();
  for (const n of nodes) {
    const cid = n.community ?? -1;
    // Skip unassigned and singletons
    if (cid < 0 || !multiMemberIds.has(cid)) continue;
    const acc = sums.get(cid) ?? { sumX: 0, sumY: 0, count: 0 };
    acc.sumX += n.x;
    acc.sumY += n.y;
    acc.count += 1;
    sums.set(cid, acc);
  }

  // Build a lookup map for community metadata (label, etc.)
  const communityMap = new Map<number, GraphCommunity>();
  for (const c of communities) communityMap.set(c.id, c);

  // Produce the result map
  const result = new Map<number, CommunityCentroid>();
  for (const [cid, acc] of sums) {
    if (acc.count === 0) continue;
    const c = communityMap.get(cid);
    const rawLabel = c?.label;
    // Ultra-short fallback for the overlay: "C{id}" (legend shows the full name)
    const label =
      rawLabel != null && rawLabel.trim().length > 0
        ? rawLabel
        : `C${cid}`;
    result.set(cid, {
      x: acc.sumX / acc.count,
      y: acc.sumY / acc.count,
      label,
      color: colorForCommunity(cid),
    });
  }
  return result;
}

// ─── DomainCentroid result type ───────────────────────────────────────────────
// Reuses CommunityCentroid shape (same fields: x, y, label, color).
// The key type is string (domain name) rather than number.

export type DomainCentroid = CommunityCentroid;

// ─── computeDomainCentroids ───────────────────────────────────────────────────

/**
 * Compute graph-space centroids for all domains that have >= 2 nodes.
 *
 * Singletons (domains represented by only 1 node) are excluded to avoid
 * cluttering the overlay. Untagged nodes (domain === null / undefined) are
 * always skipped — the "Senza dominio" bucket is shown in the legend only.
 *
 * INVARIANT I2: reads server-provided x/y from GraphNode[] without mutation.
 * INVARIANT I3: pure function — no side effects; intended for useMemo.
 *
 * @param nodes  GraphNode[] from the store (server-provided coords + domain field).
 * @returns Map from domain name → DomainCentroid.
 */
export function computeDomainCentroids(nodes: GraphNode[]): Map<string, DomainCentroid> {
  // First pass: count nodes per domain to determine multi-member domains
  const countPerDomain = new Map<string, number>();
  for (const n of nodes) {
    const d = n.domain;
    if (d === null || d === undefined || d.trim() === "") continue;
    countPerDomain.set(d, (countPerDomain.get(d) ?? 0) + 1);
  }

  // Second pass: accumulate x/y sums for multi-member domains only
  const sums = new Map<string, { sumX: number; sumY: number; count: number }>();
  for (const n of nodes) {
    const d = n.domain;
    if (d === null || d === undefined || d.trim() === "") continue;
    const memberCount = countPerDomain.get(d) ?? 0;
    if (memberCount < 2) continue; // skip singletons
    const acc = sums.get(d) ?? { sumX: 0, sumY: 0, count: 0 };
    acc.sumX += n.x;
    acc.sumY += n.y;
    acc.count += 1;
    sums.set(d, acc);
  }

  // Produce the result map
  const result = new Map<string, DomainCentroid>();
  for (const [domain, acc] of sums) {
    if (acc.count === 0) continue;
    result.set(domain, {
      x: acc.sumX / acc.count,
      y: acc.sumY / acc.count,
      label: domain,
      color: colorForDomain(domain),
    });
  }
  return result;
}
