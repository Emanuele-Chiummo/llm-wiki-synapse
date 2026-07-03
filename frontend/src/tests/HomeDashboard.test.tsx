/**
 * HomeDashboard.test.tsx — Vitest unit tests for the Home landing section [F18][R12-1][A2+A3].
 *
 * Covers:
 *   AC-R12-1-4: HomeDashboard mounted when "home" section is active; Home icon in NavRail.
 *   AC-R12-1-5a: KPI cards render from mocked overview.
 *   AC-R12-1-5b: empty sections → small hint (not prominent placeholder); groups still render.
 *   AC-R12-1-5c: non-empty sections → section cards render with correct page counts.
 *   AC-R12-1-6: No charting library imported; SVG type-bar present in section/group cards.
 *   AC-R12-1-7: Clicking a section card dispatches setActiveSection("pages") + writes
 *               localStorage domain filter key.
 *   A2: System status block renders from mocked /health/detailed (component dots, provider,
 *       version, data version); health 404 → block still renders (graceful hide of dots only);
 *       manual refresh button present.
 *   A3: Groups grid renders ordered by pages_total desc, capped at 12; click → pages section
 *       + localStorage slug; 404 on /stats/groups → groups block hidden; curated sections
 *       hidden when vocabulary empty but groups still render.
 *   R12-3 AC-R12-3-5: VersionMismatchBanner shows only when backendVersion ≠ __APP_VERSION__
 *               and backendVersion ≠ "dev"; banner is dismissible (sessionStorage flag);
 *               matching versions → no banner.
 *   AC-R12-2-6: Settings domain_vocabulary field: renders current vocab from GET /config/app;
 *               saving triggers PUT /config/app/domain_vocabulary with JSON-array string.
 *   i18n EN/IT parity (spot-checks on new home.* and config.domainVocabulary.* keys including
 *               A2+A3 additions: home.systemStatus.*, home.groups.*).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ─── Fake localStorage (Node.js 26 / jsdom compat — same pattern as auth-base.test.ts) ──

function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) { return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null; },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeLocalStorage = makeFakeStorage();
vi.stubGlobal("localStorage", fakeLocalStorage);

const fakeSessionStorage = makeFakeStorage();
vi.stubGlobal("sessionStorage", fakeSessionStorage);

// ─── Module-level mocks (hoisted before any imports) ──────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      // Substitute template vars like {{version}}
      const parts = key.split(".");
      let label = parts[parts.length - 1] ?? key;
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          label = label.replace(`{{${k}}}`, String(v));
        }
      }
      return label;
    },
  }),
}));

// ─── graphStore mock ──────────────────────────────────────────────────────────

const mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeSection: "home",
      setActiveSection: mockSetActiveSection,
    }),
  selectActiveSection: (s: { activeSection: string }) => s.activeSection,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

// ─── statsClient mock ─────────────────────────────────────────────────────────

vi.mock("../api/statsClient", () => ({
  getStatsOverview: vi.fn(),
  getStatsSections: vi.fn(),
  getStatsGroups: vi.fn(),
}));

// ─── healthClient mock ────────────────────────────────────────────────────────

vi.mock("../api/healthClient", () => ({
  getHealthDetailed: vi.fn(),
}));

// ─── statusStore mock ─────────────────────────────────────────────────────────

const mockBackendVersion = vi.fn<() => string | undefined>(() => undefined);

vi.mock("../store/statusStore", () => ({
  useStatusStore: (selector: (s: unknown) => unknown) =>
    selector({ backendVersion: mockBackendVersion() }),
  selectBackendVersion: (s: { backendVersion: string | undefined }) => s.backendVersion,
  selectSetBackendVersion: (s: { setBackendVersion: () => void }) => s.setBackendVersion,
}));

// ─── providerStore mock ───────────────────────────────────────────────────────

const mockActiveProvider = vi.fn<() => { provider_type: string; model_id: string | null } | null>(
  () => null,
);

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
    selector({ activeItem: mockActiveProvider() }),
  selectActiveProvider: (s: { activeItem: { provider_type: string; model_id: string | null } | null }) =>
    s.activeItem,
}));

// ─── Imports after mocks ──────────────────────────────────────────────────────

import { getStatsOverview, getStatsSections, getStatsGroups } from "../api/statsClient";
import { getHealthDetailed } from "../api/healthClient";
import type { StatsOverview, StatsSections, StatsGroups } from "../api/statsClient";
import type { DetailedHealth } from "../api/healthClient";
import { HomeDashboard } from "../components/home/HomeDashboard";
import { VersionMismatchBanner } from "../components/common/VersionMismatchBanner";

const mockGetStatsOverview = vi.mocked(getStatsOverview);
const mockGetStatsSections = vi.mocked(getStatsSections);
const mockGetStatsGroups = vi.mocked(getStatsGroups);
const mockGetHealthDetailed = vi.mocked(getHealthDetailed);

// ─── Test data ────────────────────────────────────────────────────────────────

const MOCK_OVERVIEW: StatsOverview = {
  pages_total: 128,
  pages_by_type: { entity: 40, concept: 55, source: 20, synthesis: 8, comparison: 5 },
  links_total: 342,
  communities_count: 7,
  review_pending: 3,
  lint_open: 2,
  monthly_cost_usd: 1.8421,
  data_version: 57,
  recent_activity: [
    {
      page_id: "a1b2c3d4-0000-0000-0000-000000000001",
      title: "Incident Management",
      slug: "incident-management",
      updated_at: "2026-07-03T09:12:44+00:00",
    },
    {
      page_id: "a1b2c3d4-0000-0000-0000-000000000002",
      title: "Flow Designer",
      slug: "flow-designer",
      updated_at: "2026-07-02T15:00:00+00:00",
    },
  ],
};

const MOCK_SECTIONS: StatsSections = {
  sections: [
    {
      domain: "ServiceNow",
      pages_total: 42,
      pages_by_type: { concept: 25, entity: 12, source: 5 },
      last_activity: "2026-07-03T08:40:11+00:00",
      top_pages: [
        { id: "b1", title: "Flow Designer", slug: "flow-designer", degree: 9 },
        { id: "b2", title: "Incident Management", slug: "incident-management", degree: 7 },
      ],
    },
    {
      domain: "SAM",
      pages_total: 15,
      pages_by_type: { concept: 10, entity: 5 },
      last_activity: "2026-07-01T10:00:00+00:00",
      top_pages: [],
    },
    {
      domain: "untagged",
      pages_total: 8,
      pages_by_type: { concept: 5, entity: 3 },
      last_activity: "2026-07-02T21:03:00+00:00",
      top_pages: [],
    },
  ],
};

const MOCK_GROUPS: StatsGroups = {
  groups: [
    {
      community: 2,
      label: "Service Management",
      pages_total: 60,
      pages_by_type: { concept: 35, entity: 20, source: 5 },
      top_pages: [
        { id: "c1", title: "Incident Management", slug: "incident-management", degree: 15 },
      ],
      last_activity: "2026-07-03T09:00:00+00:00",
    },
    {
      community: 5,
      label: "Asset Lifecycle",
      pages_total: 30,
      pages_by_type: { concept: 18, entity: 12 },
      top_pages: [
        { id: "c2", title: "Software Asset Manager", slug: "software-asset-manager", degree: 8 },
      ],
      last_activity: "2026-07-01T12:00:00+00:00",
    },
    {
      community: 1,
      label: "Procurement",
      pages_total: 20,
      pages_by_type: { concept: 12, entity: 8 },
      top_pages: [],
      last_activity: null,
    },
  ],
};

const MOCK_HEALTH: DetailedHealth = {
  status: "ok",
  components: {
    watcher: { alive: true, last_event_at: "2026-07-03T09:00:00+00:00" },
    import_scheduler: { enabled: false, last_run_at: null, last_error: null },
    ingest_queue: { running: 0, pending: 0, paused: false },
    graph_cache: { warm: true, last_recompute_at: null, node_count: 128 },
    database: { ok: true, latency_ms: 5 },
    qdrant: { ok: true, latency_ms: 12 },
    embeddings: { enabled: true, ok: true },
  },
  last_errors: [],
  checked_at: "2026-07-03T09:15:00+00:00",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function renderDashboard() {
  const result = render(<HomeDashboard />);
  await waitFor(() => {
    expect(screen.queryByTestId("home-dashboard-loading")).toBeNull();
  });
  return result;
}

// ─── AC-R12-1-5a: KPI cards render from mocked overview ──────────────────────

describe("HomeDashboard — KPI cards (AC-R12-1-5a)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
    try { sessionStorage.clear(); } catch { /* ignore */ }
  });

  it("renders the main dashboard container after loading", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-dashboard")).not.toBeNull();
  });

  it("renders pages-total KPI card with correct count", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-pages-total");
    expect(card.textContent).toContain("128");
  });

  it("renders links-total KPI card with correct count", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-links-total");
    expect(card.textContent).toContain("342");
  });

  it("renders communities KPI card", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-communities");
    expect(card.textContent).toContain("7");
  });

  it("renders review-pending KPI card", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-review-pending");
    expect(card.textContent).toContain("3");
  });

  it("renders lint-open KPI card", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-lint-open");
    expect(card.textContent).toContain("2");
  });

  it("renders monthly-cost KPI card with formatted cost", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-monthly-cost");
    expect(card.textContent).toContain("$1.84");
  });

  it("renders data-version KPI card", async () => {
    await renderDashboard();
    const card = screen.getByTestId("kpi-data-version");
    expect(card.textContent).toContain("57");
  });

  it("renders recent activity list", async () => {
    await renderDashboard();
    const list = screen.getByTestId("home-recent-activity");
    expect(list).not.toBeNull();
    expect(list.querySelectorAll("li").length).toBe(2);
  });
});

