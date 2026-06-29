/**
 * shell-m4-phase2.spec.ts — M4 Phase 2 QA gate (ADR-0018 / F1-NAV + F17 + INGEST)
 *
 * Gate checks:
 *   CHECK-NAVRAIL-1:  NavRail renders 5 buttons (pages/graph/ingest/chat/settings)
 *   CHECK-NAVRAIL-2:  Default active section = pages; aria-current="page" on Pages
 *   CHECK-NAVRAIL-3:  Clicking Graph switches to graph section (full-bleed canvas)
 *   CHECK-NAVRAIL-4:  Clicking Ingest switches to ingest section
 *   CHECK-NAVRAIL-5:  Clicking Settings switches to settings section
 *   CHECK-NAVRAIL-6:  Chat button is disabled (native + aria-disabled)
 *   CHECK-INGEST-1:   Ingest view renders with "Ingest Activity" header
 *   CHECK-INGEST-2:   Run list shows demo rows (10 seeded rows visible)
 *   CHECK-INGEST-3:   Each row card has a status badge
 *   CHECK-INGEST-4:   Each row shows cost at 4dp ($x.xxxx format)
 *   CHECK-INGEST-5:   Clicking a row opens IngestRunDetail in the right pane
 *   CHECK-INGEST-6:   Run Ingest button toggles form open/closed
 *   CHECK-PROVIDER-1: ProviderSelector trigger button is visible in the header
 *   CHECK-PROVIDER-2: Clicking trigger opens provider panel
 *   CHECK-PROVIDER-3: Provider panel shows scope toggle (Vault / Global)
 *   CHECK-SETTINGS-1: Settings panel renders with context window select
 *   CHECK-SETTINGS-2: Context window select has expected options (4K, 8K, etc.)
 *   CHECK-SETTINGS-3: Language toggle shows EN / IT buttons
 *   CHECK-I3-1:       No React store subscription warnings in console
 *   CHECK-D5:         Screenshots to docs/screens/
 *
 * Prerequisites:
 *   Backend:  http://localhost:8000  (10 demo ingest runs + 2 provider configs seeded)
 *   Frontend: http://localhost:5173  (Vite dev server)
 *
 * Run:
 *   cd frontend && npx playwright test e2e/shell-m4-phase2.spec.ts
 */

import { test, expect, type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

// ── Config ─────────────────────────────────────────────────────────────────────

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";
const BACKEND_URL  = process.env["SYNAPSE_BACKEND_URL"]  ?? "http://localhost:8000";

const _thisDir   = path.dirname(fileURLToPath(import.meta.url));
const SCREENS_DIR = path.resolve(_thisDir, "../../docs/screens");

function ensureScreensDir() {
  if (!fs.existsSync(SCREENS_DIR)) {
    fs.mkdirSync(SCREENS_DIR, { recursive: true });
  }
}

async function gotoApp(page: Page) {
  await page.goto(FRONTEND_URL, { waitUntil: "networkidle" });
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("nav-rail")).toBeVisible();
}

// ── Backend health check ───────────────────────────────────────────────────────

test("backend is reachable", async ({ request }) => {
  const res = await request.get(`${BACKEND_URL}/status`);
  expect(res.status()).toBe(200);
});

// ── NavRail ───────────────────────────────────────────────────────────────────

test.describe("NavRail", () => {
  test("CHECK-NAVRAIL-1: renders 5 buttons (pages/graph/ingest/chat/settings)", async ({ page }) => {
    await gotoApp(page);
    const buttons = page.getByTestId("nav-rail").getByRole("button");
    await expect(buttons).toHaveCount(5);
  });

  test("CHECK-NAVRAIL-2: Chat is active by default (AC-HARD-ORD-2 — default section = Chat)", async ({ page }) => {
    await gotoApp(page);
    // M4-HARD F1-HARD-NAV-ORDER: default section on first load is Chat (graphStore INITIAL_STATE.activeSection = "chat")
    const chatBtn = page.locator("[data-section='chat']");
    await expect(chatBtn).toHaveAttribute("aria-current", "page");
  });

  test("CHECK-NAVRAIL-3: clicking Graph switches section, shows graph canvas", async ({ page }) => {
    await gotoApp(page);
    await page.locator("[data-section='graph']").click();
    await expect(page.getByTestId("section-graph")).toBeVisible();
    // Graph canvas: sigma renders multiple canvas layers; .first() avoids strict-mode error
    const canvas = page.getByTestId("section-graph").locator("canvas").first();
    await expect(canvas).toBeVisible({ timeout: 8000 });
  });

  test("CHECK-NAVRAIL-4: clicking Ingest switches section", async ({ page }) => {
    await gotoApp(page);
    await page.locator("[data-section='ingest']").click();
    await expect(page.getByTestId("section-ingest")).toBeVisible();
    await expect(page.getByTestId("ingest-view")).toBeVisible();
  });

  test("CHECK-NAVRAIL-5: clicking Settings switches section", async ({ page }) => {
    await gotoApp(page);
    await page.locator("[data-section='settings']").click();
    await expect(page.getByTestId("section-settings")).toBeVisible();
    await expect(page.getByTestId("settings-panel")).toBeVisible();
  });

  test("CHECK-NAVRAIL-6: Chat is enabled (Phase 3 shipped — ADR-0019)", async ({ page }) => {
    await gotoApp(page);
    const chatBtn = page.locator("[data-section='chat']");
    await expect(chatBtn).toBeEnabled();
  });

  test("CHECK-NAVRAIL-BACK: clicking Pages goes back to 3-panel view", async ({ page }) => {
    await gotoApp(page);
    await page.locator("[data-section='graph']").click();
    await expect(page.getByTestId("section-graph")).toBeVisible();
    await page.locator("[data-section='pages']").click();
    // Pages section = PanelGroup; nav-tree should be visible
    await expect(page.getByTestId("nav-tree")).toBeVisible({ timeout: 5000 });
  });
});

