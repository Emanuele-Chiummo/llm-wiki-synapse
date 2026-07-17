/**
 * scenariosClient.test.ts — wire-shape contract tests (QA gate D2 regression).
 *
 * GET /scenarios returns an ENVELOPE {items: [...]} — the client must unwrap it.
 * These tests stub fetch with the REAL wire shape (unlike component tests which
 * mock the client), so an envelope regression fails here.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchScenarios, applyScenario } from "../api/scenariosClient";

const ITEMS = [
  { id: "research", name: "Research", description: "d1" },
  { id: "reading", name: "Reading", description: "d2" },
];

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchScenarios — envelope unwrap", () => {
  it("unwraps {items: [...]} into the array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: ITEMS }), { status: 200 })),
    );
    const result = await fetchScenarios();
    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(2);
    expect(result[0]?.id).toBe("research");
  });

  it("returns [] when items is missing or not an array", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    expect(await fetchScenarios()).toEqual([]);
  });
});

describe("applyScenario", () => {
  it("POSTs to /scenarios/{id}/apply and returns the response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ applied: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await applyScenario("research");
    expect(fetchMock).toHaveBeenCalledWith(
      "/scenarios/research/apply",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.applied).toBe(true);
  });
});
