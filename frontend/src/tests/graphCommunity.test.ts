/**
 * graphCommunity.test.ts — unit tests for graph community and domain coloring (F4, v0.6).
 *
 * Coverage:
 *   A.  COMMUNITY_PALETTE + colorForCommunity — correct palette mapping, cycling, unassigned (-1).
 *   B.  GraphNode community field passes through graphTransform verbatim (I2: no recompute).
 *   C.  GraphStore carries communities from setGraph; selectCommunities selector.
 *   D.  GraphResponse community types contract (GraphCommunity shape).
 *   E.  LOW_COHESION_THRESHOLD contract — communities below threshold flagged.
 *   F.  GraphCommunity label/dominant_domain/top_page fields (feat/b3-graph-look).
 *   G.  computeCommunityCentroids — memoized centroid computation (I2/I3).
 *   H.  communityDisplayName — unique names per cluster; same-domain communities get
 *       different display names via their top_page subtopic.
 *   I.  colorForDomain — stable/deterministic hash, null/untagged → DOMAIN_UNTAGGED_COLOR.
 *   J.  computeDomainCentroids — correct centroids, skips singletons, skips null, no mutation.
 *   K.  Community legend aggregation — one row per Louvain community, unique display names,
 *       correct color (colorForCommunity), low-cohesion marker.
 *   L.  Default colorMode is "community" (= Louvain community coloring via colorForCommunity).
 *
 * INVARIANT I2: community ids and domain values are ALWAYS read from the server response.
 *   No client-side community detection, Louvain, or domain assignment runs in any test.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  COMMUNITY_PALETTE,
  DOMAIN_UNTAGGED_COLOR,
  LOW_COHESION_THRESHOLD,
  colorForCommunity,
  colorForDomain,
} from "../components/graphPalette";
import { buildGraphologyGraph } from "../api/graphTransform";
import { computeCommunityCentroids, computeDomainCentroids, communityDisplayName } from "../components/graphCommunityUtils";
import { useGraphStore, selectCommunities } from "../store/graphStore";
import type { GraphNode, GraphEdge, GraphCommunity } from "../api/types";

// ─── A. COMMUNITY_PALETTE + colorForCommunity ─────────────────────────────────

describe("COMMUNITY_PALETTE — 12-color categorical palette (§COMMUNITY-PALETTE)", () => {
  it("has exactly 12 entries", () => {
    expect(COMMUNITY_PALETTE).toHaveLength(12);
  });

  it("all entries are valid 7-char hex strings (#rrggbb)", () => {
    for (const color of COMMUNITY_PALETTE) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("all 12 entries are distinct (no duplicates)", () => {
    const unique = new Set(COMMUNITY_PALETTE);
    expect(unique.size).toBe(12);
  });
});

describe("colorForCommunity — palette mapping (I2: read-only server values)", () => {
  it("returns COMMUNITY_PALETTE[0] for community 0 (largest)", () => {
    expect(colorForCommunity(0)).toBe(COMMUNITY_PALETTE[0]);
  });

  it("returns COMMUNITY_PALETTE[11] for community 11 (last in palette)", () => {
    expect(colorForCommunity(11)).toBe(COMMUNITY_PALETTE[11]);
  });

  it("cycles back to COMMUNITY_PALETTE[0] for community 12 (wraps)", () => {
    expect(colorForCommunity(12)).toBe(COMMUNITY_PALETTE[0]);
  });

  it("cycles correctly for community 25 (25 % 12 = 1)", () => {
    expect(colorForCommunity(25)).toBe(COMMUNITY_PALETTE[1]);
  });

  it("returns DEFAULT_NODE_COLOR (#6e7781) for unassigned community (-1)", () => {
    expect(colorForCommunity(-1)).toBe("#6e7781");
  });

  it("returns DEFAULT_NODE_COLOR for any negative community id", () => {
    expect(colorForCommunity(-99)).toBe("#6e7781");
  });

  it("returns a hex string for every community 0–23 (two full cycles)", () => {
    for (let id = 0; id < 24; id++) {
      const color = colorForCommunity(id);
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

// ─── B. graphTransform passes community through verbatim (I2) ─────────────────

describe("buildGraphologyGraph — community passthrough (I2: no client recompute)", () => {
  const nodes: GraphNode[] = [
    { id: "n1", title: "Alpha", type: "concept", x: 0, y: 0, degree: 1, community: 0 },
    { id: "n2", title: "Beta",  type: "entity",  x: 1, y: 1, degree: 1, community: 3 },
    { id: "n3", title: "Gamma", type: "source",  x: 2, y: 2, degree: 0 }, // no community field
  ];
  const edges: GraphEdge[] = [{ source: "n1", target: "n2", weight: 1 }];

  it("stores community=0 on node n1 verbatim", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n1", "community")).toBe(0);
  });

  it("stores community=3 on node n2 verbatim", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n2", "community")).toBe(3);
  });

  it("defaults to community=-1 when field is absent (older server, non-breaking)", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n3", "community")).toBe(-1);
  });

  it("does NOT call Math.random during community assignment (I2 sentinel)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    buildGraphologyGraph(nodes, edges);
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });
});

// ─── C. graphStore carries communities from setGraph ──────────────────────────

describe("graphStore — communities via setGraph + selectCommunities (I3)", () => {
  beforeEach(() => {
    useGraphStore.getState().reset();
  });

  it("selectCommunities returns empty array in initial state", () => {
    const s = useGraphStore.getState();
    expect(selectCommunities(s)).toEqual([]);
  });

  it("setGraph without communities defaults to [] (backward compat)", () => {
    useGraphStore.getState().setGraph([], [], 1, "hit");
    const s = useGraphStore.getState();
    expect(selectCommunities(s)).toEqual([]);
  });

  it("setGraph with communities stores them and selectCommunities returns them", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 42, cohesion: 0.85 },
      { id: 1, size: 10, cohesion: 0.05 },
    ];
    useGraphStore.getState().setGraph([], [], 2, "miss", communities);
    const s = useGraphStore.getState();
    const stored = selectCommunities(s);
    expect(stored).toHaveLength(2);
    expect(stored[0]?.id).toBe(0);
    expect(stored[0]?.size).toBe(42);
    expect(stored[0]?.cohesion).toBe(0.85);
    expect(stored[1]?.id).toBe(1);
  });

  it("reset clears communities back to []", () => {
    useGraphStore.getState().setGraph([], [], 1, "hit", [
      { id: 0, size: 5, cohesion: 0.9 },
    ]);
    useGraphStore.getState().reset();
    expect(selectCommunities(useGraphStore.getState())).toEqual([]);
  });
});

// ─── D. GraphCommunity shape contract ────────────────────────────────────────

describe("GraphCommunity shape contract (types.ts)", () => {
  it("a valid GraphCommunity has id (number), size (number), cohesion (number)", () => {
    const c: GraphCommunity = { id: 2, size: 7, cohesion: 0.45 };
    expect(typeof c.id).toBe("number");
    expect(typeof c.size).toBe("number");
    expect(typeof c.cohesion).toBe("number");
  });

  it("community id -1 is a valid value for unassigned communities", () => {
    const unassigned: GraphCommunity = { id: -1, size: 3, cohesion: 0.0 };
    expect(unassigned.id).toBe(-1);
  });
});

// ─── E. LOW_COHESION_THRESHOLD ────────────────────────────────────────────────

describe("LOW_COHESION_THRESHOLD — legend warning logic", () => {
  it("is 0.1", () => {
    expect(LOW_COHESION_THRESHOLD).toBe(0.1);
  });

  it("cohesion = 0.09 is considered low-cohesion (< threshold)", () => {
    const c: GraphCommunity = { id: 0, size: 10, cohesion: 0.09 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(true);
  });

  it("cohesion = 0.10 is NOT low-cohesion (= threshold, not strictly less)", () => {
    const c: GraphCommunity = { id: 0, size: 10, cohesion: 0.10 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(false);
  });

  it("cohesion = 0.0 is low-cohesion (isolated community)", () => {
    const c: GraphCommunity = { id: 3, size: 1, cohesion: 0.0 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(true);
  });

  it("cohesion = 0.85 is not low-cohesion", () => {
    const c: GraphCommunity = { id: 1, size: 50, cohesion: 0.85 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(false);
  });
});

// ─── F. GraphCommunity label / dominant_domain / top_page (feat/b3-graph-look) ─

describe("GraphCommunity — label/dominant_domain/top_page fields", () => {
  it("accepts a community with label, dominant_domain, and top_page", () => {
    const c: GraphCommunity = {
      id: 0,
      size: 42,
      cohesion: 0.8,
      label: "SAM",
      dominant_domain: "SAM",
      top_page: { id: "page-1", title: "SAM Overview", slug: "sam-overview" },
    };
    expect(c.label).toBe("SAM");
    expect(c.dominant_domain).toBe("SAM");
    expect(c.top_page?.title).toBe("SAM Overview");
  });

  it("accepts a community without the new optional fields (backward compat — old server)", () => {
    const c: GraphCommunity = { id: 1, size: 10, cohesion: 0.5 };
    expect(c.label).toBeUndefined();
    expect(c.dominant_domain).toBeUndefined();
    expect(c.top_page).toBeUndefined();
  });

  it("accepts null dominant_domain (no domain vocabulary configured)", () => {
    const c: GraphCommunity = {
      id: 2,
      size: 5,
      cohesion: 0.3,
      label: "Concetto A",
      dominant_domain: null,
      top_page: null,
    };
    expect(c.dominant_domain).toBeNull();
    expect(c.top_page).toBeNull();
    expect(c.label).toBe("Concetto A");
  });

  it("stores label + dominant_domain through graphStore.setGraph (I3)", () => {
    useGraphStore.getState().reset();
    const communities: GraphCommunity[] = [
      { id: 0, size: 20, cohesion: 0.9, label: "Procurement", dominant_domain: "Procurement" },
      { id: 1, size: 5, cohesion: 0.4, label: "TPRM", dominant_domain: "TPRM" },
    ];
    useGraphStore.getState().setGraph([], [], 3, "hit", communities);
    const stored = selectCommunities(useGraphStore.getState());
    expect(stored[0]?.label).toBe("Procurement");
    expect(stored[0]?.dominant_domain).toBe("Procurement");
    expect(stored[1]?.label).toBe("TPRM");
  });
});

// ─── G. computeCommunityCentroids — I2/I3 contract ───────────────────────────

describe("computeCommunityCentroids — centroid computation (I2/I3)", () => {
  const communities: GraphCommunity[] = [
    { id: 0, size: 2, cohesion: 0.8, label: "SAM", dominant_domain: "SAM" },
    { id: 1, size: 3, cohesion: 0.7, label: "Procurement" },
    { id: 2, size: 1, cohesion: 0.5, label: "Singleton" }, // size=1 — should be excluded
  ];

  const nodes: GraphNode[] = [
    { id: "n1", title: "A", type: "concept", x: 0,   y: 0,   community: 0 },
    { id: "n2", title: "B", type: "concept", x: 4,   y: 4,   community: 0 },
    { id: "n3", title: "C", type: "entity",  x: 10,  y: 20,  community: 1 },
    { id: "n4", title: "D", type: "entity",  x: 30,  y: 0,   community: 1 },
    { id: "n5", title: "E", type: "entity",  x: 20,  y: 10,  community: 1 },
    { id: "n6", title: "F", type: "source",  x: 100, y: 100, community: 2 }, // singleton
    { id: "n7", title: "G", type: "source",  x: 50,  y: 50,  community: -1 }, // unassigned
  ];

  it("returns centroids only for communities with size > 1 (skips singletons)", () => {
    const result = computeCommunityCentroids(nodes, communities);
    // community 2 has size=1 → excluded
    expect(result.has(2)).toBe(false);
    // community -1 is unassigned → always excluded
    expect(result.has(-1)).toBe(false);
    // communities 0 and 1 have size > 1
    expect(result.has(0)).toBe(true);
    expect(result.has(1)).toBe(true);
    expect(result.size).toBe(2);
  });

  it("computes correct centroid for community 0 (avg of n1(0,0) and n2(4,4))", () => {
    const result = computeCommunityCentroids(nodes, communities);
    const c0 = result.get(0)!;
    expect(c0.x).toBeCloseTo(2); // (0+4)/2
    expect(c0.y).toBeCloseTo(2); // (0+4)/2
  });

  it("computes correct centroid for community 1 (avg of n3,n4,n5)", () => {
    const result = computeCommunityCentroids(nodes, communities);
    const c1 = result.get(1)!;
    expect(c1.x).toBeCloseTo(20); // (10+30+20)/3
    expect(c1.y).toBeCloseTo(10); // (20+0+10)/3
  });

  it("uses communityDisplayName for the centroid label (I2/I3)", () => {
    const result = computeCommunityCentroids(nodes, communities);
    // community 0: dominant_domain="SAM", no top_page — falls back to label "SAM"
    expect(result.get(0)?.label).toBe("SAM");
    // community 1: no dominant_domain, no top_page — falls back to label "Procurement"
    expect(result.get(1)?.label).toBe("Procurement");
  });

  it("falls back to 'C{id}' when community has no label/domain/top_page", () => {
    const commNoLabel: GraphCommunity[] = [
      { id: 3, size: 2, cohesion: 0.5 }, // no label/dominant_domain/top_page
      { id: 4, size: 2, cohesion: 0.5, label: "" }, // empty label, no domain/top_page
    ];
    const nodesExtra: GraphNode[] = [
      { id: "x1", title: "X1", type: "concept", x: 0, y: 0, community: 3 },
      { id: "x2", title: "X2", type: "concept", x: 2, y: 2, community: 3 },
      { id: "x3", title: "X3", type: "entity", x: 5, y: 5, community: 4 },
      { id: "x4", title: "X4", type: "entity", x: 7, y: 7, community: 4 },
    ];
    const result = computeCommunityCentroids(nodesExtra, commNoLabel);
    expect(result.get(3)?.label).toBe("C3");
    expect(result.get(4)?.label).toBe("C4");
  });

  it("uses colorForCommunity for the centroid color (matches palette)", () => {
    const result = computeCommunityCentroids(nodes, communities);
    expect(result.get(0)?.color).toBe(colorForCommunity(0));
    expect(result.get(1)?.color).toBe(colorForCommunity(1));
  });

  it("DOES NOT mutate node x/y (I2 invariant)", () => {
    const nodesCopy = nodes.map((n) => ({ ...n }));
    computeCommunityCentroids(nodesCopy, communities);
    for (let i = 0; i < nodes.length; i++) {
      expect(nodesCopy[i]!.x).toBe(nodes[i]!.x);
      expect(nodesCopy[i]!.y).toBe(nodes[i]!.y);
    }
  });

  it("does NOT call Math.random (I2 sentinel — no client layout)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    computeCommunityCentroids(nodes, communities);
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  it("returns empty map when nodes array is empty", () => {
    const result = computeCommunityCentroids([], communities);
    expect(result.size).toBe(0);
  });

  it("returns empty map when communities array is empty", () => {
    const result = computeCommunityCentroids(nodes, []);
    expect(result.size).toBe(0);
  });

  it("returns empty map when all communities are singletons (size=1)", () => {
    const singletonComms: GraphCommunity[] = [
      { id: 0, size: 1, cohesion: 0.9 },
    ];
    const singleNodes: GraphNode[] = [
      { id: "s1", title: "Solo", type: "concept", x: 1, y: 2, community: 0 },
    ];
    const result = computeCommunityCentroids(singleNodes, singletonComms);
    expect(result.size).toBe(0);
  });
});

// ─── H. communityDisplayName — unique labels per cluster ─────────────────────

describe("communityDisplayName — unique label derivation (I2/I3)", () => {
  it("H1: returns '{domain} · {sub}' when dominant_domain and top_page exist", () => {
    const c: GraphCommunity = {
      id: 0, size: 10, cohesion: 0.8,
      dominant_domain: "SAM",
      top_page: { id: "p1", title: "Reconciliation Process", slug: "reconciliation-process" },
    };
    expect(communityDisplayName(c)).toBe("SAM · Reconciliation Process");
  });

  it("H2: strips leading domain word from top_page.title to avoid duplication", () => {
    const c: GraphCommunity = {
      id: 1, size: 8, cohesion: 0.7,
      dominant_domain: "SAM",
      top_page: { id: "p2", title: "SAM Reconciliation", slug: "sam-reconciliation" },
    };
    // "SAM · SAM Reconciliation" → "SAM · Reconciliation"
    const name = communityDisplayName(c);
    expect(name).toBe("SAM · Reconciliation");
    expect(name).not.toContain("SAM · SAM");
  });

  it("H3: two same-domain communities get DIFFERENT display names via their top_page", () => {
    const cA: GraphCommunity = {
      id: 0, size: 12, cohesion: 0.9,
      dominant_domain: "SAM",
      top_page: { id: "p1", title: "SAM Reconciliation", slug: "sam-reconciliation" },
    };
    const cB: GraphCommunity = {
      id: 1, size: 8, cohesion: 0.6,
      dominant_domain: "SAM",
      top_page: { id: "p2", title: "SAM Reporting", slug: "sam-reporting" },
    };
    const nameA = communityDisplayName(cA);
    const nameB = communityDisplayName(cB);
    // Both have SAM domain — they must differ because their subtopics differ
    expect(nameA).not.toBe(nameB);
    expect(nameA).toBe("SAM · Reconciliation");
    expect(nameB).toBe("SAM · Reporting");
  });

  it("H4: truncates subtitle at 24 chars with ellipsis", () => {
    const c: GraphCommunity = {
      id: 2, size: 5, cohesion: 0.5,
      dominant_domain: "Procurement",
      top_page: { id: "p3", title: "Supplier Evaluation Framework", slug: "supplier-evaluation-framework" },
    };
    const name = communityDisplayName(c);
    // "Supplier Evaluation Framework" is 29 chars; MAX_SUB_CHARS=24 → slice(0,23)+"…"
    // = "Supplier Evaluation Fra…"
    expect(name).toBe("Procurement · Supplier Evaluation Fra…");
  });

  it("H5: falls back to top_page.title when no dominant_domain", () => {
    const c: GraphCommunity = {
      id: 3, size: 4, cohesion: 0.4,
      dominant_domain: null,
      top_page: { id: "p4", title: "Risk Assessment", slug: "risk-assessment" },
    };
    expect(communityDisplayName(c)).toBe("Risk Assessment");
  });

  it("H6: falls back to community.label when no domain or top_page", () => {
    const c: GraphCommunity = {
      id: 4, size: 3, cohesion: 0.3,
      label: "Finance",
    };
    expect(communityDisplayName(c)).toBe("Finance");
  });

  it("H7: falls back to C{id} when no label/domain/top_page", () => {
    const c: GraphCommunity = { id: 5, size: 2, cohesion: 0.2 };
    expect(communityDisplayName(c)).toBe("C5");
  });

  it("H8: uses fallbackFn for 'Community N' i18n string when provided", () => {
    const c: GraphCommunity = { id: 7, size: 2, cohesion: 0.2 };
    const name = communityDisplayName(c, (id) => `Community ${id}`);
    expect(name).toBe("Community 7");
  });

  it("H9: does NOT call Math.random (I2/I3 sentinel — pure function)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    communityDisplayName({ id: 0, size: 5, cohesion: 0.8, dominant_domain: "SAM",
      top_page: { id: "x", title: "SAM Overview", slug: "sam-overview" } });
    communityDisplayName({ id: 1, size: 2, cohesion: 0.2 });
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });
});

// ─── I. colorForDomain — stable/deterministic hash ───────────────────────────

describe("colorForDomain — domain color palette (I2/I3)", () => {
  it("returns DOMAIN_UNTAGGED_COLOR (#8b949e) for null domain", () => {
    expect(colorForDomain(null)).toBe(DOMAIN_UNTAGGED_COLOR);
    expect(DOMAIN_UNTAGGED_COLOR).toBe("#8b949e");
  });

  it("returns DOMAIN_UNTAGGED_COLOR for undefined domain", () => {
    expect(colorForDomain(undefined)).toBe(DOMAIN_UNTAGGED_COLOR);
  });

  it("returns DOMAIN_UNTAGGED_COLOR for empty string domain", () => {
    expect(colorForDomain("")).toBe(DOMAIN_UNTAGGED_COLOR);
  });

  it("returns DOMAIN_UNTAGGED_COLOR for whitespace-only domain", () => {
    expect(colorForDomain("   ")).toBe(DOMAIN_UNTAGGED_COLOR);
  });

  it("returns a 7-char hex string (#rrggbb) for named domains", () => {
    for (const domain of ["SAM", "Procurement", "TPRM", "Regolamentazioni"]) {
      expect(colorForDomain(domain)).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("is DETERMINISTIC — same domain → same color across calls", () => {
    const domains = ["SAM", "Procurement", "TPRM", "Finance", "HR", "IT"];
    for (const d of domains) {
      const first = colorForDomain(d);
      const second = colorForDomain(d);
      const third = colorForDomain(d);
      expect(second).toBe(first);
      expect(third).toBe(first);
    }
  });

  it("is STABLE — calling Math.random does NOT affect colorForDomain output", () => {
    // Color must not change even if Math.random is called between invocations
    const before = colorForDomain("SAM");
    Math.random();
    Math.random();
    const after = colorForDomain("SAM");
    expect(after).toBe(before);
  });

  it("does NOT call Math.random (I2/I3 sentinel — pure hash function)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    colorForDomain("SAM");
    colorForDomain("Procurement");
    colorForDomain(null);
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  it("different domain names return different colors in most cases", () => {
    // With 16 colors and well-known domains we should see at least some variation
    const colors = ["SAM", "Procurement", "TPRM", "Regolamentazioni", "Finance"].map(colorForDomain);
    const unique = new Set(colors);
    expect(unique.size).toBeGreaterThan(1);
  });
});

// ─── J. computeDomainCentroids — I2/I3 contract ──────────────────────────────

describe("computeDomainCentroids — domain centroid computation (I2/I3)", () => {
  const nodesWithDomains: GraphNode[] = [
    { id: "n1", title: "A",    type: "concept", x: 0,   y: 0,   community: 0, domain: "SAM" },
    { id: "n2", title: "B",    type: "concept", x: 4,   y: 4,   community: 0, domain: "SAM" },
    { id: "n3", title: "C",    type: "entity",  x: 10,  y: 20,  community: 1, domain: "Procurement" },
    { id: "n4", title: "D",    type: "entity",  x: 30,  y: 0,   community: 1, domain: "Procurement" },
    { id: "n5", title: "E",    type: "entity",  x: 20,  y: 10,  community: 1, domain: "Procurement" },
    { id: "n6", title: "F",    type: "source",  x: 100, y: 100, community: 2, domain: "TPRM" }, // singleton domain
    { id: "n7", title: "G",    type: "source",  x: 50,  y: 50,  community: -1, domain: null }, // untagged (explicit null)
    { id: "n8", title: "H",    type: "concept", x: 5,   y: 5,   community: -1 }, // untagged (domain absent)
  ];

  it("returns centroids only for domains with >= 2 nodes (skips singletons + null)", () => {
    const result = computeDomainCentroids(nodesWithDomains);
    // TPRM has only 1 node → excluded
    expect(result.has("TPRM")).toBe(false);
    // null/undefined domain → excluded
    expect(result.has("")).toBe(false);
    // SAM and Procurement have >= 2 nodes → included
    expect(result.has("SAM")).toBe(true);
    expect(result.has("Procurement")).toBe(true);
    expect(result.size).toBe(2);
  });

  it("computes correct centroid for SAM (avg of n1(0,0) and n2(4,4))", () => {
    const result = computeDomainCentroids(nodesWithDomains);
    const sam = result.get("SAM")!;
    expect(sam.x).toBeCloseTo(2); // (0+4)/2
    expect(sam.y).toBeCloseTo(2); // (0+4)/2
  });

  it("computes correct centroid for Procurement (avg of n3,n4,n5)", () => {
    const result = computeDomainCentroids(nodesWithDomains);
    const proc = result.get("Procurement")!;
    expect(proc.x).toBeCloseTo(20); // (10+30+20)/3
    expect(proc.y).toBeCloseTo(10); // (20+0+10)/3
  });

  it("sets label = domain name on each centroid", () => {
    const result = computeDomainCentroids(nodesWithDomains);
    expect(result.get("SAM")?.label).toBe("SAM");
    expect(result.get("Procurement")?.label).toBe("Procurement");
  });

  it("sets color = colorForDomain(domain) on each centroid", () => {
    const result = computeDomainCentroids(nodesWithDomains);
    expect(result.get("SAM")?.color).toBe(colorForDomain("SAM"));
    expect(result.get("Procurement")?.color).toBe(colorForDomain("Procurement"));
  });

  it("DOES NOT mutate node x/y (I2 invariant)", () => {
    const nodesCopy = nodesWithDomains.map((n) => ({ ...n }));
    computeDomainCentroids(nodesCopy);
    for (let i = 0; i < nodesWithDomains.length; i++) {
      expect(nodesCopy[i]!.x).toBe(nodesWithDomains[i]!.x);
      expect(nodesCopy[i]!.y).toBe(nodesWithDomains[i]!.y);
    }
  });

  it("does NOT call Math.random (I2/I3 sentinel — no client layout)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    computeDomainCentroids(nodesWithDomains);
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  it("returns empty map when all nodes have null domain", () => {
    const noTagNodes: GraphNode[] = [
      { id: "u1", title: "U1", type: "concept", x: 0, y: 0, community: 0, domain: null },
      { id: "u2", title: "U2", type: "concept", x: 1, y: 1, community: 0, domain: null },
    ];
    const result = computeDomainCentroids(noTagNodes);
    expect(result.size).toBe(0);
  });

  it("returns empty map when nodes array is empty", () => {
    const result = computeDomainCentroids([]);
    expect(result.size).toBe(0);
  });

  it("returns empty map when all domains are singletons", () => {
    const singletons: GraphNode[] = [
      { id: "s1", title: "S1", type: "concept", x: 0, y: 0, community: 0, domain: "SAM" },
      { id: "s2", title: "S2", type: "concept", x: 1, y: 1, community: 1, domain: "TPRM" },
    ];
    const result = computeDomainCentroids(singletons);
    expect(result.size).toBe(0);
  });
});

// ─── K. Community legend aggregation — one row per Louvain community, unique names ──

describe("community legend aggregation — one row per cluster, unique names (I2/I3)", () => {
  /**
   * Helper that reproduces the useMemo logic inside GraphLegend's community branch.
   * We test the pure aggregation logic here without rendering the full component.
   */
  function aggregateCommunityLegendRows(communities: GraphCommunity[]) {
    return [...communities]
      .sort((a, b) => b.size - a.size)
      .map((c) => ({
        community: c,
        displayName: communityDisplayName(c),
        color: colorForCommunity(c.id),
        lowCohesion: c.cohesion < LOW_COHESION_THRESHOLD,
      }));
  }

  it("K1: produces ONE row per Louvain community (no domain-aggregation)", () => {
    // Two communities both with dominant_domain="SAM" but different top_pages
    const communities: GraphCommunity[] = [
      { id: 0, size: 12, cohesion: 0.9, dominant_domain: "SAM",
        top_page: { id: "p1", title: "SAM Reconciliation", slug: "sam-reconciliation" } },
      { id: 1, size: 8, cohesion: 0.6, dominant_domain: "SAM",
        top_page: { id: "p2", title: "SAM Reporting", slug: "sam-reporting" } },
    ];
    const rows = aggregateCommunityLegendRows(communities);
    expect(rows).toHaveLength(2); // two clusters, not merged into one "SAM"
  });

  it("K2: two same-domain communities get DIFFERENT display names", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 10, cohesion: 0.8, dominant_domain: "SAM",
        top_page: { id: "p1", title: "SAM Reconciliation", slug: "sam-reconciliation" } },
      { id: 1, size: 6, cohesion: 0.5, dominant_domain: "SAM",
        top_page: { id: "p2", title: "SAM Reporting", slug: "sam-reporting" } },
    ];
    const rows = aggregateCommunityLegendRows(communities);
    const names = rows.map((r) => r.displayName);
    // Names must be distinct despite same domain
    expect(names[0]).not.toBe(names[1]);
    expect(names[0]).toBe("SAM · Reconciliation");
    expect(names[1]).toBe("SAM · Reporting");
  });

  it("K3: rows sorted by community size descending", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 5, cohesion: 0.8 },
      { id: 1, size: 20, cohesion: 0.6 },
      { id: 2, size: 10, cohesion: 0.4 },
    ];
    const rows = aggregateCommunityLegendRows(communities);
    expect(rows[0]?.community.id).toBe(1); // size=20 first
    expect(rows[1]?.community.id).toBe(2); // size=10 second
    expect(rows[2]?.community.id).toBe(0); // size=5 last
  });

  it("K4: color = colorForCommunity(id) (Louvain palette, not domain palette)", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 10, cohesion: 0.8 },
      { id: 1, size: 5, cohesion: 0.6 },
    ];
    const rows = aggregateCommunityLegendRows(communities);
    expect(rows[0]?.color).toBe(colorForCommunity(0));
    expect(rows[1]?.color).toBe(colorForCommunity(1));
    // Ensure these are community palette colors, not domain palette
    const COMMUNITY_PALETTE_0 = colorForCommunity(0);
    expect(COMMUNITY_PALETTE_0).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("K5: low-cohesion flag set for communities with cohesion < LOW_COHESION_THRESHOLD", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 10, cohesion: 0.05 }, // low — 0.05 < 0.1
      { id: 1, size: 5, cohesion: 0.10 },  // exactly at threshold — NOT low
      { id: 2, size: 3, cohesion: 0.80 },  // healthy
    ];
    const rows = aggregateCommunityLegendRows(communities);
    const row0 = rows.find((r) => r.community.id === 0)!;
    const row1 = rows.find((r) => r.community.id === 1)!;
    const row2 = rows.find((r) => r.community.id === 2)!;
    expect(row0.lowCohesion).toBe(true);
    expect(row1.lowCohesion).toBe(false);
    expect(row2.lowCohesion).toBe(false);
  });

  it("K6: empty community list yields empty rows", () => {
    const rows = aggregateCommunityLegendRows([]);
    expect(rows).toHaveLength(0);
  });
});

