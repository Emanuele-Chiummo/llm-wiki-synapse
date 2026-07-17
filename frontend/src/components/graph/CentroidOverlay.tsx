/**
 * CentroidOverlay.tsx — generalised centroid-label overlay for Community/Domain
 * color modes (R9-5). Move-only extraction from GraphViewer.tsx (FE-ARCH-1,
 * 1.9.3 W2) — no behavior change.
 */

import React, { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { CommunityCentroid } from "../graphCommunityUtils";

// ─── CentroidOverlay ──────────────────────────────────────────────────────────
// Generalised centroid-label overlay for both Community and Domain color modes.
//
// Architecture:
//   - Centroids are received as a pre-memoized Map<string, CommunityCentroid>.
//     (Community mode converts number ids → strings; Domain mode uses domain names.)
//   - On sigma `afterRender`, project graph-space centroids → viewport pixels with
//     sigma.graphToViewport(). Schedule with requestAnimationFrame so DOM writes are
//     batched with sigma's frame (I3: no string work per frame — only numeric transforms).
//   - Labels are positioned absolutely in the overlay div that sits above the canvas.
//   - pointer-events: none so the overlay never blocks sigma interaction.
//   - Hidden entirely when active=false or centroids map is empty.
//
// INVARIANT I2: we NEVER mutate node x/y. We read coords to project them.
// INVARIANT I3: centroid computation is outside this component (memoized by caller).
//              Projection (graphToViewport) is fast: pure arithmetic, no string ops.

interface CentroidOverlayProps {
  /**
   * Pre-memoized centroids keyed by string (community id as string, or domain name).
   * Memoized by GraphViewer caller.
   */
  centroids: Map<string, CommunityCentroid>;
  /** The sigma instance — used to subscribe to afterRender + call graphToViewport. */
  sigmaRef: React.RefObject<Sigma<Attributes, Attributes, Attributes> | null>;
  /** Only render when this mode is active. */
  active: boolean;
  /** data-testid prefix for the overlay container. */
  testId?: string;
}

/** Maximum label character count before truncation with ellipsis. */
const OVERLAY_LABEL_MAX_CHARS = 20;

/**
 * P3: Minimum screen padding (px) so centroid labels never render behind
 * the toolbar/header or outside the canvas bounds.
 * Only the on-screen CSS position is clamped — graph coordinates are untouched (I2).
 */
const CENTROID_LABEL_PAD = 8;

function truncateOverlayLabel(label: string): string {
  if (label.length <= OVERLAY_LABEL_MAX_CHARS) return label;
  return label.slice(0, OVERLAY_LABEL_MAX_CHARS - 1) + "…";
}

export const CentroidOverlay: React.FC<CentroidOverlayProps> = ({
  centroids,
  sigmaRef,
  active,
  testId = "community-overlay",
}) => {
  // Stored in a ref (not state) to avoid React re-renders on every sigma frame.
  // We update the DOM directly via the overlayRef to stay off the React tree entirely.
  const overlayRef = useRef<HTMLDivElement>(null);
  // rafHandle: rAF id so we can cancel on cleanup
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active || centroids.size === 0) {
      // Hide all labels when not in the active mode or no centroids
      if (overlayRef.current) overlayRef.current.style.display = "none";
      return;
    }
    if (overlayRef.current) overlayRef.current.style.display = "block";

    const sigma = sigmaRef.current;
    if (!sigma) return;

    // Project all centroids and update the overlay DOM directly (no React state mutation).
    // This avoids React re-renders on every sigma frame (I3).
    function project() {
      const overlay = overlayRef.current;
      const s = sigmaRef.current;
      if (!overlay || !s) return;

      // Canvas bounds — used to clamp label positions so they stay inside.
      const containerRect = overlay.getBoundingClientRect();
      const maxX = containerRect.width - CENTROID_LABEL_PAD;
      const maxY = containerRect.height - CENTROID_LABEL_PAD;

      // One pass: update each label element's transform. Elements are keyed by data-cid.
      // P3: clamp projected viewport (x, y) so labels never escape the canvas or
      // render behind the toolbar. ONLY the on-screen CSS position is clamped —
      // centroid.x / centroid.y (graph coords from server) are never mutated (I2).
      //
      // De-overlap: in some camera states many centroids project to nearly the SAME
      // point (e.g. a dense cluster panned into a corner), stacking every community
      // label into one illegible "ghost" of text over the toolbar. We greedily place
      // labels in Map order (largest community first) and HIDE any that would land on
      // top of one already placed — so a stack collapses to a single readable label.
      const placed: Array<[number, number]> = [];
      const MIN_SEP = 22; // px; labels closer than this collapse to the first-placed one
      for (const [cid, centroid] of centroids) {
        const el = overlay.querySelector<HTMLElement>(`[data-cid="${String(cid)}"]`);
        if (!el) continue;
        const vp = s.graphToViewport({ x: centroid.x, y: centroid.y });
        const cx = Math.max(CENTROID_LABEL_PAD, Math.min(maxX, vp.x));
        const cy = Math.max(CENTROID_LABEL_PAD, Math.min(maxY, vp.y));
        // If clamping had to MOVE the label, its centroid lies OUTSIDE the on-canvas
        // safe zone ([PAD, max]) — HIDE it instead of stranding it at the edge.
        if (cx !== vp.x || cy !== vp.y) {
          el.style.display = "none";
          continue;
        }
        // Collapse near-coincident labels (declutter + kill the corner pile-up).
        let collides = false;
        for (const [px, py] of placed) {
          if (Math.abs(px - cx) < MIN_SEP && Math.abs(py - cy) < MIN_SEP) {
            collides = true;
            break;
          }
        }
        if (collides) {
          el.style.display = "none";
          continue;
        }
        placed.push([cx, cy]);
        el.style.display = "";
        // Translate so the label is centered on the centroid's viewport position.
        el.style.transform = `translate(calc(${cx}px - 50%), calc(${cy}px - 50%))`;
      }
    }

    // Throttle via rAF: sigma fires afterRender at most 60fps; we just schedule
    // one DOM-write frame after each render event (I3: no per-token string work).
    function onAfterRender() {
      if (rafRef.current !== null) return; // already queued
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        project();
      });
    }

    // Initial projection
    project();

    // Subscribe to sigma afterRender so projections follow camera moves/zooms
    sigma.on("afterRender", onAfterRender);

    return () => {
      sigma.off("afterRender", onAfterRender);
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    // Re-subscribe when sigma instance changes (graph rebuild) or centroids change
  }, [active, centroids, sigmaRef]);

  if (!active || centroids.size === 0) return null;

  return (
    <div
      ref={overlayRef}
      data-testid={testId}
      style={{
        position: "absolute",
        inset: 0,
        // pointer-events none: overlay is purely visual — never blocks sigma interaction
        pointerEvents: "none",
        // overflow hidden: labels near edges clip cleanly
        overflow: "hidden",
      }}
      aria-hidden="true"
    >
      {/* Render one label element per centroid.
          Their CSS transform is updated directly in the effect above — NO React state.
          I2: we do not add graph nodes here; this is pure DOM overlay. */}
      {Array.from(centroids.entries()).map(([cid, centroid]) => (
        <div
          key={String(cid)}
          data-cid={String(cid)}
          data-testid={`community-overlay-label-${String(cid)}`}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            // Hidden until project() positions it. Otherwise, before sigma is ready
            // (or right after a React re-render resets this inline style), every label
            // sits at the container origin (0,0) and they pile into an illegible blob
            // over the top-left toolbar. project() flips display back to "" once it has
            // a real, de-overlapped on-canvas position.
            display: "none",
            // transform is set dynamically in the effect
            transform: "translate(-50%, -50%)",
            fontSize: 11,
            fontWeight: 600,
            fontFamily: "Inter, system-ui, sans-serif",
            letterSpacing: "0.02em",
            color: centroid.color,
            // Text halo for legibility on any background (matches sigma's own halo approach)
            textShadow: [
              "-1px -1px 0 rgba(255,255,255,0.85)",
              " 1px -1px 0 rgba(255,255,255,0.85)",
              "-1px  1px 0 rgba(255,255,255,0.85)",
              " 1px  1px 0 rgba(255,255,255,0.85)",
              " 0    0   3px rgba(255,255,255,0.6)",
            ].join(","),
            whiteSpace: "nowrap",
            userSelect: "none",
          }}
        >
          {truncateOverlayLabel(centroid.label)}
        </div>
      ))}
    </div>
  );
};
