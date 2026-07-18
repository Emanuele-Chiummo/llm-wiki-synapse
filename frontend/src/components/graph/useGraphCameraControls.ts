/**
 * useGraphCameraControls.ts — Custom hook: camera zoom/fit/search/reset/fullscreen callbacks.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * INVARIANT I2: all handlers only manipulate the sigma CAMERA (no layout algorithm invoked).
 * Camera.animatedZoom / animatedUnzoom / animatedReset / animate operate on the existing
 * precomputed-coords rendering — no FA2, no rAF physics loop.
 */

import { useCallback } from "react";
import type { MutableRefObject, RefObject } from "react";
import type Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { AppActions } from "../../store/appStore";
import { reducedMotion } from "./graphViewerShared";

interface UseGraphCameraControlsParams {
  sigmaRef: MutableRefObject<Sigma<Attributes, Attributes, Attributes> | null>;
  graphRootRef: RefObject<HTMLDivElement | null>;
  selectPage: AppActions["selectPage"];
  setSelectedNodeId: (id: string | null) => void;
  clearAllGraphFilters: () => void;
}

interface UseGraphCameraControlsResult {
  handleZoomIn: () => void;
  handleZoomOut: () => void;
  handleFit: () => void;
  /** GR2: in-graph search — find node by title substring, select + camera center */
  handleSearch: (query: string) => void;
  /** GR4: reset — clear ALL filters (type + GI-2) + fit camera */
  handleReset: () => void;
  /** GR7: fullscreen — Fullscreen API on the graph root container */
  handleFullscreen: () => void;
}

export function useGraphCameraControls({
  sigmaRef,
  graphRootRef,
  selectPage,
  setSelectedNodeId,
  clearAllGraphFilters,
}: UseGraphCameraControlsParams): UseGraphCameraControlsResult {
  // ── Camera controls — zoom in / out / fit ─────────────────────────────────────
  // These are simple camera calls; I2 is preserved (no layout algorithm invoked).
  // reducedMotion is read from the module-level const declared in graphViewerShared.

  const handleZoomIn = useCallback(() => {
    sigmaRef.current?.getCamera().animatedZoom({ duration: reducedMotion ? 0 : 200 });
  }, [sigmaRef]);

  const handleZoomOut = useCallback(() => {
    sigmaRef.current?.getCamera().animatedUnzoom({ duration: reducedMotion ? 0 : 200 });
  }, [sigmaRef]);

  const handleFit = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 300 });
  }, [sigmaRef]);

  // ── GR2: In-graph search — find node by title substring, select + camera center ──
  // Client-side only; nodes are already in the store (I3: computed on change, not per frame).
  const handleSearch = useCallback(
    (query: string) => {
      if (!query.trim() || !sigmaRef.current) return;
      const q = query.toLowerCase();
      // Find first matching node in the sigma graph
      const sigma = sigmaRef.current;
      const graph = sigma.getGraph();
      let matchKey: string | null = null;
      graph.forEachNode((key, attrs) => {
        if (matchKey !== null) return;
        const label = ((attrs["label"] as string | undefined) ?? "").toLowerCase();
        if (label.includes(q)) matchKey = key;
      });
      if (matchKey === null) return;
      // Select the node (triggers aria announcement + tree sync)
      selectPage(matchKey, "graph");
      setSelectedNodeId(matchKey);
      // Animate camera to center on the found node's precomputed coords (I2-safe: read-only)
      const attrs = graph.getNodeAttributes(matchKey);
      const x = attrs["x"] as number;
      const y = attrs["y"] as number;
      sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: reducedMotion ? 0 : 400 });
    },
    [sigmaRef, selectPage, setSelectedNodeId],
  );

  // ── GR4: Reset — clear ALL filters (type + GI-2) + fit camera ───────────────
  const handleReset = useCallback(() => {
    clearAllGraphFilters(); // clears filterNodeTypes + hideMetaTypes/hideIsolated/minLinks/maxLinks/nodeSizeScale/spacingScale
    sigmaRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 300 });
  }, [clearAllGraphFilters, sigmaRef]);

  // ── GR7: Fullscreen — Fullscreen API on the graph root container ───────────
  const handleFullscreen = useCallback(() => {
    const el = graphRootRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch((err: unknown) => {
        if (err instanceof Error) console.warn("[GraphViewer] fullscreen failed:", err.message);
      });
    } else {
      document.exitFullscreen().catch(() => {
        /* ignore */
      });
    }
  }, [graphRootRef]);

  return {
    handleZoomIn,
    handleZoomOut,
    handleFit,
    handleSearch,
    handleReset,
    handleFullscreen,
  };
}
