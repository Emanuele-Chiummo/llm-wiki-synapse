/**
 * playwright.config.ts — Playwright E2E test configuration for Synapse frontend.
 *
 * Tests in frontend/e2e/ are DEFERRED-TO-LIVE. They require:
 *   - Backend running at SYNAPSE_BACKEND_URL (default: http://localhost:8000)
 *   - Frontend running at SYNAPSE_FRONTEND_URL (default: http://localhost:5173)
 *   - Postgres seeded with the G4 fixture (seed_graph_fixture.py)
 *
 * Run:
 *   cd frontend && npx playwright test e2e/graph-perf.spec.ts
 *
 * D5 screenshot output: docs/screens/ (relative to repo root)
 *
 * References:
 *   - AC-F4-6: G2 long-task test (EC-M3-5)
 *   - AC-F4-7: G4 fps test (EC-M3-6)
 *   - AC-FE-1: viewer loads (EC-M3-7)
 *   - AC-D5-1..4: D5 screenshots (EC-M3-11)
 *   - ADR-0014: X-Graph-Cache header assertions
 *   - ADR-0015: no client-side layout (static bundle check in vitest; Playwright proves runtime)
 */

import { defineConfig, devices } from "@playwright/test";

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";
const FRONTEND_ORIGIN = new URL(FRONTEND_URL).origin;

const SETUP_COMPLETED_STATE = {
  version: 1,
  status: "completed",
  lastStep: 4,
  connectionVerified: true,
  providerVerified: true,
  providerFingerprint: null,
  updatedAt: "2026-01-01T00:00:00.000Z",
};

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",

  // Generous timeout for graph render (WebGL init + /graph API call)
  timeout: 30_000,

  // Expect timeout for individual assertions
  expect: { timeout: 10_000 },

  // Fail immediately on first failure in CI (don't waste time)
  fullyParallel: false,
  workers: 1,

  // Reporters
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],

  use: {
    // Base URL = frontend dev server
    baseURL: FRONTEND_URL,

    // The E2E suite exercises the post-setup product shell. First-run behavior
    // is covered by focused unit tests, so keep the wizard from overlaying every
    // page in clean browser contexts.
    storageState: {
      cookies: [],
      origins: [
        {
          origin: FRONTEND_ORIGIN,
          localStorage: [
            { name: "synapse.setupCompleted", value: "1" },
            { name: "synapse.setupState", value: JSON.stringify(SETUP_COMPLETED_STATE) },
          ],
        },
      ],
    },

    // Always capture trace on failure for debugging
    trace: "on-first-retry",
    screenshot: "only-on-failure",

    // Chromium headless for CI; switch to headed for manual inspection
    headless: true,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
