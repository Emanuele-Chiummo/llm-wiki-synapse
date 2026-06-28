/**
 * ActivityBar.tsx — bottom status bar: vault id, data_version, provider placeholder.
 *
 * Polls GET /status every 30 s to keep the data_version indicator fresh
 * (triggers NavTree or graph refresh only if the version changed — no eager re-fetch).
 *
 * INVARIANT I3: subscribes to graphStore only via typed selectors.
 */

import { useEffect, useRef, useState } from "react";
import { useGraphStore } from "../../store/graphStore";
import { selectVaultId } from "../../store/graphStore";
import { useGraphMeta } from "../../store/graphStore";
import { fetchStatus } from "../../api/pagesClient";

const POLL_INTERVAL_MS = 30_000;

interface StatusInfo {
  dataVersion: number | null;
  uptimeSeconds: number | null;
}

export function ActivityBar() {
  const vaultId = useGraphStore(selectVaultId);
  const { dataVersion: storeVersion } = useGraphMeta();

  const [status, setStatus] = useState<StatusInfo>({
    dataVersion: null,
    uptimeSeconds: null,
  });
  const [pollError, setPollError] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();

    async function poll() {
      try {
        const res = await fetchStatus(ctrl.signal);
        if (!ctrl.signal.aborted) {
          setStatus({ dataVersion: res.data_version, uptimeSeconds: res.uptime_seconds });
          setPollError(false);
        }
      } catch {
        if (!ctrl.signal.aborted) {
          setPollError(true);
        }
      }
      if (!ctrl.signal.aborted) {
        timerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    void poll();

    return () => {
      ctrl.abort();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const displayVersion = storeVersion ?? status.dataVersion;

  function formatUptime(s: number | null): string {
    if (s === null) return "–";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  return (
    <footer
      className="activity-bar"
      aria-label="Activity bar"
      data-testid="activity-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 12px",
        height: 28,
        background: "#0d1117",
        borderTop: "1px solid #21262d",
        fontSize: 11,
        color: "#484f58",
        flexShrink: 0,
        overflow: "hidden",
      }}
    >
      {/* Vault id */}
      <span
        aria-label={`Vault: ${vaultId}`}
        style={{ display: "flex", alignItems: "center", gap: 4 }}
      >
        <span aria-hidden="true" style={{ opacity: 0.5 }}>&#128193;</span>
        <span style={{ color: "#6e7681" }}>{vaultId}</span>
      </span>

      {/* Data version */}
      <span
        aria-label={`Data version: ${displayVersion ?? "unknown"}`}
        style={{ display: "flex", alignItems: "center", gap: 4 }}
      >
        <span aria-hidden="true" style={{ opacity: 0.5 }}>v</span>
        <span style={{ fontFamily: "monospace", color: "#6e7681" }}>
          {displayVersion ?? "–"}
        </span>
      </span>

      {/* Uptime */}
      {status.uptimeSeconds !== null && (
        <span
          aria-label={`Uptime: ${formatUptime(status.uptimeSeconds)}`}
          style={{ display: "flex", alignItems: "center", gap: 4 }}
        >
          <span aria-hidden="true" style={{ opacity: 0.5 }}>&#8679;</span>
          <span style={{ color: "#484f58" }}>{formatUptime(status.uptimeSeconds)}</span>
        </span>
      )}

      {/* Connectivity indicator */}
      <span
        aria-label={pollError ? "Backend unreachable" : "Backend connected"}
        style={{ display: "flex", alignItems: "center", gap: 4 }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: pollError ? "#f85149" : "#3fb950",
            display: "inline-block",
          }}
        />
      </span>

      {/* Spacer */}
      <span style={{ flex: 1 }} />

      {/* Provider placeholder — F17 Provider Selector will replace this */}
      <span
        aria-label="Provider selector (coming in Phase 2)"
        style={{ color: "#484f58", cursor: "default" }}
        title="Provider selector — coming in v0.4 Phase 2 (F17)"
      >
        Provider: –
      </span>
    </footer>
  );
}