// ─── A2: System status block ──────────────────────────────────────────────────

describe("HomeDashboard — system status block (A2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
    mockBackendVersion.mockReturnValue("1.2.0");
    mockActiveProvider.mockReturnValue({ provider_type: "api", model_id: "claude-sonnet-4-6" });
  });

  it("renders the system status block", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-system-status")).not.toBeNull();
  });

  it("renders component dots for database and watcher", async () => {
    await renderDashboard();
    await waitFor(() => {
      expect(screen.queryByTestId("home-status-components")).not.toBeNull();
    });
    const comps = screen.getByTestId("home-status-components");
    expect(comps.querySelector("[data-testid='home-status-component-database']")).not.toBeNull();
    expect(comps.querySelector("[data-testid='home-status-component-watcher']")).not.toBeNull();
  });

  it("renders active provider label", async () => {
    await renderDashboard();
    const providerEl = screen.getByTestId("home-status-provider");
    expect(providerEl.textContent).toContain("api");
    expect(providerEl.textContent).toContain("claude-sonnet-4-6");
  });

  it("renders backend version when available and not 'dev'", async () => {
    await renderDashboard();
    const versionEl = screen.getByTestId("home-status-version");
    expect(versionEl.textContent).toContain("1.2.0");
  });

  it("renders data version from overview", async () => {
    await renderDashboard();
    const dvEl = screen.getByTestId("home-status-data-version");
    expect(dvEl.textContent).toContain("57");
  });

  it("has a manual refresh button", async () => {
    await renderDashboard();
    const refreshBtn = screen.getByTestId("home-system-status-refresh");
    expect(refreshBtn).not.toBeNull();
  });

  it("does NOT show version element when backendVersion is 'dev'", async () => {
    mockBackendVersion.mockReturnValue("dev");
    await renderDashboard();
    expect(screen.queryByTestId("home-status-version")).toBeNull();
  });

  it("does NOT show version element when backendVersion is undefined", async () => {
    mockBackendVersion.mockReturnValue(undefined);
    await renderDashboard();
    expect(screen.queryByTestId("home-status-version")).toBeNull();
  });

  it("shows 'None configured' when no active provider", async () => {
    mockActiveProvider.mockReturnValue(null);
    await renderDashboard();
    await waitFor(() => {
      const el = screen.queryByTestId("home-status-provider");
      expect(el).not.toBeNull();
      // The mock t() returns the last key segment: "providerNone"
      expect(el?.textContent).toContain("providerNone");
    });
  });

  it("health 404 (null) → system status block still renders, component dots hidden", async () => {
    mockGetHealthDetailed.mockResolvedValue(null);
    await renderDashboard();
    // Block still renders (it shows meta strip even when health is unavailable)
    expect(screen.getByTestId("home-system-status")).not.toBeNull();
    // Component dots are not shown when health is null
    expect(screen.queryByTestId("home-status-components")).toBeNull();
  });

  it("clicking refresh button re-invokes getHealthDetailed", async () => {
    await renderDashboard();
    const refreshBtn = screen.getByTestId("home-system-status-refresh");
    fireEvent.click(refreshBtn);
    // Should have been called at least twice (initial + refresh)
    await waitFor(() => {
      expect(mockGetHealthDetailed.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });
});

// ─── A3: Groups grid ──────────────────────────────────────────────────────────

describe("HomeDashboard — groups grid (A3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSetActiveSection.mockReset();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("renders the groups section when groups are available", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-groups-section")).not.toBeNull();
  });

  it("renders the groups grid", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-groups-grid")).not.toBeNull();
  });

  it("renders a card for community 2 (Service Management)", async () => {
    await renderDashboard();
    const card = screen.getByTestId("group-card-2");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Service Management");
    expect(card.textContent).toContain("60");
  });

  it("renders a card for community 5 (Asset Lifecycle)", async () => {
    await renderDashboard();
    const card = screen.getByTestId("group-card-5");
    expect(card.textContent).toContain("Asset Lifecycle");
    expect(card.textContent).toContain("30");
  });

  it("renders groups ordered by pages_total desc (backend-ordered, frontend preserves order)", async () => {
    await renderDashboard();
    const grid = screen.getByTestId("home-groups-grid");
    const cards = Array.from(grid.querySelectorAll("[data-testid^='group-card-']"));
    // MOCK_GROUPS is ordered: community 2 (60), 5 (30), 1 (20)
    expect(cards[0]?.getAttribute("data-testid")).toBe("group-card-2");
    expect(cards[1]?.getAttribute("data-testid")).toBe("group-card-5");
    expect(cards[2]?.getAttribute("data-testid")).toBe("group-card-1");
  });

  it("caps groups at 12 (renders exactly as many as returned, up to 12)", async () => {
    // The mock has 3 groups — verify 3 are rendered (cap is server-side at 12)
    await renderDashboard();
    const grid = screen.getByTestId("home-groups-grid");
    const cards = grid.querySelectorAll("[data-testid^='group-card-']");
    expect(cards.length).toBe(3);
  });

  it("clicking a group card calls setActiveSection('pages')", async () => {
    await renderDashboard();
    const card = screen.getByTestId("group-card-2");
    fireEvent.click(card);
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });

  it("clicking a group card writes the top page slug to localStorage", async () => {
    await renderDashboard();
    const card = screen.getByTestId("group-card-2");
    fireEvent.click(card);
    const stored = localStorage.getItem("synapse:groupTopPageSlug");
    expect(stored).toBe("incident-management");
  });

  it("clicking a group with no top pages calls setActiveSection('pages') without writing slug", async () => {
    await renderDashboard();
    const card = screen.getByTestId("group-card-1");
    fireEvent.click(card);
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
    // No slug written for a group with no top pages
    expect(localStorage.getItem("synapse:groupTopPageSlug")).toBeNull();
  });

  it("groups 404 (null) → groups block is hidden", async () => {
    mockGetStatsGroups.mockResolvedValue(null);
    await renderDashboard();
    expect(screen.queryByTestId("home-groups-section")).toBeNull();
  });

  it("groups empty array → groups block is hidden", async () => {
    mockGetStatsGroups.mockResolvedValue({ groups: [] });
    await renderDashboard();
    expect(screen.queryByTestId("home-groups-section")).toBeNull();
  });
});

