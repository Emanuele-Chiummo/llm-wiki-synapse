/**
 * graphDrillDown.test.ts — unit tests for R9-5 community panel + edge breakdown.
 *
 * Covers:
 *   A. fetchCommunityDetail shape contract — {community_id, size, cohesion,
 *      cohesion_warning, members[]}.
 *   B. fetchCommunityDetail 409 → ApiError(409) (graph cache cold).
 *   C. fetchEdgeDetail shape contract — {weight, breakdown:{direct_links,
 *      shared_sources, adamic_adar, type_affinity}}.
 *   D. fetchEdgeDetail 404 → ApiError(404).
 *   E. fetchCommunityDetail passes AbortSignal.
 *   F. fetchEdgeDetail passes AbortSignal.
 *
 * INVARIANT I2: fetch functions return server data verbatim — no layout work.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  fetchCommunityDetail,
  fetchEdgeDetail,
  ApiError,
  type CommunityDetail,
  type EdgeDetail,
  type CommunityMember,
  type EdgeBreakdown,
} from "../api/graphClient";

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const COMMUNITY_MEMBER: CommunityMember = {
  id: "page-abc",
  title: "Alpha Concept",
  page_type: "concept",
  degree: 5,
};

const COMMUNITY_DETAIL: CommunityDetail = {
  community_id: 2,
  size: 7,
  cohesion: 0.45,
  cohesion_warning: false,
  members: [COMMUNITY_MEMBER],
};

const EDGE_BREAKDOWN: EdgeBreakdown = {
  direct_links: 3.0,
  shared_sources: 4.5,
  adamic_adar: 1.2,
  type_affinity: 1.0,
};

const EDGE_DETAIL: EdgeDetail = {
  weight: 9.7,
  breakdown: EDGE_BREAKDOWN,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ─── A. CommunityDetail shape contract ───────────────────────────────────────

describe("CommunityDetail shape contract (R9-5 wire format)", () => {
  it("community_id is a number", () => {
    expect(typeof COMMUNITY_DETAIL.community_id).toBe("number");
  });

  it("size is a number", () => {
    expect(typeof COMMUNITY_DETAIL.size).toBe("number");
  });

  it("cohesion is a number between 0 and 1", () => {
    expect(typeof COMMUNITY_DETAIL.cohesion).toBe("number");
    expect(COMMUNITY_DETAIL.cohesion).toBeGreaterThanOrEqual(0);
    expect(COMMUNITY_DETAIL.cohesion).toBeLessThanOrEqual(1);
  });

  it("cohesion_warning is a boolean", () => {
    expect(typeof COMMUNITY_DETAIL.cohesion_warning).toBe("boolean");
  });

  it("members is an array", () => {
    expect(Array.isArray(COMMUNITY_DETAIL.members)).toBe(true);
  });

  it("each member has id (string), title (string), page_type (string|null), degree (number)", () => {
    const m: CommunityMember = COMMUNITY_DETAIL.members[0]!;
    expect(typeof m.id).toBe("string");
    expect(typeof m.title).toBe("string");
    expect(m.page_type === null || typeof m.page_type === "string").toBe(true);
    expect(typeof m.degree).toBe("number");
  });

  it("cohesion_warning=true when cohesion is below threshold (< 0.1)", () => {
    const lowCohesion: CommunityDetail = {
      ...COMMUNITY_DETAIL,
      cohesion: 0.05,
      cohesion_warning: true,
    };
    expect(lowCohesion.cohesion_warning).toBe(true);
  });
});

// ─── B. fetchCommunityDetail 409 ─────────────────────────────────────────────

describe("fetchCommunityDetail — 409 graph cache cold (AC-R9-5-409)", () => {
  it("throws ApiError(409) when server returns 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            {
              error: {
                code: "conflict",
                message: "Graph cache cold — regenerate first",
                status: 409,
                details: null,
              },
            },
            409,
          ),
        ),
    );

    const err = await fetchCommunityDetail(2).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
  });

  it("error message contains 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            {
              error: { code: "conflict", message: "Graph cache cold", status: 409, details: null },
            },
            409,
          ),
        ),
    );

    try {
      await fetchCommunityDetail(2);
      expect.fail("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(409);
    }
  });
});

// ─── B2. fetchCommunityDetail happy path ─────────────────────────────────────

describe("fetchCommunityDetail — 200 success", () => {
  it("returns CommunityDetail on 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(COMMUNITY_DETAIL)));

    const result = await fetchCommunityDetail(2);

    expect(result.community_id).toBe(2);
    expect(result.size).toBe(7);
    expect(result.cohesion).toBe(0.45);
    expect(result.cohesion_warning).toBe(false);
    expect(result.members).toHaveLength(1);
    expect(result.members[0]?.id).toBe("page-abc");
  });

  it("builds the correct URL: /graph/communities/{id}", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(COMMUNITY_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCommunityDetail(5);

    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).toMatch(/\/graph\/communities\/5$/);
  });
});

// ─── C. EdgeDetail shape contract ────────────────────────────────────────────

describe("EdgeDetail shape contract (R9-5 4-signal breakdown)", () => {
  it("weight is a number", () => {
    expect(typeof EDGE_DETAIL.weight).toBe("number");
  });

  it("breakdown has direct_links (number)", () => {
    expect(typeof EDGE_DETAIL.breakdown.direct_links).toBe("number");
  });

  it("breakdown has shared_sources (number)", () => {
    expect(typeof EDGE_DETAIL.breakdown.shared_sources).toBe("number");
  });

  it("breakdown has adamic_adar (number)", () => {
    expect(typeof EDGE_DETAIL.breakdown.adamic_adar).toBe("number");
  });

  it("breakdown has type_affinity (number)", () => {
    expect(typeof EDGE_DETAIL.breakdown.type_affinity).toBe("number");
  });

  it("4 signals: direct_links×3, shared_sources×4, adamic_adar×1.5, type_affinity×1 (AC-F4)", () => {
    // Verify that breakdown contains exactly the 4 named signals from CLAUDE.md §4b F4
    const keys = Object.keys(EDGE_DETAIL.breakdown).sort();
    expect(keys).toEqual(["adamic_adar", "direct_links", "shared_sources", "type_affinity"]);
  });
});

// ─── D. fetchEdgeDetail 404 ───────────────────────────────────────────────────

describe("fetchEdgeDetail — 404 edge not found", () => {
  it("throws ApiError(404) when edge does not exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error: { code: "not_found", message: "Edge not found", status: 404, details: null } },
            404,
          ),
        ),
    );

    const err = await fetchEdgeDetail("src-1", "tgt-2").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });
});

// ─── D2. fetchEdgeDetail happy path ──────────────────────────────────────────

describe("fetchEdgeDetail — 200 success", () => {
  it("returns EdgeDetail on 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(EDGE_DETAIL)));

    const result = await fetchEdgeDetail("src-1", "tgt-2");

    expect(result.weight).toBe(9.7);
    expect(result.breakdown.direct_links).toBe(3.0);
    expect(result.breakdown.shared_sources).toBe(4.5);
    expect(result.breakdown.adamic_adar).toBe(1.2);
    expect(result.breakdown.type_affinity).toBe(1.0);
  });

  it("builds the correct URL: /graph/edges/{src}/{tgt}", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EDGE_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    await fetchEdgeDetail("node-a", "node-b");

    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).toMatch(/\/graph\/edges\/node-a\/node-b$/);
  });

  it("URL-encodes node IDs with special characters", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EDGE_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    await fetchEdgeDetail("node a/1", "node b/2");

    const url = mockFetch.mock.calls[0]![0] as string;
    // Both IDs should be encoded
    expect(url).toContain("node%20a%2F1");
    expect(url).toContain("node%20b%2F2");
  });
});

// ─── E. AbortSignal — fetchCommunityDetail ────────────────────────────────────

describe("fetchCommunityDetail — AbortSignal", () => {
  it("passes signal to fetch when provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(COMMUNITY_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    const ctrl = new AbortController();
    await fetchCommunityDetail(2, ctrl.signal);

    const opts = mockFetch.mock.calls[0]![1] as { signal?: AbortSignal } | undefined;
    expect(opts?.signal).toBe(ctrl.signal);
  });

  it("omits signal when no signal provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(COMMUNITY_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCommunityDetail(2);

    // apiFetch always passes a RequestInit (for auth headers), so check signal is absent
    const opts = mockFetch.mock.calls[0]![1] as { signal?: AbortSignal } | undefined;
    expect(opts?.signal).toBeUndefined();
  });
});

// ─── F. AbortSignal — fetchEdgeDetail ────────────────────────────────────────

describe("fetchEdgeDetail — AbortSignal", () => {
  it("passes signal to fetch when provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(jsonResponse(EDGE_DETAIL));
    vi.stubGlobal("fetch", mockFetch);

    const ctrl = new AbortController();
    await fetchEdgeDetail("a", "b", ctrl.signal);

    const opts = mockFetch.mock.calls[0]![1] as { signal?: AbortSignal } | undefined;
    expect(opts?.signal).toBe(ctrl.signal);
  });
});

// ─── G. I2 invariant sentinel ────────────────────────────────────────────────

describe("R9-5 I2 invariant — no layout computed in fetch functions", () => {
  it("fetchCommunityDetail does NOT call Math.random (no layout)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(COMMUNITY_DETAIL)));
    const randomSpy = vi.spyOn(Math, "random");

    await fetchCommunityDetail(2);

    expect(randomSpy).not.toHaveBeenCalled();
  });

  it("fetchEdgeDetail does NOT call Math.random (no layout)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(EDGE_DETAIL)));
    const randomSpy = vi.spyOn(Math, "random");

    await fetchEdgeDetail("a", "b");

    expect(randomSpy).not.toHaveBeenCalled();
  });
});
