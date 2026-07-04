/**
 * mobileShellWiring.test.tsx — ADR-0057 §5: shell wiring smoke tests.
 *
 * Verifies that:
 *   1. The tree drawer button appears in the DOM on the "mobile" viewport tier.
 *   2. The preview drawer button appears on "mobile" and "tablet" tiers.
 *   3. Clicking the tree button opens the tree drawer (uiStore.treeDrawerOpen).
 *   4. Clicking the preview button opens the preview drawer.
 *
 * PanelGroup has heavy dependencies (react-resizable-panels, NavTree, etc.)
 * so they are all mocked to keep these tests fast and focused on the wiring.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { useUiStore } from "../store/uiStore";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "panels.openTree": "Open wiki tree",
        "panels.openPreview": "Open page details",
        "panels.expandPanel": "Expand panel",
        "panels.collapsePanel": "Collapse panel",
        "panels.expand": "Expand",
        "panels.collapse": "Collapse",
      };
      return map[key] ?? key;
    },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => {
  // PanelGroup calls useGraphStore.getState() imperatively inside a useEffect
  // (to lazily hydrate the graph without subscribing). The mock must attach
  // getState as a method on the hook function itself, matching Zustand's API.
  const storeState = {
    vaultId: "vault-1",
    nodes: [] as unknown[],
    edges: [] as unknown[],
    selectedNodeId: null as string | null,
    setGraph: vi.fn(),
  };
  const useGraphStore = (selector: (s: typeof storeState) => unknown) => selector(storeState);
  useGraphStore.getState = () => storeState;
  return {
    useGraphStore,
    selectVaultId: (s: { vaultId: string }) => s.vaultId,
    selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
  };
});

// ─── Mock API ─────────────────────────────────────────────────────────────────

vi.mock("../api/graphClient", () => ({
  fetchGraph: vi.fn().mockResolvedValue({
    data: { nodes: [], edges: [], data_version: 1, communities: [] },
    cacheStatus: "hit",
  }),
}));

// ─── Mock child components (heavy) ────────────────────────────────────────────

vi.mock("../components/nav/NavTree", () => ({
  NavTree: () => <div data-testid="mock-nav-tree">NavTree</div>,
}));

vi.mock("../components/wiki/NoteView", () => ({
  NoteView: () => <div data-testid="mock-note-view">NoteView</div>,
}));

vi.mock("../components/preview/PreviewPanel", () => ({
  PreviewPanel: () => <div data-testid="mock-preview-panel">PreviewPanel</div>,
}));

vi.mock("../components/common/ScenarioTemplates", () => ({
  ScenarioTemplates: () => null,
}));

// ─── Mock react-resizable-panels ──────────────────────────────────────────────

vi.mock("react-resizable-panels", () => {
  // Inline prop types avoid importing the React namespace (new JSX transform).
  type Children = { children?: unknown; className?: string; style?: Record<string, unknown> };
  const Panel = ({ children, className, style }: Children) => (
    <div className={className} style={style as never}>
      {children as never}
    </div>
  );
  const Group = ({ children, style }: Children) => (
    <div style={style as never}>{children as never}</div>
  );
  const Separator = ({ className }: { className?: string }) => <div className={className} />;
  return {
    Group,
    Panel,
    Separator,
    usePanelRef: () => ({ current: { collapse: vi.fn(), expand: vi.fn() } }),
  };
});

// ─── Mock useViewport ────────────────────────────────────────────────────────

let mockViewportTier: "mobile" | "tablet" | "desktop" = "desktop";

vi.mock("../hooks/useViewport", () => ({
  useViewport: () => mockViewportTier,
}));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  cleanup();
  mockViewportTier = "desktop";
  // Reset uiStore to defaults
  act(() => {
    const s = useUiStore.getState();
    s.setLeftPanelOpen(true);
    s.setRightPanelOpen(true);
    s.closeTreeDrawer();
    s.closePreviewDrawer();
  });
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("mobile shell wiring — tree drawer button (ADR-0057 §5)", () => {
  it("tree button NOT rendered on desktop tier", async () => {
    mockViewportTier = "desktop";
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.queryByTestId("open-tree-drawer-btn")).toBeNull();
  });

  it("tree button rendered on mobile tier", async () => {
    mockViewportTier = "mobile";
    vi.resetModules();
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.getByTestId("open-tree-drawer-btn")).not.toBeNull();
  });

  it("tree button NOT rendered on tablet tier (tree panel stays visible)", async () => {
    mockViewportTier = "tablet";
    vi.resetModules();
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.queryByTestId("open-tree-drawer-btn")).toBeNull();
  });

  it("clicking tree button opens tree drawer (treeDrawerOpen=true)", async () => {
    mockViewportTier = "mobile";
    vi.resetModules();
    // Import both from the same fresh module registry so both PanelGroup and the
    // assertion operate on the SAME Zustand store instance (vi.resetModules()
    // creates a new store instance; importing useUiStore from the same registry
    // ensures the assertion sees the state written by the click handler).
    const [{ PanelGroup }, { useUiStore: freshUiStore }] = await Promise.all([
      import("../components/panels/PanelGroup"),
      import("../store/uiStore"),
    ]);
    render(<PanelGroup />);
    const btn = screen.getByTestId("open-tree-drawer-btn");
    act(() => {
      fireEvent.click(btn);
    });
    expect(freshUiStore.getState().treeDrawerOpen).toBe(true);
  });
});

describe("mobile shell wiring — preview drawer button (ADR-0057 §5)", () => {
  it("preview button NOT rendered on desktop tier", async () => {
    mockViewportTier = "desktop";
    vi.resetModules();
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.queryByTestId("open-preview-drawer-btn")).toBeNull();
  });

  it("preview button rendered on mobile tier", async () => {
    mockViewportTier = "mobile";
    vi.resetModules();
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.getByTestId("open-preview-drawer-btn")).not.toBeNull();
  });

  it("preview button rendered on tablet tier", async () => {
    mockViewportTier = "tablet";
    vi.resetModules();
    const { PanelGroup } = await import("../components/panels/PanelGroup");
    render(<PanelGroup />);
    expect(screen.getByTestId("open-preview-drawer-btn")).not.toBeNull();
  });

  it("clicking preview button opens preview drawer (previewDrawerOpen=true)", async () => {
    mockViewportTier = "mobile";
    vi.resetModules();
    // Same fresh-registry pattern: both PanelGroup and the assertion must use
    // the same Zustand store created after vi.resetModules().
    const [{ PanelGroup }, { useUiStore: freshUiStore }] = await Promise.all([
      import("../components/panels/PanelGroup"),
      import("../store/uiStore"),
    ]);
    render(<PanelGroup />);
    const btn = screen.getByTestId("open-preview-drawer-btn");
    act(() => {
      fireEvent.click(btn);
    });
    expect(freshUiStore.getState().previewDrawerOpen).toBe(true);
  });
});