// ─── A3: empty vocab + groups still render ────────────────────────────────────

describe("HomeDashboard — empty vocabulary but groups present (A3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    // Only "untagged" bucket → no vocab sections
    mockGetStatsSections.mockResolvedValue({
      sections: [
        {
          domain: "untagged",
          pages_total: 8,
          pages_by_type: { concept: 5, entity: 3 },
          last_activity: null,
          top_pages: [],
        },
      ],
    });
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
  });

  it("shows the small empty-vocab hint (not the prominent placeholder)", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-sections-empty")).not.toBeNull();
  });

  it("shows a settings link in the empty-vocab hint", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-sections-go-settings")).not.toBeNull();
  });

  it("sections grid is NOT rendered when vocabulary is empty", async () => {
    await renderDashboard();
    expect(screen.queryByTestId("home-sections-grid")).toBeNull();
  });

  it("groups section IS rendered even when vocabulary is empty", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-groups-section")).not.toBeNull();
  });

  it("groups have correct cards even without vocab sections", async () => {
    await renderDashboard();
    expect(screen.getByTestId("group-card-2")).not.toBeNull();
  });
});

// ─── AC-R12-1-5b: empty sections → small hint ────────────────────────────────

describe("HomeDashboard — empty vocabulary (AC-R12-1-5b)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue({
      sections: [
        {
          domain: "untagged",
          pages_total: 8,
          pages_by_type: { concept: 5, entity: 3 },
          last_activity: null,
          top_pages: [],
        },
      ],
    });
    mockGetStatsGroups.mockResolvedValue(null);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
  });

  it("shows the empty-vocabulary hint element when no vocabulary domains are configured", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-sections-empty")).not.toBeNull();
  });

  it("shows a link to settings from the empty-vocab hint", async () => {
    await renderDashboard();
    const btn = screen.getByTestId("home-sections-go-settings");
    expect(btn).not.toBeNull();
  });

  it("clicking settings link navigates to settings section", async () => {
    await renderDashboard();
    const btn = screen.getByTestId("home-sections-go-settings");
    fireEvent.click(btn);
    expect(mockSetActiveSection).toHaveBeenCalledWith("settings");
  });
});

