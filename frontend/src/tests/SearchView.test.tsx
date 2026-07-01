/**
 * SearchView.test.tsx — unit tests for SearchView (F5, llm_wiki parity).
 *
 * Covers:
 *   A. searchClient.searchWiki — correct URL construction, response shape, error.
 *   B. SearchView — empty state on mount, results render, result click selects page.
 *   C. SearchResultItem TS shape verification (n/id/title/slug/score/phase).
 *
 * Note on debounce: SearchView uses a 300ms debounce internally. Tests that need
 * to trigger search bypass the debounce by mocking the module-level timer or by
 * advancing fake timers in a controlled way.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
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
        "common.loading": "Loading…",
        "common.unknown": "Unknown",
      };
      return map[key] ?? key;
    },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSelectPage = vi.fn();
const mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "vault-test",
      selectPage: mockSelectPage,
      setActiveSection: mockSetActiveSection,
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
}));

// ─── Import modules ───────────────────────────────────────────────────────────

import * as searchClientModule from "../api/searchClient";
import type { SearchResultItem, SearchResponse } from "../api/searchClient";
import { SearchView } from "../components/search/SearchView";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_RESULT_VECTOR: SearchResultItem = {
  n: 1,
  id: "page-uuid-1",
  title: "Homelab Setup",
  slug: "homelab-setup",
  score: 0.87,
  phase: "vector",
};

const MOCK_RESULT_EXPANSION: SearchResultItem = {
  n: 2,
  id: "page-uuid-2",
  title: "Network Configuration",
  slug: "network-configuration",
  score: 0.72,
  phase: "expansion",
};

const MOCK_RESPONSE: SearchResponse = {
  query: "homelab",
  context: "[1] Homelab Setup content…",
  results: [MOCK_RESULT_VECTOR, MOCK_RESULT_EXPANSION],
  data_version: 42,
  approx_tokens: 128,
  token_budget: 6553,
};

// ─── A. searchClient.searchWiki ───────────────────────────────────────────────

describe("searchClient.searchWiki — shape and URL", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls fetch with correct URL params", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchClientModule.searchWiki("homelab", { vault_id: "v1", k: 5 });

    expect(fetchSpy).toHaveBeenCalled();
    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url).toContain("q=homelab");
    expect(url).toContain("vault_id=v1");
    expect(url).toContain("k=5");
  });

  it("returns correctly typed SearchResponse", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await searchClientModule.searchWiki("homelab");

    expect(result.query).toBe("homelab");
    expect(result.results).toHaveLength(2);

    const first = result.results[0];
    expect(typeof first?.n).toBe("number");
    expect(typeof first?.id).toBe("string");
    expect(typeof first?.title).toBe("string");
    expect(typeof first?.slug).toBe("string");
    expect(typeof first?.score).toBe("number");
    expect(first?.phase === "vector" || first?.phase === "expansion").toBe(true);
  });

  it("throws ApiError on non-200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }),
    );

    await expect(searchClientModule.searchWiki("nonexistent")).rejects.toThrow();
  });

  it("mirrors exact SearchResultItem shape (n/id/title/slug/score/phase)", () => {
    // Shape verification: TypeScript compile-time check mirrored as a runtime assertion.
    const item: SearchResultItem = {
      n: 1,
      id: "abc",
      title: "Title",
      slug: "title",
      score: 0.9,
      phase: "vector",
    };
    expect(Object.keys(item).sort()).toEqual(
      ["id", "n", "phase", "score", "slug", "title"],
    );
  });
});

// ─── B. SearchView — static rendering ────────────────────────────────────────

describe("SearchView — initial state", () => {
  it("renders the search input", () => {
    render(<SearchView />);
    expect(screen.getByTestId("search-input")).toBeTruthy();
  });

  it("renders the empty state when query is blank", () => {
    render(<SearchView />);
    expect(screen.getByTestId("search-empty-state")).toBeTruthy();
  });

  it("does NOT show loading or error initially", () => {
    render(<SearchView />);
    expect(screen.queryByTestId("search-loading")).toBeNull();
    expect(screen.queryByTestId("search-error")).toBeNull();
  });
});

// ─── B. SearchView — query flow (using fake timers) ──────────────────────────

describe("SearchView — query flow", () => {
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not fire search for a single-char query (below minLength)", async () => {
    const spy = vi
      .spyOn(searchClientModule, "searchWiki")
      .mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "h" } });
      vi.advanceTimersByTime(400);
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("fires search after debounce when query >= 2 chars", async () => {
    const spy = vi
      .spyOn(searchClientModule, "searchWiki")
      .mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "ho" } });
      vi.advanceTimersByTime(400);
    });

    // Flush promises after timer fires
    await act(async () => {
      await Promise.resolve();
    });

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(
      "ho",
      expect.objectContaining({ vault_id: "vault-test" }),
    );
  });

  it("renders result rows after successful search", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "homelab" } });
      vi.advanceTimersByTime(400);
    });

    await act(async () => {
      await Promise.resolve();
    });

    await waitFor(() => screen.getByTestId("search-results"));

    const rows = screen.getAllByTestId("search-result-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("Homelab Setup")).toBeTruthy();
  });

  it("renders no-results state when results array is empty", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue({
      ...MOCK_RESPONSE,
      results: [],
    });

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "xyzabc" } });
      vi.advanceTimersByTime(400);
    });

    await act(async () => { await Promise.resolve(); });

    await waitFor(() => screen.getByTestId("search-no-results"));
  });

  it("renders error state when search rejects", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockRejectedValue(
      new Error("Service unavailable"),
    );

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "query" } });
      vi.advanceTimersByTime(400);
    });

    await act(async () => { await Promise.resolve(); });

    await waitFor(() => screen.getByTestId("search-error"));
  });

  it("clicking a result calls selectPage and navigates to 'pages' section", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");

    await act(async () => {
      fireEvent.change(input, { target: { value: "homelab" } });
      vi.advanceTimersByTime(400);
    });

    await act(async () => { await Promise.resolve(); });

    await waitFor(() => screen.getAllByTestId("search-result-row"));

    const firstRow = screen.getAllByTestId("search-result-row")[0]!;
    await act(async () => { fireEvent.click(firstRow); });

    expect(mockSelectPage).toHaveBeenCalledWith("page-uuid-1", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});
