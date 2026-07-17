/**
 * shell-m5-phase5-mcp-ui.spec.ts — M5 Phase 5 D5 screenshot gate (AC-F1-MCP-UI-9)
 *
 * Captures docs/screens/settings-api-mcp.png.
 * The test navigates to Settings → API + MCP and waits for the real panel to load
 * (tool rows from GET /mcp/info), then saves the screenshot.
 *
 * Prerequisites:
 *   Backend:  http://localhost:8000  (GET /mcp/info must respond)
 *   Frontend: http://localhost:5173  (Vite dev server)
 *
 * Run:
 *   cd frontend && npx playwright test e2e/shell-m5-phase5-mcp-ui.spec.ts
 *
 * AC-F1-MCP-UI-9: docs/screens/settings-api-mcp.png committed after capture.
 */

import { test, expect, type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

// ── Config ──────────────────────────────────────────────────────────────────────

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";
const BACKEND_URL  = process.env["SYNAPSE_BACKEND_URL"]  ?? "http://localhost:8000";

const _thisDir    = path.dirname(fileURLToPath(import.meta.url));
const SCREENS_DIR = path.resolve(_thisDir, "../../docs/screens");

if (!fs.existsSync(SCREENS_DIR)) {
  fs.mkdirSync(SCREENS_DIR, { recursive: true });
}

const VIEWPORT = { width: 1440, height: 900 };

// ── Helpers ──────────────────────────────────────────────────────────────────────

async function loadShell(page: Page): Promise<void> {
  await page.setViewportSize(VIEWPORT);
  await page.goto(`${FRONTEND_URL}/`, { waitUntil: "domcontentloaded" });
  // v1.2 [F18]: app boots to "home" section — no canvas or nav-tree on initial load.
  // Wait only for the app-shell root to confirm the SPA has mounted.
  await page.waitForSelector("[data-testid='app-shell']", { timeout: 15_000 });
  await page.waitForTimeout(400);
}

// ── Backend smoke check ──────────────────────────────────────────────────────────

test("GET /mcp/info responds 200 with >= 4 tools (pre-screenshot smoke)", async ({ request }) => {
  const res = await request.get(`${BACKEND_URL}/mcp/info`);
  expect(res.status()).toBe(200);
  const body = await res.json() as {
    server_name: string;
    transport: string;
    entry_point_command: string;
    tool_count: number;
    tools: { name: string; description: string; input_schema: object }[];
  };
  expect(body.server_name).toBe("synapse");
  expect(body.transport).toBeTruthy();
  expect(body.entry_point_command).toBeTruthy();
  expect(body.tool_count).toBeGreaterThanOrEqual(4);
  expect(body.tools.length).toBe(body.tool_count);
  for (const expectedName of ["search_wiki", "write_page", "get_page", "list_pages"]) {
    expect(body.tools.map((t) => t.name)).toContain(expectedName);
  }
});

// ── D5 screenshot (AC-F1-MCP-UI-9) ─────────────────────────────────────────────

test.describe("D5 screenshot — Settings > API + MCP panel (AC-F1-MCP-UI-9)", () => {
  test("captures settings-api-mcp.png with connection panel and tool list visible", async ({ page }) => {
    await loadShell(page);

    // Open the Settings panel via the nav rail Settings button.
    // NavRail buttons use data-section attribute (not data-testid). [ADR-0018]
    const settingsBtn = page.locator("[data-section='settings']");
    await expect(settingsBtn).toBeVisible({ timeout: 5_000 });
    await settingsBtn.click();

    // Wait for the SettingsPanel to mount.
    await page.waitForSelector("[data-testid='settings-panel']", { timeout: 5_000 });

    // Navigate to the API + MCP section.
    const apiMcpBtn = page.locator("[data-settings-section='apiMcp']");
    await expect(apiMcpBtn).toBeVisible({ timeout: 5_000 });
    await apiMcpBtn.click();

    // Wait for at least one tool row — the real panel has loaded GET /mcp/info.
    await page.waitForSelector("[data-testid^='mcp-tool-row-']", { timeout: 10_000 });

    // Ensure the copy button and snippet are also visible.
    await expect(page.locator("[data-testid='mcp-copy-btn']")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("[data-testid='mcp-snippet']")).toBeVisible({ timeout: 5_000 });

    // All 4 contracted tool names must be present.
    for (const toolName of ["search_wiki", "write_page", "get_page", "list_pages"]) {
      await expect(page.locator(`[data-testid='mcp-tool-name-${toolName}']`)).toBeVisible();
    }

    await page.waitForTimeout(300); // let any micro-animations finish

    const screenshotPath = path.join(SCREENS_DIR, "settings-api-mcp.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });

    const stats = fs.statSync(screenshotPath);
    expect(stats.size).toBeGreaterThan(1000); // at least 1 KB — not a blank page
    console.log(`[D5] Saved: ${screenshotPath} (${(stats.size / 1024).toFixed(1)} KB)`);
  });

  test("docs/screens/settings-api-mcp.png exists after capture", () => {
    const target = path.join(SCREENS_DIR, "settings-api-mcp.png");
    expect(fs.existsSync(target), `${target} must exist after screenshot test`).toBe(true);
  });
});