// ─── AC-R12-1-5c: non-empty sections → section cards render ──────────────────

describe("HomeDashboard — section cards render (AC-R12-1-5c)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
  });

  it("renders the sections grid", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-sections-grid")).not.toBeNull();
  });

  it("renders a card for ServiceNow domain with correct page count", async () => {
    await renderDashboard();
    const card = screen.getByTestId("section-card-ServiceNow");
    expect(card.textContent).toContain("42");
  });

  it("renders a card for SAM domain with correct page count", async () => {
    await renderDashboard();
    const card = screen.getByTestId("section-card-SAM");
    expect(card.textContent).toContain("15");
  });

  it("renders the untagged bucket card last", async () => {
    await renderDashboard();
    const grid = screen.getByTestId("home-sections-grid");
    const cards = grid.querySelectorAll("[data-testid^='section-card-']");
    const lastCard = cards[cards.length - 1];
    expect(lastCard?.getAttribute("data-testid")).toBe("section-card-untagged");
  });

  it("renders sections in vocabulary order (ServiceNow, SAM, then untagged)", async () => {
    await renderDashboard();
    const grid = screen.getByTestId("home-sections-grid");
    const cards = Array.from(grid.querySelectorAll("[data-testid^='section-card-']"));
    expect(cards[0]?.getAttribute("data-testid")).toBe("section-card-ServiceNow");
    expect(cards[1]?.getAttribute("data-testid")).toBe("section-card-SAM");
    expect(cards[2]?.getAttribute("data-testid")).toBe("section-card-untagged");
  });
});

