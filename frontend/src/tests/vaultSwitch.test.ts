/**
 * vaultSwitch.test.ts — FE-UIUX-3 gate: switching vault A → B must not leave ANY
 * vault-A data/selection visible in any vault-scoped Zustand store afterward.
 *
 * This is the frontend equivalent of the cross-vault leak already fixed
 * server-side in 1.9.1 — resetAllVaultStores() is the single choke point every
 * vault-switch entry point (ProjectLauncher, NewProjectWizard) goes through.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// fetchStatus is called by refreshStatusNow() (one-shot /status refresh) — mock
// it so the test never hits the network.
vi.mock("../api/pagesClient", () => ({
  fetchStatus: vi.fn().mockResolvedValue({
    version: "1.9.3",
    review_pending: 0,
    supports_vision: false,
    data_version: 1,
    uptime_seconds: 1,
    vault_id: "vault-b",
  }),
}));

import { resetAllVaultStores } from "../store/vaultSwitch";
import { useAppStore } from "../store/appStore";
import { useGraphStore } from "../store/graphStore";
import { useIngestStore } from "../store/ingestStore";
import { useActivityStore } from "../store/activityStore";
import { useChatStore } from "../store/chatStore";
import { useLintStore } from "../store/lintStore";
import { useReviewStore } from "../store/reviewStore";
import { useResearchStore } from "../store/researchStore";
import { useImportScheduleStore } from "../store/importScheduleStore";
import { useProviderStore } from "../store/providerStore";
import { useStatusStore } from "../store/statusStore";

const VAULT_A = "vault-a";
const VAULT_B = "vault-b";

/** Seed every vault-scoped store with fake "vault A" data. */
function seedVaultAData(): void {
  useAppStore.setState({
    vaultId: VAULT_A,
    selectedNodeId: "node-a-1",
    selectedSource: "tree",
    showInsightsPanel: true,
  });

  useGraphStore.setState({
    nodes: [{ id: "node-a-1", label: "A1", type: "concept", x: 0, y: 0, size: 1 }] as never,
    edges: [{ source: "node-a-1", target: "node-a-1", weight: 1 }] as never,
    dataVersion: 42,
    cacheStatus: "hit",
    totalNodes: 1,
    totalEdges: 1,
  });

  useIngestStore.setState({
    runs: [{ id: "run-a-1" }] as never,
    total: 1,
    selectedRunId: "run-a-1",
    runningCount: 1,
  });

  useActivityStore.setState({
    snapshot: { tasks: [{ run_id: "run-a-1" }] } as never,
    cancellingIds: new Set(["run-a-1"]),
  });

  useChatStore.setState({
    conversations: [
      { id: "conv-a-1", vault_id: VAULT_A, title: "A", created_at: "", updated_at: "" },
    ],
    activeConversationId: "conv-a-1",
    messages: [
      {
        id: "msg-a-1",
        conversation_id: "conv-a-1",
        role: "user",
        content: "hello from vault A",
        input_tokens: 0,
        output_tokens: 0,
        total_cost_usd: 0,
        created_at: "",
        citations: [],
      },
    ],
    streamingContent: "partial vault-A token stream",
    isStreaming: true,
  });

  useLintStore.setState({
    findings: [{ id: "finding-a-1" }] as never,
    findingsTotal: 1,
    selectedIds: new Set(["finding-a-1"]),
  });

  useReviewStore.setState({
    items: [{ id: "review-a-1" }] as never,
    total: 1,
    selectedIds: new Set(["review-a-1"]),
  });

  useResearchStore.setState({
    runs: [{ id: "research-a-1" }] as never,
    total: 1,
    selectedRunId: "research-a-1",
    detail: { id: "research-a-1" } as never,
  });

  useImportScheduleStore.setState({
    schedule: { id: "sched-a-1" } as never,
  });

  useProviderStore.setState({
    activeItem: { id: "provider-a-1" } as never,
    error: "vault-a transient error",
  });

  useStatusStore.setState({
    dataVersion: 42,
    reviewPending: 3,
  });
}

describe("resetAllVaultStores — FE-UIUX-3 cross-vault leak gate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedVaultAData();
  });

  it("adopts the new vaultId in appStore and clears vault-A selection state", () => {
    resetAllVaultStores(VAULT_B);
    const s = useAppStore.getState();
    expect(s.vaultId).toBe(VAULT_B);
    expect(s.selectedNodeId).toBeNull();
    expect(s.selectedSource).toBeNull();
    expect(s.showInsightsPanel).toBe(false);
  });

  it("clears graphStore — no vault-A nodes/edges/dataVersion remain", () => {
    resetAllVaultStores(VAULT_B);
    const s = useGraphStore.getState();
    expect(s.nodes).toEqual([]);
    expect(s.edges).toEqual([]);
    expect(s.dataVersion).toBeNull();
    expect(s.totalNodes).toBeNull();
  });

  it("clears ingestStore — no vault-A runs remain", () => {
    resetAllVaultStores(VAULT_B);
    const s = useIngestStore.getState();
    expect(s.runs).toEqual([]);
    expect(s.selectedRunId).toBeNull();
    expect(s.runningCount).toBe(0);
  });

  it("clears activityStore — no vault-A queue snapshot remains", () => {
    resetAllVaultStores(VAULT_B);
    const s = useActivityStore.getState();
    expect(s.snapshot).toBeNull();
    expect(s.cancellingIds.size).toBe(0);
  });

  it("clears chatStore AND aborts any in-flight vault-A stream", () => {
    const abortFn = vi.fn();
    useChatStore.setState({ streamAbortFn: abortFn });

    resetAllVaultStores(VAULT_B);

    expect(abortFn).toHaveBeenCalledTimes(1);
    const s = useChatStore.getState();
    expect(s.conversations).toEqual([]);
    expect(s.activeConversationId).toBeNull();
    expect(s.messages).toEqual([]);
    expect(s.streamingContent).toBe("");
    expect(s.isStreaming).toBe(false);
  });

  it("clears lintStore — no vault-A findings/selection remain", () => {
    resetAllVaultStores(VAULT_B);
    const s = useLintStore.getState();
    expect(s.findings).toEqual([]);
    expect(s.selectedIds.size).toBe(0);
  });

  it("clears reviewStore — no vault-A items/selection remain", () => {
    resetAllVaultStores(VAULT_B);
    const s = useReviewStore.getState();
    expect(s.items).toEqual([]);
    expect(s.selectedIds.size).toBe(0);
    expect(s.activeTab).toBe("pending");
  });

  it("clears researchStore — no vault-A runs/detail remain", () => {
    resetAllVaultStores(VAULT_B);
    const s = useResearchStore.getState();
    expect(s.runs).toEqual([]);
    expect(s.selectedRunId).toBeNull();
    expect(s.detail).toBeNull();
  });

  it("clears importScheduleStore — no vault-A schedule remains", () => {
    resetAllVaultStores(VAULT_B);
    expect(useImportScheduleStore.getState().schedule).toBeNull();
  });

  it("clears providerStore's derived activeItem — no vault-A provider config leaks", () => {
    resetAllVaultStores(VAULT_B);
    const s = useProviderStore.getState();
    expect(s.activeItem).toBeNull();
    expect(s.error).toBeNull();
  });

  it("clears statusStore's per-vault fields (dataVersion, reviewPending)", () => {
    resetAllVaultStores(VAULT_B);
    const s = useStatusStore.getState();
    expect(s.dataVersion).toBeNull();
    expect(s.reviewPending).toBeUndefined();
  });
});
