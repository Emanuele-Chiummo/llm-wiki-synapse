/**
 * useGraphDataFetch.ts — Custom hook: mount fetch + WS-A version-bump re-fetch.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * INVARIANT I2: only calls fetchGraph (precomputed server coords); NEVER runs FA2 or
 *   any layout algorithm.
 * INVARIANT I3: subscriptions use typed selectors. No per-tick re-render.
 */

import { useEffect, useRef, useState } from "react";
import { fetchGraph } from "../../api/graphClient";
import {
  useGraphStore,
  selectSetGraph,
  selectSetLoading,
  selectSetError,
} from "../../store/graphStore";
import { useStatusStore, selectStatusDataVersion } from "../../store/statusStore";
import { useAppStore, selectVaultId } from "../../store/appStore";

// ─── RT-3: minimum interval between version-driven graph re-fetches ───────────
// During a long ingest the data_version can bump on every poll tick (3s cadence).
// Re-fetching the full graph on every bump is jittery and wasteful; a 10s minimum
// interval means we update the graph at most 6×/minute.
const GRAPH_REFETCH_MIN_MS = 10_000;

/**
 * Manages the two graph data-fetch effects:
 * 1. Mount / vaultId-change fetch (P2 cache-hit skip when store is already current).
 * 2. WS-A version-bump refetch (throttled; shows isGraphRefetching while in-flight).
 *
 * Reads vaultId, setGraph, setLoading, setError, statusDataVersion from their
 * respective stores directly — GraphViewer does not need to pass them.
 *
 * @returns isGraphRefetching — true while a background version-bump re-fetch is in-flight (UX-1)
 */
export function useGraphDataFetch(): { isGraphRefetching: boolean } {
  // I3: typed selectors
  const vaultId = useAppStore(selectVaultId);
  const setGraph = useGraphStore(selectSetGraph);
  const setLoading = useGraphStore(selectSetLoading);
  const setError = useGraphStore(selectSetError);
  // WS-A [F4/F16]: subscribe to data_version from the ActivityBar's existing GET /status poll.
  // INVARIANT AC-WS-A-4: no new poller; ActivityBar's STATUS_POLL_MS is the sole driver.
  const statusDataVersion = useStatusStore(selectStatusDataVersion);

  // Track which data_version the current graph data corresponds to so we only
  // refetch when the server version actually advances (AC-WS-A-3).
  const lastFetchedGraphVersionRef = useRef<number | null>(null);

  // RT-3: timestamp of the last version-driven graph re-fetch (milliseconds).
  const lastGraphRefetchTimeRef = useRef<number>(0);

  // UX-1: true only while a version-bump-triggered graph re-fetch is in-flight.
  const [isGraphRefetching, setIsGraphRefetching] = useState(false);

  // ── Fetch graph on mount / vaultId change ────────────────────────────────
  //
  // P2 — skip redundant fetch when the Zustand store already holds current data.
  //
  // On a REVISIT (navigate away → navigate back), the component unmounts/remounts
  // but the graphStore retains its nodes + dataVersion from the previous fetch.
  // If the store's dataVersion matches the latest statusDataVersion (from the
  // ActivityBar's existing /status poll), the data is already current and we can
  // rebuild sigma directly from the store without a network round-trip.
  //
  // Read store state imperatively via .getState() — this avoids adding reactive
  // deps to the effect and keeps I3 clean (no extra subscriptions).
  //
  // INVARIANT I2: no layout algorithm invoked — sigma rebuilds from precomputed
  // server coords stored in the Zustand nodes array (unchanged).
  useEffect(() => {
    // P2: cache-hit check — skip fetch when store data is already at the current version.
    const { nodes: storeNodes, dataVersion: storeDataVersion } = useGraphStore.getState();
    const currentStatusVersion = useStatusStore.getState().dataVersion;

    if (
      storeNodes.length > 0 &&
      storeDataVersion !== null &&
      currentStatusVersion !== null &&
      storeDataVersion === currentStatusVersion
    ) {
      // Store data is current. Sigma will rebuild from the existing nodes array.
      // Initialise the WS-A ref so a same-version status tick doesn't trigger a
      // redundant re-fetch via the WS-A effect below (AC-WS-A-3).
      lastFetchedGraphVersionRef.current = storeDataVersion;
      return; // no AbortController cleanup needed
    }

    const ctrl = new AbortController();
    setLoading(true);

    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        // WS-A: record the server version just fetched so we don't re-fetch on same-version ticks.
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
        }
      });

    return () => ctrl.abort();
  }, [vaultId, setGraph, setLoading, setError]);

  // ── WS-A [AC-WS-A-2, AC-WS-A-3]: re-fetch graph when data_version bumps ──
  // Polls via the existing ActivityBar /status cadence — no new interval (AC-WS-A-4).
  // Skips re-fetch if the version hasn't changed from last graph fetch (AC-WS-A-3).
  // INVARIANT I2: only calls fetchGraph; NEVER runs FA2 or any layout algorithm.
  // INVARIANT I3: effect deps are the version scalar; no per-tick re-render when unchanged.
  // RT-3: throttled to at most once per GRAPH_REFETCH_MIN_MS to prevent jitter during
  // long ingests (status poll cadence = 3s; many bumps in a row → skip intermediate ones).
  useEffect(() => {
    if (statusDataVersion === null) return;
    if (statusDataVersion === lastFetchedGraphVersionRef.current) return;
    // RT-3: enforce minimum interval between version-driven re-fetches (not initial mount).
    const now = Date.now();
    if (now - lastGraphRefetchTimeRef.current < GRAPH_REFETCH_MIN_MS) return;
    lastGraphRefetchTimeRef.current = now;
    // Version has advanced past the throttle window — refetch precomputed coords (AC-WS-A-2).
    const ctrl = new AbortController();
    setIsGraphRefetching(true); // UX-1: show "updating…" pill while in-flight
    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        // Transient errors (network hiccup) — don't surface to the user, just log.
        if (err instanceof Error && err.name !== "AbortError") {
          console.warn("[WS-A] graph freshness re-fetch failed:", err.message);
        }
      })
      .finally(() => {
        setIsGraphRefetching(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusDataVersion]); // vaultId, setGraph intentionally omitted: mount effect owns initial fetch

  return { isGraphRefetching };
}