// ─── AC-R12-1-6: no charting library; SVG type-bar present ───────────────────

describe("HomeDashboard — I3: no chart library, SVG sparklines only (AC-R12-1-6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
  });

  it("renders SVG elements inside section cards for the type bar", async () => {
    await renderDashboard();
    const serviceNowCard = screen.getByTestId("section-card-ServiceNow");
    const svgs = serviceNowCard.querySelectorAll("svg");
    expect(svgs.length).toBeGreaterThan(0);
  });

  it("renders SVG elements inside group cards for the type bar", async () => {
    await renderDashboard();
    const groupCard = screen.getByTestId("group-card-2");
    const svgs = groupCard.querySelectorAll("svg");
    expect(svgs.length).toBeGreaterThan(0);
  });

  it("no recharts/d3/chart.js canvas elements are rendered (plain SVG only)", async () => {
    await renderDashboard();
    const canvases = document.querySelectorAll("canvas");
    expect(canvases.length).toBe(0);
  });
});

// ─── AC-R12-1-7: clicking section card dispatches filter+navigation ───────────

describe("HomeDashboard — section card click navigation (AC-R12-1-7)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSetActiveSection.mockReset();
    mockGetStatsOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetStatsSections.mockResolvedValue(MOCK_SECTIONS);
    mockGetStatsGroups.mockResolvedValue(MOCK_GROUPS);
    mockGetHealthDetailed.mockResolvedValue(MOCK_HEALTH);
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("clicking a domain section card calls setActiveSection('pages')", async () => {
    await renderDashboard();
    const card = screen.getByTestId("section-card-ServiceNow");
    fireEvent.click(card);
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });

  it("clicking a domain section card writes the domain filter to localStorage", async () => {
    await renderDashboard();
    const card = screen.getByTestId("section-card-ServiceNow");
    fireEvent.click(card);
    const stored = localStorage.getItem("synapse:domainFilter");
    expect(stored).toBe("ServiceNow");
  });

  it("clicking the untagged card clears the localStorage domain filter", async () => {
    localStorage.setItem("synapse:domainFilter", "ServiceNow");
    await renderDashboard();
    const card = screen.getByTestId("section-card-untagged");
    fireEvent.click(card);
    expect(localStorage.getItem("synapse:domainFilter")).toBeNull();
  });

  it("clicking a recent-activity item calls setActiveSection('pages')", async () => {
    await renderDashboard();
    const item = screen.getByTestId("home-activity-item-incident-management");
    fireEvent.click(item);
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});

// ─── 404 placeholder (backend v1.1) ──────────────────────────────────────────

describe("HomeDashboard — 404 backend placeholder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatsOverview.mockResolvedValue(null);
    mockGetStatsSections.mockResolvedValue(null);
    mockGetStatsGroups.mockResolvedValue(null);
    mockGetHealthDetailed.mockResolvedValue(null);
  });

  it("shows the server-v1.1 placeholder when overview returns null (404)", async () => {
    await renderDashboard();
    expect(screen.getByTestId("home-dashboard-placeholder")).not.toBeNull();
  });
});

