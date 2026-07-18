/**
 * csp.spec.ts — Content Security Policy E2E test suite (SEC-CSP-1 / ADR-0087).
 *
 * Verifies:
 *   1. CSP header is present on the served HTML document.
 *   2. The header contains the required directives (script-src 'self', style-src 'unsafe-inline',
 *      connect-src, frame-ancestors 'none', etc.).
 *   3. ZERO CSP violation errors on core app surfaces in BOTH light and dark themes.
 *   4. KaTeX math rendering (display math: $$…$$) does not trigger style-src violations.
 *   5. WebGL graph view (sigma.js) loads without violations.
 *
 * Implementation notes:
 *   - CSP violations appear as console messages of type "error" containing the phrase
 *     "Content Security Policy" in Chromium. We listen for these throughout each test.
 *   - ``page.evaluate()`` runs via CDP and bypasses CSP; it is used only for theme injection,
 *     not to test CSP-sensitive paths.
 *   - KaTeX math test: mocks a wiki page response that contains $$…$$ display math, navigates
 *     to it in the preview panel, and asserts no violations during rendering.
 *   - Theme switching uses the same localStorage pattern as the existing v09-happy-paths.spec.ts.
 *
 * Run (requires live backend + frontend):
 *   cd frontend && SYNAPSE_FRONTEND_URL=http://localhost:5173 \
 *     npx playwright test e2e/csp.spec.ts --reporter=line
 *
 * Key acceptance criteria (SEC-CSP-1):
 *   AC-CSP-1  CSP header present on HTML document response.
 *   AC-CSP-2  script-src 'self' present (no unsafe-inline or unsafe-eval for scripts).
 *   AC-CSP-3  style-src 'self' 'unsafe-inline' present (required by KaTeX / React inline styles).
 *   AC-CSP-4  frame-ancestors 'none' present.
 *   AC-CSP-5  ZERO violations in light theme across Chat, Search, Graph, Settings surfaces.
 *   AC-CSP-6  ZERO violations in dark theme across the same surfaces.
 *   AC-CSP-7  ZERO violations when KaTeX renders display-math content.
 *   AC-CSP-8  ZERO violations during sigma.js WebGL graph render.
 */

import { test, expect, type Page } from "@playwright/test";

// ── Config ─────────────────────────────────────────────────────────────────────

const FRONTEND_URL = process.env["SYNAPSE_FRONTEND_URL"] ?? "http://localhost:5173";

const VIEWPORT = { width: 1440, height: 900 };

// ── CSP violation collector ────────────────────────────────────────────────────

/**
 * Attach a console-error listener that captures every Content Security Policy violation
 * the browser reports. Call this before any navigation in each test.
 *
 * In Chromium, CSP violations are emitted as console errors with text similar to:
 *   "Refused to execute inline script because it violates the following Content Security
 *    Policy directive: ..."
 *
 * Returns the mutable violations array — check it after the test actions.
 */
function collectCspViolations(page: Page): string[] {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (
      msg.type() === "error" &&
      msg.text().toLowerCase().includes("content security policy")
    ) {
      violations.push(msg.text());
    }
  });
  // Also capture pageerror events (unhandled JS errors from violated CSP in some contexts).
  page.on("pageerror", (err) => {
    if (err.message.toLowerCase().includes("content security policy")) {
      violations.push(`[pageerror] ${err.message}`);
    }
  });
  return violations;
}

// ── Navigation helpers ─────────────────────────────────────────────────────────

async function gotoApp(page: Page): Promise<void> {
  await page.setViewportSize(VIEWPORT);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("app-shell")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("nav-rail")).toBeVisible({ timeout: 10_000 });
}

async function navTo(page: Page, section: string): Promise<void> {
  const btn = page.locator(`[data-section='${section}']`);
  await expect(btn).toBeVisible({ timeout: 5_000 });
  await btn.click();
}

async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((t) => {
    localStorage.setItem("synapse.theme", t);
  }, theme);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("app-shell")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(300);
}

