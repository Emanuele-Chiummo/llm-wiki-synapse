/**
 * useSelectedNodeAnnouncement.ts — Custom hook: sync selectedNodeId → aria-live announcement.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * Keeps selectedNodeIdRef in sync (so the sigma mount effect's nodeReducer can paint
 * the persistent selection ring without a re-render per frame — I3) and computes the
 * accessible announcement text for the aria-live region.
 */

import { useEffect, useState } from "react";
import type { MutableRefObject } from "react";
import type { TFunction } from "i18next";
import type Sigma from "sigma";
import type { Attributes } from "graphology-types";

interface UseSelectedNodeAnnouncementParams {
  selectedNodeId: string | null;
  /** Stable ref that the sigma mount effect's nodeReducer reads — mutated here (I3) */
  selectedNodeIdRef: MutableRefObject<string | null>;
  sigmaRef: MutableRefObject<Sigma<Attributes, Attributes, Attributes> | null>;
  t: TFunction;
}

interface UseSelectedNodeAnnouncementResult {
  /** Aria-live announcement text for the selected node */
  announcement: string;
  /** Clear the announcement text (e.g. on tooltip close) */
  clearAnnouncement: () => void;
}

export function useSelectedNodeAnnouncement({
  selectedNodeId,
  selectedNodeIdRef,
  sigmaRef,
  t,
}: UseSelectedNodeAnnouncementParams): UseSelectedNodeAnnouncementResult {
  const [announcement, setAnnouncement] = useState<string>("");

  // ── Sync selectedNodeId from store → ref (for sigma reducer) + announcement ──
  useEffect(() => {
    // Keep the reducer's ref in sync so the persistent selection ring follows the store.
    selectedNodeIdRef.current = selectedNodeId;
    if (!selectedNodeId) {
      setAnnouncement("");
      // Clear the ring: re-run reducers now that nothing is selected.
      sigmaRef.current?.refresh({ skipIndexation: true });
      return;
    }
    if (!sigmaRef.current) return;

    const graph = sigmaRef.current.getGraph();
    const attrs = graph.getNodeAttributes(selectedNodeId) as Attributes & {
      label?: string;
      nodeType?: string | null;
      degree?: number;
    };

    const title = attrs["label"] ?? selectedNodeId;
    const type = attrs["nodeType"] ?? "unknown type";
    const neighborCount = graph.neighbors(selectedNodeId).length;

    // F8: use i18n for screen-reader announcement (was hardcoded English)
    setAnnouncement(t("graph.nodeSelected", { title, type, count: neighborCount }));

    // Trigger a refresh so sigma re-applies reducers with updated selectedNode
    sigmaRef.current.refresh({ skipIndexation: true });
  }, [selectedNodeId, selectedNodeIdRef, sigmaRef, t]);

  return {
    announcement,
    clearAnnouncement: () => setAnnouncement(""),
  };
}