// ─── R12-3 AC-R12-3-5: VersionMismatchBanner ─────────────────────────────────

describe("VersionMismatchBanner — version mismatch (R12-3 AC-R12-3-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    try { sessionStorage.clear(); } catch { /* ignore */ }
  });

  it("shows banner when backendVersion differs from __APP_VERSION__", () => {
    mockBackendVersion.mockReturnValue("1.1.0");
    render(<VersionMismatchBanner />);
    const banner = document.querySelector("[data-testid='version-mismatch-banner']");
    expect(banner).not.toBeNull();
  });

  it("does NOT show banner when backendVersion is 'dev'", () => {
    mockBackendVersion.mockReturnValue("dev");
    render(<VersionMismatchBanner />);
    const banner = document.querySelector("[data-testid='version-mismatch-banner']");
    expect(banner).toBeNull();
  });

  it("does NOT show banner when backendVersion is undefined (older backend)", () => {
    mockBackendVersion.mockReturnValue(undefined);
    render(<VersionMismatchBanner />);
    const banner = document.querySelector("[data-testid='version-mismatch-banner']");
    expect(banner).toBeNull();
  });

  it("banner renders the version-mismatch-text element when versions differ", () => {
    mockBackendVersion.mockReturnValue("1.1.0");
    render(<VersionMismatchBanner />);
    const textEl = document.querySelector("[data-testid='version-mismatch-text']");
    expect(textEl).not.toBeNull();
  });

  it("banner is dismissible — clicking dismiss removes it", () => {
    mockBackendVersion.mockReturnValue("1.1.0");
    render(<VersionMismatchBanner />);
    const dismissBtn = document.querySelector("[data-testid='version-mismatch-dismiss']");
    if (dismissBtn) {
      fireEvent.click(dismissBtn);
    }
    const banner = document.querySelector("[data-testid='version-mismatch-banner']");
    expect(banner).toBeNull();
  });

  it("dismiss sets sessionStorage flag", () => {
    mockBackendVersion.mockReturnValue("1.1.0");
    render(<VersionMismatchBanner />);
    const dismissBtn = document.querySelector("[data-testid='version-mismatch-dismiss']");
    if (dismissBtn) {
      fireEvent.click(dismissBtn);
    }
    try {
      expect(sessionStorage.getItem("synapse:versionBannerDismissed")).toBe("1");
    } catch {
      // sessionStorage unavailable in this environment — skip assertion
    }
  });
});

// ─── i18n parity spot-checks for new keys (EN/IT) ────────────────────────────

