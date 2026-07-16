/**
 * SearchFilters.test.tsx — R8-5: Search filter UI tests (AC-R8-5-3).
 *
 * Coverage:
 *   A. searchClient.searchWiki — type param appended when types selected.
 *   B. searchClient.searchWiki — sort param appended when non-default sort.
 *   C. searchClient.searchWiki — no type/sort param when not set (backward compat).
 *   D. SearchView FilterBar — type chip toggles update activeTypes state.
 *   E. SearchView FilterBar — sort dropdown change updates sort state.
 *   F. SearchView — filter change triggers re-fetch with correct params.
 *   G. Existing search tests still green (no client-side crash when server ignores filters).
 *
 * AC-R8-5-3: filter state in component state; changing type selection calls client with
 *   `type` param; changing sort calls client with `sort` param.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// ─── Mock i18n (stable reference) ────────────────────────────────────────────

vi.mock("react-i18next", () => {
  const map: Record<string, string> = {
    "search.title": "Search",
    "search.inputLabel": "Search wiki",
    "search.placeholder": "Search wiki pages…",
    "search.clear": "Clear search",
    "search.minCharsHint": "Type at least 2 characters",
    "search.emptyTitle": "Search your wiki",
    "search.emptyBody": "Start typing to find pages.",
    "search.noResults": "No results",
    "search.noResultsHint": "Try different keywords.",
    "search.error": "Search error",
    "search.resultsLabel": "Search results",
    "search.score": "Score",
    "search.phaseVector": "vector",
    "search.phaseExpansion": "expansion",
    "search.filters.label": "Filters",
    "search.filters.typeLabel": "Type",
    "search.filters.allTypes": "All types",
    "search.sort.label": "Sort",
    "search.sort.relevance": "Relevance",
    "search.sort.date_desc": "Newest",
    "search.sort.date_asc": "Oldest",
    "search.pageType.concept": "Concept",
    "search.pageType.entity": "Entity",
    "search.pageType.source": "Source",
    "search.pageType.synthesis": "Synthesis",
    "search.pageType.comparison": "Comparison",
    "search.pageType.query": "Query",
    "common.loading": "Loading…",
    "common.unknown": "Unknown",
  };
  const t = (key: string): string => map[key] ?? key;
  const translation = { t };
  return { useTranslation: () => translation };
});

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSelectPage = vi.fn();
const mockSetActiveSection = vi.fn();

vi.mock("../store/appStore", () => ({
  useAppStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "vault-test",
      selectPage: mockSelectPage,
      setActiveSection: mockSetActiveSection,
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
}));

// ─── Import after mocks ───────────────────────────────────────────────────────

import * as searchClientModule from "../api/searchClient";
import type { SearchResponse } from "../api/searchClient";
import { SearchView } from "../components/search/SearchView";

// ─── Fixture ─────────────────────────────────────────────────────────────────

const MOCK_RESPONSE: SearchResponse = {
  query: "test",
  context: "[1] Test page content…",
  results: [
    { n: 1, id: "page-1", title: "Test Page", slug: "test-page", score: 0.9, phase: "vector" },
  ],
  data_version: 1,
  approx_tokens: 100,
  token_budget: 6553,
};

// ─── A. searchClient — type param (AC-R8-5-3) ────────────────────────────────

describe("searchClient.searchWiki — type filter param (R8-5, AC-R8-5-3)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("appends ?type=concept when types=['concept']", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { types: ["concept"] });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).toContain("type=concept");
  });

  it("appends ?type=concept,entity when types=['concept','entity']", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { types: ["concept", "entity"] });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).toContain("type=concept%2Centity");
  });

  it("does NOT append type param when types is empty", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { types: [] });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).not.toContain("type=");
  });

  it("does NOT append type param when types is undefined (existing callers unaffected)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    // Existing call pattern (no types) — backward compatibility
    await searchClientModule.searchWiki("test", { vault_id: "v1" });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).not.toContain("type=");
    expect(url).toContain("vault_id=v1");
  });
});

// ─── B. searchClient — sort param (AC-R8-5-3) ────────────────────────────────

describe("searchClient.searchWiki — sort param (R8-5, AC-R8-5-3)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("appends ?sort=date_desc when sort='date_desc'", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { sort: "date_desc" });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).toContain("sort=date_desc");
  });

  it("appends ?sort=date_asc when sort='date_asc'", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { sort: "date_asc" });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).toContain("sort=date_asc");
  });

  it("does NOT append sort param when sort='relevance' (default — no param sent)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test", { sort: "relevance" });

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).not.toContain("sort=");
  });

  it("does NOT append sort param when sort is undefined (existing callers unaffected)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("test");

    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).not.toContain("sort=");
  });
});

// ─── D. SearchView FilterBar — type chip toggles (AC-R8-5-3) ─────────────────

describe("SearchView FilterBar — type chips render and toggle", () => {
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  it("renders the filter bar", () => {
    render(<SearchView />);
    expect(screen.getByTestId("search-filter-bar")).toBeTruthy();
  });

  it("renders all 6 page type chips", () => {
    render(<SearchView />);
    const types = ["concept", "entity", "source", "synthesis", "comparison", "query"];
    for (const type of types) {
      expect(screen.getByTestId(`search-type-chip-${type}`)).toBeTruthy();
    }
  });

  it("type chip starts with aria-pressed=false (not active)", () => {
    render(<SearchView />);
    const conceptChip = screen.getByTestId("search-type-chip-concept") as HTMLButtonElement;
    expect(conceptChip.getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking a type chip toggles it active (aria-pressed=true)", () => {
    render(<SearchView />);
    const conceptChip = screen.getByTestId("search-type-chip-concept") as HTMLButtonElement;
    fireEvent.click(conceptChip);
    expect(conceptChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking an active chip deactivates it (aria-pressed back to false)", () => {
    render(<SearchView />);
    const chip = screen.getByTestId("search-type-chip-entity") as HTMLButtonElement;
    fireEvent.click(chip); // activate
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(chip); // deactivate
    expect(chip.getAttribute("aria-pressed")).toBe("false");
  });

  it("multiple chips can be active simultaneously (multi-toggle)", () => {
    render(<SearchView />);
    const conceptChip = screen.getByTestId("search-type-chip-concept") as HTMLButtonElement;
    const entityChip = screen.getByTestId("search-type-chip-entity") as HTMLButtonElement;
    fireEvent.click(conceptChip);
    fireEvent.click(entityChip);
    expect(conceptChip.getAttribute("aria-pressed")).toBe("true");
    expect(entityChip.getAttribute("aria-pressed")).toBe("true");
  });
});

// ─── E. SearchView FilterBar — sort dropdown (AC-R8-5-3) ─────────────────────

describe("SearchView FilterBar — sort dropdown", () => {
  it("renders the sort dropdown with default value 'relevance'", () => {
    render(<SearchView />);
    const select = screen.getByTestId("search-sort-select") as HTMLSelectElement;
    expect(select).toBeTruthy();
    expect(select.value).toBe("relevance");
  });

  it("sort dropdown has all 3 options", () => {
    render(<SearchView />);
    const select = screen.getByTestId("search-sort-select") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("relevance");
    expect(options).toContain("date_desc");
    expect(options).toContain("date_asc");
  });

  it("changing sort dropdown updates the select value", () => {
    render(<SearchView />);
    const select = screen.getByTestId("search-sort-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "date_desc" } });
    expect(select.value).toBe("date_desc");
  });
});

// ─── F. SearchView — filter change triggers re-fetch with params (AC-R8-5-3) ──

describe("SearchView — filter change re-fetches with correct params", () => {
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  afterEach(() => vi.restoreAllMocks());

  it("type chip selection includes 'type' param in the search call", async () => {
    const spy = vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);

    // Type a query first to trigger a search
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "test query" } });

    // Wait for the initial debounced search
    await waitFor(() => expect(spy).toHaveBeenCalled(), { timeout: 2000 });
    spy.mockClear();

    // Now toggle the 'concept' type chip — should trigger re-fetch
    const conceptChip = screen.getByTestId("search-type-chip-concept");
    fireEvent.click(conceptChip);

    await waitFor(() => expect(spy).toHaveBeenCalled(), { timeout: 2000 });

    // The most recent call should include types: ["concept"]
    const lastCall = spy.mock.calls[spy.mock.calls.length - 1];
    expect(lastCall?.[1]).toMatchObject({ types: ["concept"] });
  });

  it("sort change includes 'sort' param in the search call", async () => {
    const spy = vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "test query" } });

    await waitFor(() => expect(spy).toHaveBeenCalled(), { timeout: 2000 });
    spy.mockClear();

    // Change sort to "date_desc"
    const select = screen.getByTestId("search-sort-select");
    fireEvent.change(select, { target: { value: "date_desc" } });

    await waitFor(() => expect(spy).toHaveBeenCalled(), { timeout: 2000 });

    const lastCall = spy.mock.calls[spy.mock.calls.length - 1];
    expect(lastCall?.[1]).toMatchObject({ sort: "date_desc" });
  });

  it("no client-side crash when server ignores filters (AC-R8-5-3 guard)", async () => {
    // Server returns normal results regardless of filter params — UI must not break.
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue({
      ...MOCK_RESPONSE,
      // Server ignores type/sort and returns full results — same shape as always
      results: MOCK_RESPONSE.results,
    });

    render(<SearchView />);

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "test" } });

    // Toggle a type chip
    const entityChip = screen.getByTestId("search-type-chip-entity");
    fireEvent.click(entityChip);

    // Change sort
    const select = screen.getByTestId("search-sort-select");
    fireEvent.change(select, { target: { value: "date_asc" } });

    // Wait for results to render — no crash
    await waitFor(() => screen.queryByTestId("search-results") !== null, { timeout: 2000 });

    // UI still renders results (server response unchanged regardless of ignored params)
    expect(screen.queryByTestId("search-error")).toBeNull();
  });
});

// ─── G. Existing search tests regression ─────────────────────────────────────

describe("SearchView — existing functionality still green (regression)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders search input on mount", () => {
    render(<SearchView />);
    expect(screen.getByTestId("search-input")).toBeTruthy();
  });

  it("renders empty state on mount (no query)", () => {
    render(<SearchView />);
    expect(screen.getByTestId("search-empty-state")).toBeTruthy();
  });

  it("does not render loading or error initially", () => {
    render(<SearchView />);
    expect(screen.queryByTestId("search-loading")).toBeNull();
    expect(screen.queryByTestId("search-error")).toBeNull();
  });

  it("fires search and renders results (existing path, no filters)", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "test" } });

    await waitFor(() => screen.getByTestId("search-results"), { timeout: 2000 });
    expect(screen.getAllByTestId("search-result-row")).toHaveLength(1);
  });

  it("clicking a result calls selectPage and setActiveSection('pages') (unchanged)", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "test" } });

    await waitFor(() => screen.getAllByTestId("search-result-row"), { timeout: 2000 });
    fireEvent.click(screen.getAllByTestId("search-result-row")[0]!);

    expect(mockSelectPage).toHaveBeenCalledWith("page-1", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});