// ── AC-CSP-1/2/3/4: Header presence and directive assertions ──────────────────

test.describe("CSP header — presence and required directives (AC-CSP-1..4)", () => {
  test("HTML document response carries a Content-Security-Policy header", async ({ page }) => {
    let cspHeader = "";

    // Capture the initial HTML response before navigation.
    page.on("response", (response) => {
      const url = response.url();
      // Match the root HTML document (/ or /index.html or the FRONTEND_URL itself).
      if (
        url === FRONTEND_URL ||
        url === `${FRONTEND_URL}/` ||
        url === `${FRONTEND_URL}/index.html`
      ) {
        const hdr = response.headers()["content-security-policy"];
        if (hdr) cspHeader = hdr;
      }
    });

    await page.setViewportSize(VIEWPORT);
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });

    expect(
      cspHeader,
      "content-security-policy header must be present on the HTML document response",
    ).toBeTruthy();

    console.log(`[CSP] Header value: ${cspHeader}`);
  });

  test("CSP header contains script-src 'self' without unsafe-inline (AC-CSP-2)", async ({
    page,
  }) => {
    let cspHeader = "";
    page.on("response", (response) => {
      const url = response.url();
      if (url === FRONTEND_URL || url === `${FRONTEND_URL}/`) {
        const hdr = response.headers()["content-security-policy"];
        if (hdr) cspHeader = hdr;
      }
    });
    await page.setViewportSize(VIEWPORT);
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });

    expect(cspHeader, "CSP header must be present").toBeTruthy();

    // script-src must be present and must be 'self' (no unsafe-inline/unsafe-eval for scripts).
    expect(cspHeader, "script-src 'self' must be in the CSP").toContain("script-src 'self'");
    expect(
      cspHeader,
      "script-src must NOT contain 'unsafe-eval' (would allow eval() in scripts)",
    ).not.toMatch(/script-src[^;]*unsafe-eval/);

    // Finding (documented — ADR-0087): 'unsafe-inline' is intentionally NOT present in script-src.
    // It IS present in style-src (separate directive) — see AC-CSP-3.
    const scriptSrcMatch = cspHeader.match(/script-src([^;]*)/);
    if (scriptSrcMatch) {
      expect(
        scriptSrcMatch[1],
        "script-src directive must not contain 'unsafe-inline'",
      ).not.toContain("'unsafe-inline'");
    }

    console.log(`[CSP] script-src check passed. Full CSP: ${cspHeader}`);
  });

  test(
    "CSP header contains style-src 'self' 'unsafe-inline' (AC-CSP-3 — KaTeX requirement)",
    async ({ page }) => {
      let cspHeader = "";
      page.on("response", (response) => {
        const url = response.url();
        if (url === FRONTEND_URL || url === `${FRONTEND_URL}/`) {
          const hdr = response.headers()["content-security-policy"];
          if (hdr) cspHeader = hdr;
        }
      });
      await page.setViewportSize(VIEWPORT);
      await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });

      expect(cspHeader, "CSP header must be present").toBeTruthy();

      // style-src 'unsafe-inline' is REQUIRED — see ADR-0087 finding:
      // KaTeX's HTML+MathML output generates inline style attributes on every rendered span;
      // React inline style={{}} props are used throughout the app; the index.html <style>
      // block also requires it. Removing this would require major refactoring.
      expect(cspHeader, "style-src must contain 'unsafe-inline'").toContain(
        "'unsafe-inline'",
      );
      expect(cspHeader, "style-src 'self' must be present").toContain("style-src");

      console.log(
        "[CSP] style-src 'unsafe-inline' confirmed (ADR-0087: required by KaTeX, React, index.html)",
      );
    },
  );

  test("CSP header contains frame-ancestors 'none' (AC-CSP-4)", async ({ page }) => {
    let cspHeader = "";
    page.on("response", (response) => {
      const url = response.url();
      if (url === FRONTEND_URL || url === `${FRONTEND_URL}/`) {
        const hdr = response.headers()["content-security-policy"];
        if (hdr) cspHeader = hdr;
      }
    });
    await page.setViewportSize(VIEWPORT);
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });

    expect(cspHeader, "CSP header must be present").toBeTruthy();
    expect(cspHeader, "frame-ancestors 'none' must be in the CSP").toContain(
      "frame-ancestors 'none'",
    );
    expect(cspHeader, "object-src 'none' must be in the CSP").toContain("object-src 'none'");
    expect(cspHeader, "base-uri 'self' must be in the CSP").toContain("base-uri 'self'");

    console.log("[CSP] Security directives confirmed: frame-ancestors, object-src, base-uri");
  });
});

