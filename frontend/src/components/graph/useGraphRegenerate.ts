/**
 * useGraphRegenerate.ts — Custom hook: regenerate-graph control (reconnect + FA2 + refetch).
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * INVARIANT I2: recomputeGraph() is a server-side call — FA2 runs on the backend.
 *   The client only calls fetchGraph() afterward to receive the new precomputed coords.
 *   No layout algorithm is invoked on the main thread.
 */

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchGraph, recomputeGraph } from "../../api/graphClient";
import { useGraphStore, selectSetGraph, selectSetError } from "../../store/graphStore";
import { useAppStore, selectVaultId } from "../../store/appStore";

interface UseGraphRegenerateResult {
  /** true while the server-side recompute + refetch is in-flight */
  regenerating: boolean;
  /** user-visible result message (success or error), null while idle */
  regenMsg: string | null;
  /** trigger: reconnect cross-ingest links → server recomputes FA2 → refetch coords */
  handleRegenerate: () => Promise<void>;
}

export function useGraphRegenerate(): UseGraphRegenerateResult {
  const { t } = useTranslation();
  // I3: typed selectors
  const vaultId = useAppStore(selectVaultId);
  const setGraph = useGraphStore(selectSetGraph);
  const setError = useGraphStore(selectSetError);

  const [regenerating, setRegenerating] = useState(false);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);

  // ── Regenerate graph: reconnect cross-ingest links → server recomputes FA2 → refetch ──
  const handleRegenerate = useCallback(async () => {
    if (regenerating) return;
    setRegenerating(true);
    setRegenMsg(null);
    try {
      // 1. Reconnect cross-ingest links + FORCE a fresh server-side FA2 recompute (I2).
      //    Forcing (not just reresolve) guarantees the layout re-runs — so the outlier
      //    clamp takes effect and the graph stops collapsing to a dot.
      const result = await recomputeGraph();
      // 2. Refetch the freshly-computed precomputed coords (I2 — layout stays server-side).
      const { data, cacheStatus } = await fetchGraph(vaultId);
      setGraph(
        data.nodes,
        data.edges,
        data.data_version,
        cacheStatus,
        data.communities ?? [],
        data.total_nodes ?? null,
        data.total_edges ?? null,
      );
      setRegenMsg(
        result.reconnected > 0
          ? t("graph.regenerateDone", { count: result.reconnected })
          : t("graph.regenerateNone"),
      );
    } catch (err: unknown) {
      setRegenMsg(t("graph.regenerateError"));
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setRegenerating(false);
    }
  }, [regenerating, vaultId, setGraph, setError, t]);

  return { regenerating, regenMsg, handleRegenerate };
}
