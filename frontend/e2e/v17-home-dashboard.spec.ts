/**
 * v17-home-dashboard.spec.ts — D5 screenshot of the redesigned Home dashboard.
 *
 * Captures the v1.7.0 WS-F "second pass" Home (docs/screens/home-dashboard.png, 1440x900):
 *   - composition hero: total pages set large over a jewel-tone per-type bar + legend
 *   - semantic KPI states (lint 0 → green "clean")
 *   - primary ingest quick-action
 *   - color-coded review-type chips in the "Da revisionare" preview
 *
 * Mirrors the D5 capture convention in shell-m4-phase1.spec.ts (CHECK-4): the PNG is written
 * straight into docs/screens/ and committed. Requires the frontend + backend to be running
 * (SYNAPSE_FRONTEND_URL / SYNAPSE_BACKEND_URL), same as the other e2e specs.
 */
import { test, expect, type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";

const _thisDir = path.dirname(fileURLToPath(import.meta.url));
const SCREENS_DIR = path.resolve(_thisDir, "../../docs/screens");
if (!fs.existsSync(SCREENS_DIR)) {
  fs.mkdirSync(SCREENS_DIR, { recursive: true });
}

const VIEWPORT = { width: 1440, height: 900 };

async function loadHome(page: Page): Promise<void> {
  await page.setViewportSize(VIEWPORT);
  await page.goto(`${FRONTEND_URL}/`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("[data-testid='app-shell']", { timeout: 15_000 });

  // The first-run setup wizard supports Esc-to-dismiss (see FirstRunWizard). Close it if present
  // so it doesn't cover the dashboard — it is absent in CI (setup already completed) but shows on
  // a fresh local profile.
  const overlay = page.locator("[data-testid='wizard-overlay']");
  if (await overlay.isVisible().catch(() => false)) {
    await page.keyboard.press("Escape");
    await overlay.waitFor({ state: "hidden", timeout: 5_000 }).catch(() => {});
  }

  // The app boots to the home section; click it explicitly for robustness.
  await page.locator("[data-section='home']").click();
  // The composition hero is the v1.7.0 redesign's anchor — wait for it before shooting.
  await page.waitForSelector("[data-testid='home-composition-hero']", { timeout: 10_000 });
  await page.waitForTimeout(400); // let the type bar + sparklines settle
}

test.describe("D5 — Home dashboard screenshot (v1.7.0 redesign, I8 / WS-F)", () => {
  test("capture: home-dashboard.png (composition hero, semantic KPIs, review chips)", async ({
    page,
  }) => {
    await loadHome(page);
    await expect(page.locator("[data-testid='home-composition-hero']")).toBeVisible();
    await page.screenshot({
      path: path.join(SCREENS_DIR, "home-dashboard.png"),
      fullPage: false,
    });
  });
});