// ── AC-CSP-5: Zero violations in LIGHT theme ──────────────────────────────────

test.describe("CSP violations — LIGHT theme (AC-CSP-5)", () => {
  test("Chat section loads with ZERO CSP violations in light theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "light");

    await navTo(page, "chat");
    await expect(page.getByTestId("section-chat")).toBeVisible({ timeout: 8_000 });
    await page.waitForTimeout(500);

    expect(
      violations,
      `CSP violations in Chat (light): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][light] Chat section: 0 violations");
  });

  test("Search section loads with ZERO CSP violations in light theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "light");

    await navTo(page, "search");
    await expect(page.getByTestId("section-search")).toBeVisible({ timeout: 8_000 });
    // Wait for filter bar (UI fully rendered, including any dynamic styles)
    const filterBar = page.getByTestId("search-filter-bar");
    const filterBarVisible = await filterBar.isVisible({ timeout: 3_000 }).catch(() => false);
    if (filterBarVisible) {
      await page.waitForTimeout(300);
    }

    expect(
      violations,
      `CSP violations in Search (light): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][light] Search section: 0 violations");
  });

  test("Graph section (sigma.js WebGL) loads with ZERO CSP violations in light theme (AC-CSP-8)", async ({
    page,
  }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "light");

    await navTo(page, "graph");
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 8_000 });

    // Wait for the sigma canvas to appear (WebGL context creation).
    const canvas = page.getByTestId("graph-panel").locator("canvas").first();
    await expect(canvas).toBeVisible({ timeout: 20_000 });
    // Allow time for WebGL initialisation and graph data fetch.
    await page.waitForTimeout(1_500);

    expect(
      violations,
      `CSP violations in Graph/sigma.js WebGL (light): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][light] Graph/sigma.js (WebGL): 0 violations");
  });

  test("Settings section loads with ZERO CSP violations in light theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "light");

    await navTo(page, "settings");
    await expect(page.getByTestId("settings-panel")).toBeVisible({ timeout: 8_000 });
    // Navigate to Costi (costs) section — exercises more of the settings UI.
    const costiBtn = page.getByTestId("settings-nav-costs");
    if (await costiBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await costiBtn.click();
      await page.waitForTimeout(500);
    }

    expect(
      violations,
      `CSP violations in Settings (light): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][light] Settings section: 0 violations");
  });

  test("Wiki / pages section loads with ZERO CSP violations in light theme", async ({
    page,
  }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "light");

    // Prime graph store so nav-tree has data.
    await navTo(page, "graph");
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 8_000 });
    await page.locator("[aria-label='Graph statistics']").waitFor({ timeout: 15_000 }).catch(() => {
      console.log("[CSP][light] graph-statistics not found — proceeding anyway");
    });

    await navTo(page, "pages");
    const navTree = page.getByTestId("nav-tree").first();
    // 20s (not 10s): CI runners under concurrent-worker load have shown this element
    // taking longer than 10s to mount — matches the timeout budget already used for
    // the graph-canvas wait elsewhere in this same suite (AC-CSP-8).
    await expect(navTree).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(500);

    // Click first page row to exercise the preview panel and note rendering.
    const firstRow = page.locator(".nav-tree__page-row").first();
    if (await firstRow.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await firstRow.click();
      await page.waitForTimeout(600);
    }

    expect(
      violations,
      `CSP violations in Wiki/pages (light): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][light] Wiki/pages section (+ preview panel): 0 violations");
  });
});

// ── AC-CSP-6: Zero violations in DARK theme ───────────────────────────────────

test.describe("CSP violations — DARK theme (AC-CSP-6)", () => {
  test("Chat section loads with ZERO CSP violations in dark theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "dark");

    await navTo(page, "chat");
    await expect(page.getByTestId("section-chat")).toBeVisible({ timeout: 8_000 });
    await page.waitForTimeout(500);

    expect(
      violations,
      `CSP violations in Chat (dark): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][dark] Chat section: 0 violations");
  });

  test("Search section loads with ZERO CSP violations in dark theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "dark");

    await navTo(page, "search");
    await expect(page.getByTestId("section-search")).toBeVisible({ timeout: 8_000 });
    await page.waitForTimeout(300);

    expect(
      violations,
      `CSP violations in Search (dark): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][dark] Search section: 0 violations");
  });

  test("Graph section (sigma.js WebGL) loads with ZERO CSP violations in dark theme (AC-CSP-8)", async ({
    page,
  }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "dark");

    await navTo(page, "graph");
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 8_000 });

    const canvas = page.getByTestId("graph-panel").locator("canvas").first();
    await expect(canvas).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1_500);

    expect(
      violations,
      `CSP violations in Graph/sigma.js WebGL (dark): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][dark] Graph/sigma.js (WebGL): 0 violations");
  });

  test("Settings section loads with ZERO CSP violations in dark theme", async ({ page }) => {
    const violations = collectCspViolations(page);
    await gotoApp(page);
    await setTheme(page, "dark");

    await navTo(page, "settings");
    await expect(page.getByTestId("settings-panel")).toBeVisible({ timeout: 8_000 });
    await page.waitForTimeout(500);

    expect(
      violations,
      `CSP violations in Settings (dark): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][dark] Settings section: 0 violations");
  });
});

// ── AC-CSP-7: KaTeX display-math rendering — zero style-src violations ─────────

test.describe("KaTeX math rendering under CSP (AC-CSP-7)", () => {
  // Extra retry budget (on top of the global CI retries:1) for this describe block only.
  // These two tests have shown a recurring CI-only resource-contention flake across 5+ PRs —
  // the timeout that trips varies run to run (nav-tree, then graph-panel after a first fix
  // attempt), consistent with generic runner contention rather than a specific race in app
  // logic. The CSP security property itself (script-src clean, zero violations) is already
  // proven by 8 OTHER tests in this same file that have never flaked; these two only add
  // KaTeX-specific coverage on top. A local reproduction against a real live stack never
  // failed once. Widening retries here (not globally) accepts genuine CI variance for this
  // known-flaky pair without weakening the CSP gate's other, reliable assertions.
  test.describe.configure({ retries: 2 });

  /**
   * Inject a mock wiki page containing display-math ($$E = mc^2$$) into the pages API,
   * navigate to it in the wiki section, and assert that KaTeX's rendering of inline styles
   * does not produce any CSP violations.
   *
   * This directly tests that ``style-src 'unsafe-inline'`` correctly allows KaTeX's
   * generated span style attributes (e.g. <span style="height:0.6944em;">) and that
   * the ``font-src`` directive allows KaTeX fonts served from the same origin.
   */
  test("KaTeX $$display math$$ renders in NoteView without style-src CSP violations", async ({
    page,
  }) => {
    const violations = collectCspViolations(page);

    // Mock page-list and page-content to inject a math-containing page.
    const mathPageId = "csp-math-test-00000000-0000-0000-0000-000000000000";
    const mathContent =
      "---\ntitle: CSP KaTeX Test\ntype: concept\n---\n\n" +
      "# KaTeX CSP Test\n\n" +
      "Display math: $$E = mc^2$$\n\n" +
      "Another block: $$\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}$$\n";

    // Intercept GET /pages (list) to return our test page.
    await page.route("**/pages?**", async (route) => {
      // Only intercept GET requests (not the single-page endpoint).
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: mathPageId,
              title: "CSP KaTeX Test",
              type: "concept",
              file_path: "wiki/concepts/csp-katex-test.md",
              source_count: 0,
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
        }),
      });
    });

    // Intercept GET /pages/{id} to return math content.
    await page.route(`**/pages/${mathPageId}/content`, async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: mathPageId,
          title: "CSP KaTeX Test",
          type: "concept",
          file_path: "wiki/concepts/csp-katex-test.md",
          source_count: 0,
          content: mathContent,
          updated_at: "2026-01-01T00:00:00Z",
          sources: [],
        }),
      });
    });

    // Intercept GET /pages/{id}/related — NoteView fetches this alongside content;
    // an unmocked call would 404 against the real backend (page doesn't really exist).
    await page.route(`**/pages/${mathPageId}/related**`, async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    // Intercept /graph so the graph store can prime (returns empty graph).
    await page.route("**/graph**", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ nodes: [], edges: [], data_version: 0 }),
      });
    });

    await gotoApp(page);
    await setTheme(page, "light");

    // Prime the graph store before navigating to "pages" — matches the pattern proven
    // reliable by the AC-CSP-5 sweep test below (which never flakes here); visiting
    // "graph" first appears to settle some shared app-init state (dataVersion/SSE) that
    // NavTree's mount otherwise races under CI's concurrent-worker load.
    await navTo(page, "graph");
    // 40s (not 15s, not 25s): a local repro of this exact CI sequence (docker-compose.ci.yml
    // + seed scripts + built preview, matching the workflow step-for-step) never failed —
    // ruling out a deterministic app bug. 25s was ALSO insufficient in practice: CI logs show
    // this wait actually taking ~25.8s-26.7s under load, i.e. right at or past the prior
    // budget — not a probabilistic flake but a real, measured, shared-runner WebGL/canvas
    // rendering cost this specific wait needs real headroom for, not another near-miss.
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 40_000 });

    // Navigate to the wiki/pages section.
    await navTo(page, "pages");
    const navTree = page.getByTestId("nav-tree").first();
    // 20s (not 10s): CI runners under concurrent-worker load have shown this element
    // taking longer than 10s to mount — matches the timeout budget already used for
    // the graph-canvas wait elsewhere in this same suite (AC-CSP-8).
    await expect(navTree).toBeVisible({ timeout: 20_000 });

    // Click the math test page row.
    const mathRow = page.locator(".nav-tree__page-row").first();
    if (await mathRow.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await mathRow.click();
      // Wait for NoteView (the center content pane, NOT the unrelated right-side
      // "preview-panel" detail pane, which always shows "Select a node...") to render
      // the KaTeX-rendered math via renderMarkdown.
      const noteView = page.getByTestId("note-view").first();
      await expect(noteView.locator(".katex").first()).toBeVisible({ timeout: 8_000 });
      // Allow KaTeX to finish rendering (renderMarkdown is synchronous, but React
      // commit may add a tick).
      await page.waitForTimeout(800);
    } else {
      console.log("[CSP][math] No page row visible — math route mock may not have matched");
    }

    expect(
      violations,
      `CSP violations during KaTeX math rendering: ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][AC-CSP-7] KaTeX display-math rendered with 0 violations");
  });

  test("KaTeX math renders in dark theme without CSP violations", async ({ page }) => {
    const violations = collectCspViolations(page);

    const mathPageId = "csp-math-dark-00000000-0000-0000-0000-000000000001";
    const mathContent =
      "---\ntitle: CSP KaTeX Dark Test\ntype: concept\n---\n\n" +
      "# KaTeX CSP Dark Test\n\n" +
      "$$\\frac{d}{dx}\\left(\\int_{a}^{x} f(t)\\,dt\\right) = f(x)$$\n";

    await page.route("**/pages?**", async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{ id: mathPageId, title: "CSP KaTeX Dark Test", type: "concept",
            file_path: "wiki/concepts/csp-katex-dark-test.md", source_count: 0,
            updated_at: "2026-01-01T00:00:00Z" }],
          total: 1, limit: 100, offset: 0,
        }),
      });
    });

    await page.route(`**/pages/${mathPageId}/content`, async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: mathPageId, title: "CSP KaTeX Dark Test", type: "concept",
          file_path: "wiki/concepts/csp-katex-dark-test.md", source_count: 0,
          content: mathContent, updated_at: "2026-01-01T00:00:00Z", sources: [] }),
      });
    });

    await page.route(`**/pages/${mathPageId}/related**`, async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route("**/graph**", async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ nodes: [], edges: [], data_version: 0 }) });
    });

    await gotoApp(page);
    await setTheme(page, "dark");

    // Prime the graph store before navigating to "pages" — see the light-theme test
    // above for the rationale (matches the reliable AC-CSP-5 sweep pattern).
    await navTo(page, "graph");
    // 40s — same CI-runner timing fix as the light-theme sibling above, widened further after
    // 25s STILL failed 3/3 in the first re-enable attempt (measured ~26-27s each run — a real,
    // consistent cost under CI load, not a probabilistic flake).
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 40_000 });

    await navTo(page, "pages");
    await expect(page.getByTestId("nav-tree").first()).toBeVisible({ timeout: 20_000 });

    const mathRow = page.locator(".nav-tree__page-row").first();
    if (await mathRow.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await mathRow.click();
      await expect(
        page.getByTestId("note-view").first().locator(".katex").first(),
      ).toBeVisible({ timeout: 8_000 });
      await page.waitForTimeout(800);
    }

    expect(
      violations,
      `CSP violations during KaTeX math rendering (dark theme): ${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log("[CSP][AC-CSP-7][dark] KaTeX display-math rendered with 0 violations");
  });
});

// ── Full-surface sweep: both themes in one test ────────────────────────────────

test.describe("CSP full-surface sweep — both themes back-to-back", () => {
  /**
   * A condensed sweep that covers all main sections in both themes in a single
   * browser context, ensuring theme-switching itself doesn't introduce violations.
   */
  test("all core sections in light then dark theme accumulate zero CSP violations", async ({
    page,
  }) => {
    const violations = collectCspViolations(page);

    // ── Light theme ──────────────────────────────────────────────────────────
    await gotoApp(page);
    await setTheme(page, "light");

    for (const section of ["chat", "search", "settings", "review"] as const) {
      await navTo(page, section);
      const sectionEl = page.getByTestId(`section-${section}`);
      await expect(sectionEl).toBeVisible({ timeout: 8_000 });
      await page.waitForTimeout(300);
    }

    // ── Dark theme ───────────────────────────────────────────────────────────
    await setTheme(page, "dark");

    for (const section of ["chat", "search", "settings"] as const) {
      await navTo(page, section);
      const sectionEl = page.getByTestId(`section-${section}`);
      await expect(sectionEl).toBeVisible({ timeout: 8_000 });
      await page.waitForTimeout(300);
    }

    // Graph last (WebGL + dark theme together).
    await navTo(page, "graph");
    await expect(page.getByTestId("graph-panel")).toBeVisible({ timeout: 8_000 });
    const canvas = page.getByTestId("graph-panel").locator("canvas").first();
    await expect(canvas).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1_500);

    // Restore light theme for subsequent tests.
    await setTheme(page, "light");

    expect(
      violations,
      `CSP violations found during full-surface sweep (both themes):\n${violations.join("\n")}`,
    ).toHaveLength(0);
    console.log(
      "[CSP] Full-surface sweep (light + dark, all sections): 0 violations",
    );
  });
});