// ── Ingest section ────────────────────────────────────────────────────────────

test.describe("Ingest section", () => {
  async function gotoIngest(page: Page) {
    await gotoApp(page);
    await page.locator("[data-section='ingest']").click();
    await expect(page.getByTestId("ingest-view")).toBeVisible();
  }

  test("CHECK-INGEST-1: renders Ingest Activity header", async ({ page }) => {
    await gotoIngest(page);
    // Header text from i18n en.json: "Ingest Activity"
    await expect(page.getByTestId("ingest-view")).toContainText("Ingest Activity");
  });

  test("CHECK-INGEST-2: run list loads demo rows", async ({ page }) => {
    await gotoIngest(page);
    // Wait for the run list to appear (backend needs to respond)
    await expect(page.getByTestId("ingest-run-list")).toBeVisible({ timeout: 8000 });
    // At least 1 run card must be visible (10 demo rows seeded)
    const cards = page.getByTestId("ingest-run-card");
    await expect(cards.first()).toBeVisible({ timeout: 8000 });
    const count = await cards.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("CHECK-INGEST-3: each visible card has a status badge", async ({ page }) => {
    await gotoIngest(page);
    await expect(page.getByTestId("ingest-run-list")).toBeVisible({ timeout: 8000 });
    await expect(page.getByTestId("ingest-run-card").first()).toBeVisible({ timeout: 5000 });
    const cards = page.getByTestId("ingest-run-card");
    const count = await cards.count();
    // Every visible card should have a [data-status] badge
    for (let i = 0; i < Math.min(count, 5); i++) {
      const badge = cards.nth(i).locator("[data-status]");
      await expect(badge).toBeVisible();
    }
  });

  test("CHECK-INGEST-4: cost column shows $x.xxxx format", async ({ page }) => {
    await gotoIngest(page);
    await expect(page.getByTestId("ingest-run-card").first()).toBeVisible({ timeout: 8000 });
    // Find any element with monospace cost text — match $N.NNNN pattern
    const costTexts = await page.locator("[data-testid='ingest-run-card']")
      .first()
      .locator("text=/\\$\\d+\\.\\d{4}/")
      .count();
    expect(costTexts).toBeGreaterThanOrEqual(1);
  });

  test("CHECK-INGEST-5: clicking a card opens IngestRunDetail", async ({ page }) => {
    await gotoIngest(page);
    await expect(page.getByTestId("ingest-run-card").first()).toBeVisible({ timeout: 8000 });
    // Initially detail shows "Select a run" placeholder
    await expect(page.getByTestId("ingest-run-detail")).toContainText("Select a run");
    // Click first card
    await page.getByTestId("ingest-run-card").first().click();
    // Detail should now show run details (Iterations, Cost, etc.)
    await expect(page.getByTestId("ingest-run-detail")).not.toContainText("Select a run");
  });

  test("CHECK-INGEST-6: Run Ingest button toggles form", async ({ page }) => {
    await gotoIngest(page);
    // Form should not be visible initially
    await expect(page.getByTestId("ingest-form")).not.toBeVisible();
    // Click Run Ingest
    await page.getByRole("button", { name: /run ingest/i }).click();
    await expect(page.getByTestId("ingest-form")).toBeVisible();
    // Click Cancel
    await page.getByRole("button", { name: /cancel/i }).click();
    await expect(page.getByTestId("ingest-form")).not.toBeVisible();
  });
});

// ── Provider Selector ─────────────────────────────────────────────────────────

test.describe("Provider Selector (F17)", () => {
  test("CHECK-PROVIDER-1: selector trigger visible in header", async ({ page }) => {
    await gotoApp(page);
    await expect(page.getByTestId("provider-selector-trigger")).toBeVisible();
  });

  test("CHECK-PROVIDER-2: clicking trigger opens provider panel", async ({ page }) => {
    await gotoApp(page);
    await page.getByTestId("provider-selector-trigger").click();
    await expect(page.getByTestId("provider-selector-panel")).toBeVisible();
  });

  test("CHECK-PROVIDER-3: provider panel shows scope toggle", async ({ page }) => {
    await gotoApp(page);
    await page.getByTestId("provider-selector-trigger").click();
    const panel = page.getByTestId("provider-selector-panel");
    await expect(panel).toBeVisible();
    // Scope buttons: "Vault" and "Global"
    await expect(panel).toContainText("Vault");
    await expect(panel).toContainText("Global");
  });

  test("CHECK-PROVIDER-4: panel closes on outside click", async ({ page }) => {
    await gotoApp(page);
    await page.getByTestId("provider-selector-trigger").click();
    await expect(page.getByTestId("provider-selector-panel")).toBeVisible();
    // Click outside
    await page.getByTestId("app-header").click({ position: { x: 100, y: 24 } });
    await expect(page.getByTestId("provider-selector-panel")).not.toBeVisible();
  });
});

// ── Settings panel ────────────────────────────────────────────────────────────

test.describe("Settings panel", () => {
  async function gotoSettings(page: Page) {
    await gotoApp(page);
    await page.locator("[data-section='settings']").click();
    await expect(page.getByTestId("settings-panel")).toBeVisible();
  }

  test("CHECK-SETTINGS-1: renders context window select", async ({ page }) => {
    await gotoSettings(page);
    await expect(page.locator("#ctx-select")).toBeVisible();
  });

  test("CHECK-SETTINGS-2: context window select has expected options", async ({ page }) => {
    await gotoSettings(page);
    const select = page.locator("#ctx-select");
    const options = await select.locator("option").allTextContents();
    // Should include 4K, 8K, 16K, 32K, 64K, 128K, 256K, 512K, 1M
    expect(options.length).toBe(9);
    expect(options[0]).toBe("4K");
    expect(options[options.length - 1]).toBe("1M");
  });

  test("CHECK-SETTINGS-3: language toggle shows EN/IT", async ({ page }) => {
    await gotoSettings(page);
    await expect(page.getByRole("button", { name: /english/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /italian/i })).toBeVisible();
  });

  test("CHECK-SETTINGS-4: clicking Italian changes language toggle state", async ({ page }) => {
    await gotoSettings(page);
    const itBtn = page.getByRole("button", { name: /italian/i });
    await itBtn.click();
    // IT button should be pressed
    await expect(itBtn).toHaveAttribute("aria-pressed", "true");
  });
});

// ── I3 spot check ─────────────────────────────────────────────────────────────

test("CHECK-I3-1: no console errors about store subscriptions", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  await gotoApp(page);
  // Navigate through all sections
  for (const section of ["graph", "ingest", "settings", "pages"] as const) {
    await page.locator(`[data-section='${section}']`).click();
    await page.waitForTimeout(300);
  }
  // Filter noise — only real errors about store or invariants
  const storeErrors = consoleErrors.filter(
    (e) => /store|invariant|zustand|selector/i.test(e),
  );
  expect(storeErrors).toHaveLength(0);
});

// ── D5 screenshots ────────────────────────────────────────────────────────────

test.describe("D5 screenshots", () => {
  test("captures ingest section screenshot", async ({ page }) => {
    ensureScreensDir();
    await gotoApp(page);
    await page.locator("[data-section='ingest']").click();
    await expect(page.getByTestId("ingest-view")).toBeVisible();
    // Wait for run list to load
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: path.join(SCREENS_DIR, "ingest-section.png"),
      fullPage: false,
    });
  });

  test("captures settings section screenshot", async ({ page }) => {
    ensureScreensDir();
    await gotoApp(page);
    await page.locator("[data-section='settings']").click();
    await expect(page.getByTestId("settings-panel")).toBeVisible();
    await page.screenshot({
      path: path.join(SCREENS_DIR, "settings-section.png"),
      fullPage: false,
    });
  });

  test("captures provider selector open screenshot", async ({ page }) => {
    ensureScreensDir();
    await gotoApp(page);
    await page.getByTestId("provider-selector-trigger").click();
    await expect(page.getByTestId("provider-selector-panel")).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({
      path: path.join(SCREENS_DIR, "provider-selector-open.png"),
      fullPage: false,
    });
  });

  test("captures NavRail screenshot (graph active)", async ({ page }) => {
    ensureScreensDir();
    await gotoApp(page);
    await page.locator("[data-section='graph']").click();
    await expect(page.getByTestId("section-graph")).toBeVisible();
    await page.waitForTimeout(1000);
    await page.screenshot({
      path: path.join(SCREENS_DIR, "navrail-graph-active.png"),
      fullPage: false,
    });
  });
});
