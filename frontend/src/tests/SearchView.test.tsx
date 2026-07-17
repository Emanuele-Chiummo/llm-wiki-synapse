/**
 * SearchView.test.tsx — unit tests for SearchView (F5, llm_wiki parity).
 *
 * Covers:
 *   A. searchClient.searchWiki — correct URL construction, response shape, error.
 *   B. SearchView — empty state on mount, results render, result click selects page.
 *   C. SearchResultItem TS shape verification (n/id/title/slug/score/phase).
 *   D. SearchView — loading skeleton, slow-load message, ErrorState on error (audit #6).
 *
 * Note on timers:
 *   - The "query flow" tests (B) use real timers because @testing-library waitFor
 *     polls on setInterval; fake timers deadlock with it.
 *   - The "slow-load" tests (D) use vi.useFakeTimers() within their own describe
 *     block and do NOT use waitFor — they advance timers and check state
 *     synchronously within act() calls.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

// IMPORTANT: `t` (and the object returned by useTranslation) must be a STABLE reference
// across renders. SearchView's debounce effect lists `t` in its dependency array; a fresh
// `t` per render would re-run the effect every render, and on an empty query the effect
// calls setResults([]) (a new array) → re-render → effect again → infinite loop that hangs
// the whole test file. react-i18next's real `t` is memoized/stable, so production never
// loops — this mock must mirror that stability. The factory runs once (module-cached), so
// building the stable objects inside it and returning the same reference each call is safe
// (and avoids the vi.mock hoisting trap of referencing outer variables).
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
    "search.takingLonger": "Taking longer than expected…",
    "search.cancel": "Cancel",
    "search.error": "Search error",
    "search.resultsLabel": "Search results",
    "search.score": "Score",
    "search.phaseVector": "vector",
    "search.phaseExpansion": "expansion",
    "common.loading": "Loading…",
    "common.retry": "Retry",
    "common.unknown": "Unknown",
    "errors.genericTitle": "Something went wrong",
    "errors.technicalDetails": "Technical details",
    "errors.copyDetails": "Copy details",
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
      new Response(
        JSON.stringify({
          error: { code: "not_found", message: "Not Found", status: 404, details: null },
        }),
        { status: 404 },
      ),
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
    expect(Object.keys(item).sort()).toEqual(["id", "n", "phase", "score", "slug", "title"]);
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
    expect(screen.queryByTestId("error-state")).toBeNull();
  });
});

// ─── B. SearchView — query flow (using real timers) ──────────────────────────

describe("SearchView — query flow", () => {
  // Real timers (NOT fake): fake timers deadlock with @testing-library's waitFor, which
  // polls on timers that never advance under vi.useFakeTimers() — the whole file would hang.
  // The debounce is short (~300ms), so waitFor's default polling naturally catches up.
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not fire search for a single-char query (below minLength)", async () => {
    const spy = vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "h" } });

    // Wait past the debounce window with real time, then assert no call was made.
    await new Promise((r) => setTimeout(r, 500));
    expect(spy).not.toHaveBeenCalled();
  });

  it("fires search after debounce when query >= 2 chars", async () => {
    const spy = vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "ho" } });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(spy).toHaveBeenCalledWith("ho", expect.objectContaining({ vault_id: "vault-test" }));
  });

  it("renders result rows after successful search", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "homelab" } });

    await waitFor(() => screen.getByTestId("search-results"), { timeout: 2000 });

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
    fireEvent.change(input, { target: { value: "xyzabc" } });

    await waitFor(() => screen.getByTestId("search-no-results"), { timeout: 2000 });
  });

  it("renders ErrorState when search rejects", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockRejectedValue(new Error("Service unavailable"));

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "query" } });

    // ErrorState renders with data-testid="error-state" (audit #6 — replaces inline div)
    await waitFor(() => screen.getByTestId("error-state"), { timeout: 2000 });
  });

  it("ErrorState shows search.error title on failure", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockRejectedValue(new Error("timeout"));

    render(<SearchView />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "query" } });

    await waitFor(() => screen.getByTestId("error-state-title"), { timeout: 2000 });
    expect(screen.getByTestId("error-state-title").textContent).toBe("Search error");
  });

  it("clicking a result calls selectPage and navigates to 'pages' section", async () => {
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "homelab" } });

    await waitFor(() => screen.getAllByTestId("search-result-row"), { timeout: 2000 });

    const firstRow = screen.getAllByTestId("search-result-row")[0]!;
    fireEvent.click(firstRow);

    expect(mockSelectPage).toHaveBeenCalledWith("page-uuid-1", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});

// ─── D. SearchView — loading skeleton + slow-load (audit #6) ─────────────────

describe("SearchView — skeleton and slow-load (fake timers)", () => {
  // These tests use vi.useFakeTimers() to control DEBOUNCE_MS + SLOW_LOAD_MS.
  // They do NOT use waitFor (which needs real timers) — all assertions are
  // synchronous after act()/advanceTimersByTime().

  beforeEach(() => {
    vi.useFakeTimers();
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows skeleton rows (search-loading) while a request is in-flight", () => {
    // Never-resolving mock — keeps loading=true indefinitely
    vi.spyOn(searchClientModule, "searchWiki").mockImplementation(
      () => new Promise<SearchResponse>(() => {}),
    );

    render(<SearchView />);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "DORA" } });

    // Advance past debounce (300ms)
    act(() => {
      vi.advanceTimersByTime(301);
    });

    const loadingEl = screen.getByTestId("search-loading");
    expect(loadingEl).toBeTruthy();
    // Should have role="status" for screen-reader accessibility
    expect(loadingEl.getAttribute("role")).toBe("status");
    // Skeleton rows are aria-hidden — verify at least one exists
    const skeletonDivs = loadingEl.querySelectorAll('[aria-hidden="true"]');
    expect(skeletonDivs.length).toBeGreaterThan(0);
  });

  it("shows slow-load message (search-slow-load) after 4 s still loading", () => {
    vi.spyOn(searchClientModule, "searchWiki").mockImplementation(
      () => new Promise<SearchResponse>(() => {}),
    );

    render(<SearchView />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "DORA" } });

    // Advance debounce then slow-load threshold
    act(() => {
      vi.advanceTimersByTime(301);
    });
    act(() => {
      vi.advanceTimersByTime(4001);
    });

    expect(screen.getByTestId("search-slow-load")).toBeTruthy();
    // Cancel + Retry buttons present
    expect(screen.getByTestId("search-slow-cancel")).toBeTruthy();
    expect(screen.getByTestId("search-slow-retry")).toBeTruthy();
  });

  it("Cancel in slow-load message resets the view to empty state", () => {
    vi.spyOn(searchClientModule, "searchWiki").mockImplementation(
      () => new Promise<SearchResponse>(() => {}),
    );

    render(<SearchView />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "DORA" } });
    act(() => {
      vi.advanceTimersByTime(301);
    });
    act(() => {
      vi.advanceTimersByTime(4001);
    });

    // Slow-load is visible; click Cancel
    fireEvent.click(screen.getByTestId("search-slow-cancel"));

    // Loading panel gone, empty state shown (no query after clear)
    expect(screen.queryByTestId("search-loading")).toBeNull();
    expect(screen.getByTestId("search-empty-state")).toBeTruthy();
  });

  it("slow-load message not shown if request settles before 4 s", () => {
    // Resolves immediately (within debounce window after advance)
    vi.spyOn(searchClientModule, "searchWiki").mockResolvedValue(MOCK_RESPONSE);

    render(<SearchView />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "fast" } });
    // Advance debounce — the mock resolves synchronously within this act
    act(() => {
      vi.advanceTimersByTime(301);
    });

    // Do NOT advance past SLOW_LOAD_MS
    expect(screen.queryByTestId("search-slow-load")).toBeNull();
  });
});