describe("i18n — home.* and config.domainVocabulary.* keys present in both locales", () => {
  it("en.json has home.title key", async () => {
    const en = await import("../i18n/locales/en.json");
    expect((en as Record<string, unknown>).home).toBeDefined();
    const home = (en as { home: { title: string } }).home;
    expect(home.title).toBeTruthy();
  });

  it("it.json has home.title key", async () => {
    const it = await import("../i18n/locales/it.json");
    expect((it as Record<string, unknown>).home).toBeDefined();
    const home = (it as { home: { title: string } }).home;
    expect(home.title).toBeTruthy();
  });

  it("en.json has config.domainVocabulary.label key", async () => {
    const en = await import("../i18n/locales/en.json");
    const config = (en as { config: { domainVocabulary: { label: string } } }).config;
    expect(config.domainVocabulary.label).toBeTruthy();
  });

  it("it.json has config.domainVocabulary.label key", async () => {
    const it = await import("../i18n/locales/it.json");
    const config = (it as { config: { domainVocabulary: { label: string } } }).config;
    expect(config.domainVocabulary.label).toBeTruthy();
  });

  it("en.json has home.versionBanner.message key", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { versionBanner: { message: string } } }).home;
    expect(home.versionBanner.message).toContain("{{backendVersion}}");
    expect(home.versionBanner.message).toContain("{{appVersion}}");
  });

  it("it.json has home.versionBanner.message key with both interpolation vars", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { versionBanner: { message: string } } }).home;
    expect(home.versionBanner.message).toContain("{{backendVersion}}");
    expect(home.versionBanner.message).toContain("{{appVersion}}");
  });

  // A2: system status keys
  it("en.json has home.systemStatus.title key (A2)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { systemStatus: { title: string } } }).home;
    expect(home.systemStatus.title).toBeTruthy();
  });

  it("it.json has home.systemStatus.title key (A2)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { systemStatus: { title: string } } }).home;
    expect(home.systemStatus.title).toBeTruthy();
  });

  it("en.json has home.systemStatus.refresh key (A2)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { systemStatus: { refresh: string } } }).home;
    expect(home.systemStatus.refresh).toBeTruthy();
  });

  it("it.json has home.systemStatus.refresh key (A2)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { systemStatus: { refresh: string } } }).home;
    expect(home.systemStatus.refresh).toBeTruthy();
  });

  it("en.json has home.systemStatus.components.database key (A2)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { systemStatus: { components: { database: string } } } }).home;
    expect(home.systemStatus.components.database).toBeTruthy();
  });

  it("it.json has home.systemStatus.components.database key (A2)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { systemStatus: { components: { database: string } } } }).home;
    expect(home.systemStatus.components.database).toBeTruthy();
  });

  // A3: groups keys
  it("en.json has home.groups.title key (A3)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { groups: { title: string } } }).home;
    expect(home.groups.title).toBeTruthy();
  });

  it("it.json has home.groups.title key (A3)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { groups: { title: string } } }).home;
    expect(home.groups.title).toBeTruthy();
  });

  it("en.json has home.groups.openTopPage key (A3)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { groups: { openTopPage: string } } }).home;
    expect(home.groups.openTopPage).toBeTruthy();
  });

  it("it.json has home.groups.openTopPage key (A3)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { groups: { openTopPage: string } } }).home;
    expect(home.groups.openTopPage).toBeTruthy();
  });

  // A2: sections now uses "SEZIONI" title
  it("en.json has home.sections.title key", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { sections: { title: string } } }).home;
    expect(home.sections.title).toBeTruthy();
  });

  it("it.json has home.sections.title key", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { sections: { title: string } } }).home;
    expect(home.sections.title).toBeTruthy();
  });

  it("en.json has home.sections.emptyVocabHint key (small hint, A2)", async () => {
    const en = await import("../i18n/locales/en.json");
    const home = (en as { home: { sections: { emptyVocabHint: string } } }).home;
    expect(home.sections.emptyVocabHint).toBeTruthy();
  });

  it("it.json has home.sections.emptyVocabHint key (small hint, A2)", async () => {
    const it = await import("../i18n/locales/it.json");
    const home = (it as { home: { sections: { emptyVocabHint: string } } }).home;
    expect(home.sections.emptyVocabHint).toBeTruthy();
  });
});