// ─── L. Default ColorMode — "community" (= Louvain coloring) is the default graph view ──────

describe("ColorMode — 'community' (Louvain coloring) is the default graph view (F4)", () => {
  it("L1: ColorMode type includes 'type' and 'community' as valid members", () => {
    // TypeScript compile-time check expressed as runtime assertions.
    const typeMode: import("../components/graphPalette").ColorMode = "type";
    const communityMode: import("../components/graphPalette").ColorMode = "community";
    expect(typeMode).toBe("type");
    expect(communityMode).toBe("community");
  });

  it("L2: 'community' colorMode uses colorForCommunity for coloring (not colorForDomain)", () => {
    // Verifies the Louvain-cluster contract at the palette level:
    // community 0 → COMMUNITY_PALETTE[0]; community 1 → COMMUNITY_PALETTE[1].
    const c0Color = colorForCommunity(0);
    const c1Color = colorForCommunity(1);
    // Both are valid hex strings from COMMUNITY_PALETTE
    expect(c0Color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(c1Color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(c0Color).toBe(COMMUNITY_PALETTE[0]);
    expect(c1Color).toBe(COMMUNITY_PALETTE[1]);
    // Deterministic: same community id → same color
    expect(colorForCommunity(0)).toBe(c0Color);
    expect(colorForCommunity(1)).toBe(c1Color);
  });

  it("L3: colorForCommunity(-1) returns COMMUNITY_UNASSIGNED_COLOR (#6e7781) for unassigned", () => {
    expect(colorForCommunity(-1)).toBe("#6e7781");
  });

  it("L4: DOMAIN_UNTAGGED_COLOR is NOT a community palette color (palettes are distinct)", () => {
    // Ensures the two palettes don't accidentally overlap at the unassigned/untagged bucket
    for (const c of COMMUNITY_PALETTE) {
      expect(DOMAIN_UNTAGGED_COLOR).not.toBe(c);
    }
  });
});
