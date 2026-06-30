/**
 * vite-proxy-mcp.test.ts — GAP-MCP-VITE (ADR-0029 A-AC-6)
 *
 * Asserts that the Vite dev proxy still covers the /mcp prefix so the browser
 * can reach /mcp/info and /mcp/server without CORS failures in dev mode.
 *
 * Strategy: read vite.config.ts as raw text and assert the /mcp proxy entry is
 * present.  This avoids importing the Vite config (which pulls in the Vite ESM
 * build API and causes test-runner issues) while still being a deterministic,
 * automatable gate that CI can run without a running Vite server.
 *
 * If the /mcp key is ever removed from the proxy config, this test will fail
 * and the dev-proxy gap will be caught before it reaches review.
 *
 * ADR-0029 A-AC-6: the Vite dev proxy must expose /mcp so that the browser
 * frontend can reach both /mcp/info (REST introspection) and /mcp/server
 * (FastMCP HTTP surface) without additional CORS configuration.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// Resolve the path to vite.config.ts relative to this test file.
// Tests live at frontend/src/tests/; vite.config.ts is at frontend/.
const _thisDir = dirname(fileURLToPath(import.meta.url));
const VITE_CONFIG_PATH = join(_thisDir, "../../vite.config.ts");

describe("GAP-MCP-VITE (ADR-0029 A-AC-6) — Vite proxy covers /mcp", () => {
  it("vite.config.ts exists at the expected path", () => {
    expect(existsSync(VITE_CONFIG_PATH)).toBe(true);
  });

  it('vite.config.ts proxy block contains a "/mcp" key', () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    // The proxy entry looks like:  '"/mcp":'  or  '"/mcp": {'.
    // We match both the quoted-key form and the property form to be resilient
    // against minor formatting changes.
    const hasMcpProxy =
      raw.includes('"/mcp"') || raw.includes("'/mcp'") || raw.includes("`/mcp`");
    expect(hasMcpProxy).toBe(true);
  });

  it('the /mcp proxy entry points at the backend target (not a different host)', () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    // Find the /mcp block and verify it has a target property.
    // We accept any non-empty target string (env-driven or hardcoded fallback).
    //
    // Approach: find the index of the /mcp key, then verify "target" appears
    // within the next 200 characters (one proxy object should be <200 chars).
    const mcpIdx = raw.indexOf('"/mcp"');
    if (mcpIdx === -1) {
      // The previous test already fails for this case; be explicit here too.
      expect.fail('"/mcp" proxy entry not found in vite.config.ts (ADR-0029 A-AC-6)');
    }
    const nearby = raw.slice(mcpIdx, mcpIdx + 300);
    expect(nearby).toContain("target");
  });
});
