/**
 * mcpClient.test.ts — vitest unit tests for fetchMcpInfo (ADR-0027 §2.1).
 *
 * Covers:
 *   AC-F1-MCP-UI-1: fetchMcpInfo returns a typed McpInfoResponse on 200.
 *   AC-F1-MCP-UI-3: degraded state — fetchMcpInfo rejects on non-200.
 *   AC-F1-MCP-UI-6: all 4 tool fields (name, description, input_schema) are present.
 *
 * Uses global fetch mock — no real network calls (I3: display only, no side effects).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchMcpInfo, type McpInfoResponse } from "../api/providerClient";

// ─── 4-tool fixture (mirrors the real synapse MCP server, ADR-0027 §2.2) ─────

const MOCK_RESPONSE: McpInfoResponse = {
  server_name: "synapse",
  transport: "stdio",
  entry_point_command: "python -m app.mcp.server",
  tool_count: 4,
  tools: [
    {
      name: "search_wiki",
      description: "Search the wiki for pages matching a query. Returns ranked results.",
      input_schema: {
        type: "object",
        properties: { query: { type: "string" }, limit: { type: "integer" } },
        required: ["query"],
        additionalProperties: false,
      },
    },
    {
      name: "write_page",
      description: "Write or overwrite a wiki page with the given content.",
      input_schema: {
        type: "object",
        properties: {
          title: { type: "string" },
          content: { type: "string" },
          page_type: { type: "string" },
        },
        required: ["title", "content"],
        additionalProperties: false,
      },
    },
    {
      name: "get_page",
      description: "Retrieve a wiki page by title or ID.",
      input_schema: {
        type: "object",
        properties: { title: { type: "string" } },
        required: ["title"],
        additionalProperties: false,
      },
    },
    {
      name: "list_pages",
      description: "List all wiki pages, optionally filtered by type.",
      input_schema: {
        type: "object",
        properties: { page_type: { type: "string" } },
        additionalProperties: false,
      },
    },
  ],
};

// ─── fetch mock helpers ────────────────────────────────────────────────────────

function mockFetchOk(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    }),
  );
}

function mockFetchError(status: number, statusText = "Internal Server Error"): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status,
      statusText,
      json: () => Promise.resolve({ detail: statusText }),
    }),
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("fetchMcpInfo — success path (AC-F1-MCP-UI-1/6)", () => {
  beforeEach(() => { mockFetchOk(MOCK_RESPONSE); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("resolves to a typed McpInfoResponse", async () => {
    const result = await fetchMcpInfo();
    expect(result).toEqual(MOCK_RESPONSE);
  });

  it("server_name is 'synapse'", async () => {
    const result = await fetchMcpInfo();
    expect(result.server_name).toBe("synapse");
  });

  it("transport is 'stdio'", async () => {
    const result = await fetchMcpInfo();
    expect(result.transport).toBe("stdio");
  });

  it("entry_point_command is non-empty", async () => {
    const result = await fetchMcpInfo();
    expect(result.entry_point_command.length).toBeGreaterThan(0);
  });

  it("tool_count matches the length of tools array", async () => {
    const result = await fetchMcpInfo();
    expect(result.tool_count).toBe(result.tools.length);
  });

  it("returns exactly 4 tools", async () => {
    const result = await fetchMcpInfo();
    expect(result.tools).toHaveLength(4);
  });

  it("all 4 expected tool names are present", async () => {
    const result = await fetchMcpInfo();
    const names = result.tools.map((t) => t.name);
    expect(names).toContain("search_wiki");
    expect(names).toContain("write_page");
    expect(names).toContain("get_page");
    expect(names).toContain("list_pages");
  });

  it("each tool has a non-empty description", async () => {
    const result = await fetchMcpInfo();
    for (const tool of result.tools) {
      expect(tool.description.length).toBeGreaterThan(0);
    }
  });

  it("each tool has an input_schema object", async () => {
    const result = await fetchMcpInfo();
    for (const tool of result.tools) {
      expect(typeof tool.input_schema).toBe("object");
      expect(tool.input_schema).not.toBeNull();
    }
  });

  it("search_wiki has 2 properties in input_schema", async () => {
    const result = await fetchMcpInfo();
    const tool = result.tools.find((t) => t.name === "search_wiki")!;
    expect(Object.keys(tool.input_schema.properties ?? {}).length).toBe(2);
  });

  it("write_page has 3 properties in input_schema", async () => {
    const result = await fetchMcpInfo();
    const tool = result.tools.find((t) => t.name === "write_page")!;
    expect(Object.keys(tool.input_schema.properties ?? {}).length).toBe(3);
  });

  it("get_page has 1 property in input_schema", async () => {
    const result = await fetchMcpInfo();
    const tool = result.tools.find((t) => t.name === "get_page")!;
    expect(Object.keys(tool.input_schema.properties ?? {}).length).toBe(1);
  });

  it("list_pages has 1 property in input_schema", async () => {
    const result = await fetchMcpInfo();
    const tool = result.tools.find((t) => t.name === "list_pages")!;
    expect(Object.keys(tool.input_schema.properties ?? {}).length).toBe(1);
  });

  it("calls GET /mcp/info (correct URL suffix)", async () => {
    await fetchMcpInfo();
    const mockFetch = vi.mocked(fetch);
    expect(mockFetch).toHaveBeenCalledOnce();
    const calledUrl = mockFetch.mock.calls[0]?.[0] as string;
    expect(calledUrl).toMatch(/\/mcp\/info$/);
  });

  it("passes the AbortSignal when provided", async () => {
    const ac = new AbortController();
    await fetchMcpInfo(ac.signal);
    const mockFetch = vi.mocked(fetch);
    const callArg = mockFetch.mock.calls[0]?.[1] as { signal?: AbortSignal } | undefined;
    expect(callArg?.signal).toBe(ac.signal);
  });
});

describe("fetchMcpInfo — error path (AC-F1-MCP-UI-3 degraded)", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("rejects with an ApiError on 500", async () => {
    mockFetchError(500);
    await expect(fetchMcpInfo()).rejects.toThrow();
  });

  it("rejects with an ApiError on 404", async () => {
    mockFetchError(404, "Not Found");
    await expect(fetchMcpInfo()).rejects.toThrow();
  });

  it("rejects when fetch itself throws (e.g. AbortError)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError")));
    await expect(fetchMcpInfo()).rejects.toThrow();
  });
});

// ─── Claude Desktop snippet generation logic (ADR-0027 §2.4) ─────────────────
// This logic lives in the React component but the algorithm is pure enough to
// test here by reconstructing it inline.

describe("Claude Desktop config snippet — tokenisation contract (ADR-0027 §2.4)", () => {
  function buildSnippet(mcpInfo: McpInfoResponse): {
    mcpServers: Record<string, { command: string; args: string[] }>;
  } {
    const tokens = mcpInfo.entry_point_command.trim().split(/\s+/);
    const command = tokens[0] ?? "";
    const args = tokens.slice(1);
    return { mcpServers: { [mcpInfo.server_name]: { command, args } } };
  }

  it("server is keyed by server_name from the payload", () => {
    const snippet = buildSnippet(MOCK_RESPONSE);
    expect(snippet.mcpServers["synapse"]).toBeDefined();
  });

  it("command is argv[0] of entry_point_command", () => {
    const snippet = buildSnippet(MOCK_RESPONSE);
    expect(snippet.mcpServers["synapse"]!.command).toBe("python");
  });

  it("args is the rest of entry_point_command tokens", () => {
    const snippet = buildSnippet(MOCK_RESPONSE);
    expect(snippet.mcpServers["synapse"]!.args).toEqual(["-m", "app.mcp.server"]);
  });

  it("no hardcoded server name — derived entirely from payload.server_name", () => {
    const custom = { ...MOCK_RESPONSE, server_name: "custom-server" };
    const snippet = buildSnippet(custom);
    expect(snippet.mcpServers["custom-server"]).toBeDefined();
    expect(snippet.mcpServers["synapse"]).toBeUndefined();
  });

  it("no hardcoded command — derived from payload.entry_point_command", () => {
    const custom = { ...MOCK_RESPONSE, entry_point_command: "node dist/server.js --mcp" };
    const snippet = buildSnippet(custom);
    expect(snippet.mcpServers["synapse"]!.command).toBe("node");
    expect(snippet.mcpServers["synapse"]!.args).toEqual(["dist/server.js", "--mcp"]);
  });

  it("single-token command produces empty args array", () => {
    const custom = { ...MOCK_RESPONSE, entry_point_command: "synapse-mcp" };
    const snippet = buildSnippet(custom);
    expect(snippet.mcpServers["synapse"]!.command).toBe("synapse-mcp");
    expect(snippet.mcpServers["synapse"]!.args).toEqual([]);
  });

  it("serialises to valid JSON", () => {
    const snippet = buildSnippet(MOCK_RESPONSE);
    expect(() => JSON.stringify(snippet)).not.toThrow();
  });
});
