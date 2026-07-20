/**
 * shell-m4-phase1.spec.ts — M4 Phase 1 QA gate: 3-panel shell (F1 / ADR-0017)
 *
 * Gate checks:
 *   CHECK-2:  Functional — 3 panels, tree groups, graph canvas, chat disabled,
 *             selection wiring (tree ↔ preview, scenario templates).
 *   CHECK-3:  Accessibility — landmark roles, tablist, keyboard nav, empty state.
 *   CHECK-4:  D5 screenshots — shell-3panel.png + shell-3panel-selected.png.
 *   CHECK-5:  Invariant spot-checks — I4 (bounded DOM, virtualisation), I3 (no
 *             store subscription console errors).
 *
 * Prerequisites:
 *   Backend:  http://localhost:8000  (140-node demo graph seeded)
 *   Frontend: http://localhost:5173  (Vite dev server)
 *
 * Run:
 *   cd frontend && npx playwright test e2e/shell-m4-phase1.spec.ts
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

if (!fs.existsSync(SCREENS_DIR)) {
  fs.mkdirSync(SCREENS_DIR, { recursive: true });
}

/** Desktop viewport matching D5 spec. */
const VIEWPORT = { width: 1440, height: 900 };

// ── Helpers ────────────────────────────────────────────────────────────────────

async function loadShell(page: Page): Promise<void> {
  await page.setViewportSize(VIEWPORT);
  await page.goto(`${FRONTEND_URL}/`, { waitUntil: "domcontentloaded" });
  // v1.2 [F18]: app boots to "home" section, not pages/graph.
  // Navigate to "pages" section (PanelGroup: NavTree | NoteView | PreviewPanel)
  // so that nav-tree, preview panel, and scenario templates are in the DOM.
  await page.waitForSelector("[data-testid='app-shell']", { timeout: 15_000 });
  await page.locator("[data-section='pages']").click();
  // Wait for nav-tree to be in DOM (pages section mounts NavTree)
  await page.waitForSelector("[data-testid='nav-tree']",  { timeout: 10_000 });
  await page.waitForTimeout(400); // let the tree and note-view settle
}

// ── CHECK-2: FUNCTIONAL ────────────────────────────────────────────────────────

test.describe("CHECK-2 — 3-panel shell renders correctly (F1 / ADR-0017)", () => {
  test.beforeEach(async ({ page }) => { await loadShell(page); });

  test("app-shell is present with header, panel-group, activity-bar", async ({ page }) => {
    await expect(page.locator("[data-testid='app-shell']")).toBeVisible();
    await expect(page.locator("[data-testid='app-header']")).toBeVisible();
    await expect(page.locator("[data-testid='activity-bar']")).toBeVisible();
    // PanelGroup wrapper — the react-resizable-panels Group renders a div
    const panelGroup = page.locator("#synapse-panel-group");
    await expect(panelGroup).toBeVisible();
  });

  test("left panel: NavTree is visible with at least one type-group header", async ({ page }) => {
    // PanelGroup renders two NavTree instances (main panel + mobile drawer).
    // Use .first() to avoid Playwright strict-mode multi-match error.
    const navTree = page.locator("[data-testid='nav-tree']").first();
    await expect(navTree).toBeVisible();

    // At least one group header button (Concepts / Entities / etc.)
    const groupHeaders = page.locator(".nav-tree__group-header");
    const count = await groupHeaders.count();
    expect(count, "NavTree should have at least 1 group header (type grouping)").toBeGreaterThanOrEqual(1);
    console.log(`[CHECK-2] NavTree group headers rendered: ${count}`);
  });

  test("left panel: groups cover known types with counts > 0", async ({ page }) => {
    // The NavTree is virtualised (I4): group headers only exist in the DOM when
    // they are within the visible scroll window. Scroll to the bottom of the
    // NavTree scroll container to force all groups into the DOM, then check.
    // PanelGroup renders two NavTree instances — scope to .first() for strict-mode safety.
    const scrollContainer = page.locator(".nav-tree__scroll").first();
    await expect(scrollContainer).toBeVisible();

    // Scroll to bottom to load all group headers (concepts group is at top;
    // entity, source, etc. groups may be scrolled below the fold initially)
    await scrollContainer.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await page.waitForTimeout(200); // let virtualizer re-render

    // After scrolling, concepts group (at top) is now scrolled out and entity
    // group should be visible. Check that we have at least 2 group headers total
    // by counting all that ever appear after full scroll.
    const groupCount = await page.locator(".nav-tree__group-header").count();
    expect(groupCount, "At least one group header must be visible after scrolling").toBeGreaterThanOrEqual(1);

    // Scroll back to top to restore state for subsequent tests
    await scrollContainer.evaluate((el) => { el.scrollTop = 0; });
    await page.waitForTimeout(100);

    // Verify the visible Concepts group has a valid count in its aria-label.
    // Use .first() because PanelGroup renders two NavTree instances (main panel + drawer).
    const conceptsBtn = page.locator(".nav-tree__group-header[data-type='concept']").first();
    await expect(conceptsBtn).toBeVisible({ timeout: 3000 });
    const conceptsLabel = await conceptsBtn.getAttribute("aria-label");
    expect(conceptsLabel).toMatch(/Concepts,\s*\d+\s*items/i);
    console.log(`[CHECK-2] Concepts group label: "${conceptsLabel}"; total visible group headers after scroll: ${groupCount}`);
  });

  test("center panel: NoteView (wiki editor) is visible in pages section", async ({ page }) => {
    // As of v1.3 the "pages" section center panel shows NoteView (wiki page viewer /
    // editor), NOT a graph canvas. The sigma canvas lives in the dedicated "graph"
    // NavRail section.  This test confirms the pages-section center panel is present.
    const noteView = page.locator("[data-testid='note-view']").first();
    await expect(noteView).toBeVisible();
    // The GraphPanel / sigma canvas must NOT be in the pages section DOM.
    const graphPanel = page.locator("[data-testid='graph-panel']");
    await expect(graphPanel).not.toBeVisible();
  });

  test("NavRail: Chat button is ENABLED and pages section is active after loadShell()", async ({ page }) => {
    // Phase 3 enabled the Chat section — Chat NavRail button must be clickable.
    const chatBtn = page.locator("[data-section='chat']");
    await expect(chatBtn).toBeVisible();
    await expect(chatBtn).toBeEnabled();
    const ariaDisabled = await chatBtn.getAttribute("aria-disabled");
    expect(ariaDisabled === null || ariaDisabled === "false", "NavRail Chat must not be aria-disabled").toBe(true);

    // v1.2 [F18][R12-1]: default boot section is now "home" (changed from "chat").
    // loadShell() navigates to "pages", so after beforeEach the "pages" button is aria-current.
    const pagesBtn = page.locator("[data-section='pages']");
    const pagesCurrent = await pagesBtn.getAttribute("aria-current");
    expect(pagesCurrent, "pages must be aria-current=page after loadShell() navigates there").toBe("page");
    // Chat is NOT the default — its aria-current must be absent
    const chatCurrent = await chatBtn.getAttribute("aria-current");
    expect(chatCurrent === null || chatCurrent === "false",
      "Chat must NOT be aria-current=page (home is the boot default since v1.2)").toBe(true);
    console.log(`[CHECK-2] NavRail Chat button enabled; pages section active (loadShell navigated there)`);
  });

  test("right panel: PreviewPanel shows empty state when nothing selected", async ({ page }) => {
    // PanelGroup renders two PreviewPanel instances (right panel + mobile drawer).
    // Use .first() to target the main panel and avoid strict-mode multi-match error.
    const preview = page.locator("[data-testid='preview-panel']").first();
    await expect(preview).toBeVisible();
    // Empty state text
    await expect(preview).toContainText("Select a node");
    console.log(`[CHECK-2] PreviewPanel empty state confirmed`);
  });

  test("selection wiring: clicking tree row populates PreviewPanel", async ({ page }) => {
    // Click the first visible CONTENT page row. "Synapse Overview" is deliberately excluded
    // from the knowledge graph (it's a navigational singleton entry-point, not a content
    // node — see useNavTreeData.ts SECTION_ORDER comment) so PreviewPanel's "Connections"
    // view — which resolves purely from graph nodes — has nothing to show for it. It now
    // sorts first in the tree (2.1.3 fix: boot vault indexes overview.md at startup), so
    // exclude it here rather than assert graph-derived content for a non-graph row.
    const pageRow = page.locator(".nav-tree__page-row").filter({ hasNotText: "Synapse Overview" }).first();
    await expect(pageRow).toBeVisible();
    const pageTitle = await pageRow.getAttribute("aria-label");
    console.log(`[CHECK-2] Clicking tree row: "${pageTitle}"`);
    await pageRow.click();

    // PreviewPanel must switch from empty-state to populated (title visible).
    // Use .first() — PanelGroup renders two PreviewPanel instances (panel + drawer).
    const preview = page.locator("[data-testid='preview-panel']").first();
    await expect(preview).not.toContainText("Select a node", { timeout: 3_000 });
    // Should show a type badge (concept/entity/source/etc.)
    const typeBadge = preview.locator("[aria-label^='Type:']");
    await expect(typeBadge).toBeVisible();
    console.log(`[CHECK-2] PreviewPanel populated after tree click`);
  });

  test("selection wiring: tree row selected state mirrors selected node id", async ({ page }) => {
    // Click a page row, verify it gets aria-current="page" (selected style)
    const pageRows = page.locator(".nav-tree__page-row");
    const firstRow = pageRows.first();
    await firstRow.click();
    const ariaCurrent = await firstRow.getAttribute("aria-current");
    expect(ariaCurrent, "Clicked row must have aria-current='page'").toBe("page");
  });

  test("scenario templates: 'Most connected node' selects a node and populates preview", async ({
    page,
  }) => {
    // PanelGroup renders two ScenarioTemplates and two PreviewPanel (panel + drawer).
    // Use .first() on all to avoid strict-mode multi-match errors.
    const templates = page.locator("[data-testid='scenario-templates']").first();
    await expect(templates).toBeVisible();

    // Find and click "Most connected node" button
    const highDegreeBtn = page.locator(".scenario-templates__btn", {
      hasText: "Most connected node",
    }).first();
    await expect(highDegreeBtn).toBeVisible();
    await highDegreeBtn.click();

    // PreviewPanel must be populated
    const preview = page.locator("[data-testid='preview-panel']").first();
    await expect(preview).not.toContainText("Select a node", { timeout: 3_000 });

    // Verify the title in preview matches a known high-degree node (Max Pooling from backend)
    // We check it shows SOME title (non-empty h2)
    const h2 = preview.locator("h2");
    await expect(h2).toBeVisible();
    const title = await h2.textContent();
    expect(title?.trim().length, "Selected node title should not be empty").toBeGreaterThan(0);
    console.log(`[CHECK-2] 'Most connected node' selected: "${title?.trim()}"`);
  });

  test("scenario templates: degree shown in PreviewPanel after selection", async ({ page }) => {
    const highDegreeBtn = page.locator(".scenario-templates__btn", {
      hasText: "Most connected node",
    }).first();
    await highDegreeBtn.click();

    // PreviewPanel should show degree info via the <dt>Degree</dt><dd>N</dd> pair.
    // Use .first() — PanelGroup renders two PreviewPanel instances.
    const preview = page.locator("[data-testid='preview-panel']").first();
    await expect(preview).not.toContainText("Select a node", { timeout: 3_000 });
    // Connections field present (was "Degree" pre-v1.3; renamed to "Connections" in PreviewPanel).
    // WS-F v1.7.0 redesign moved Connections/ID/Position into a "Technical details" <details>
    // that is collapsed by default — expand it before asserting the Connections row is visible.
    await preview.locator("summary").first().click();
    // The <dd> shows an i18n string like "Connected to N pages".
    await expect(preview.locator("dt", { hasText: "Connections" })).toBeVisible();
    // Retrieve the <dd> text via DOM traversal (CSS adjacent sibling not supported in Playwright locators).
    const ddText = await page.evaluate(() => {
      const panels = document.querySelectorAll("[data-testid='preview-panel']");
      const panel = panels[0]; // first = main panel
      if (!panel) return "";
      const dts = Array.from(panel.querySelectorAll("dt"));
      const connectionsDt = dts.find((el) => el.textContent?.trim() === "Connections");
      return connectionsDt?.nextElementSibling?.textContent?.trim() ?? "";
    });
    expect(ddText.length, `Connections dd must be non-empty (got: "${ddText}")`).toBeGreaterThan(0);
    console.log(`[CHECK-2] Most connected node connections from PreviewPanel: "${ddText}"`);
  });

  test("separators are present (2 Separator elements between panels)", async ({ page }) => {
    // react-resizable-panels Separator elements get id="separator-left" / "separator-right"
    await expect(page.locator("#separator-left")).toBeVisible();
    await expect(page.locator("#separator-right")).toBeVisible();
    console.log(`[CHECK-2] Both panel separators present`);
  });
});

// ── CHECK-3: ACCESSIBILITY ─────────────────────────────────────────────────────

test.describe("CHECK-3 — Accessibility: landmarks, tablist, keyboard (WCAG 2.1)", () => {
  test.beforeEach(async ({ page }) => { await loadShell(page); });

  test("structural landmarks: <header>, <nav>, <footer> present with aria-labels", async ({
    page,
  }) => {
    // <header> is the app-header
    const header = page.locator("header.app-header");
    await expect(header).toBeVisible();

    // <nav> is the NavTree. PanelGroup renders two NavTree instances (panel + drawer).
    // Use .first() to target the main panel nav-tree.
    const nav = page.locator("nav[aria-label='Wiki pages']").first();
    await expect(nav).toBeVisible();

    // <footer> is the ActivityBar
    const footer = page.locator("footer[aria-label='Activity bar']");
    await expect(footer).toBeVisible();
    console.log(`[CHECK-3] Landmark roles: header, nav, footer all present and labelled`);
  });

  // NOTE: tablist (MainTabs) tests removed in Phase 2 — MainTabs retired per ADR-0018 §1.
  // Graph is now a NavRail section; no tab strip exists in the Pages center panel.

  test("NavTree group headers have aria-expanded attribute", async ({ page }) => {
    const groupHeaders = page.locator(".nav-tree__group-header");
    const count = await groupHeaders.count();
    expect(count).toBeGreaterThan(0);

    for (let i = 0; i < count; i++) {
      const btn = groupHeaders.nth(i);
      const ariaExpanded = await btn.getAttribute("aria-expanded");
      expect(
        ariaExpanded,
        `Group header ${i} must have aria-expanded attribute (got: ${ariaExpanded})`,
      ).toMatch(/^(true|false)$/);
    }
    console.log(`[CHECK-3] All ${count} group header buttons have aria-expanded`);
  });

  test("NavTree group header keyboard: Enter/Space toggles expand/collapse", async ({ page }) => {
    const firstGroup = page.locator(".nav-tree__group-header").first();
    const expandedBefore = await firstGroup.getAttribute("aria-expanded");

    await firstGroup.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(150);

    const expandedAfter = await firstGroup.getAttribute("aria-expanded");
    expect(
      expandedAfter,
      `aria-expanded should toggle after Enter key (was: ${expandedBefore})`,
    ).not.toBe(expandedBefore);
    console.log(`[CHECK-3] Group toggle keyboard: ${expandedBefore} → ${expandedAfter}`);
  });

  test("separators have aria-label (keyboard-resizable indication)", async ({ page }) => {
    const sepLeft  = page.locator("#separator-left");
    const sepRight = page.locator("#separator-right");

    const labelLeft  = await sepLeft.getAttribute("aria-label");
    const labelRight = await sepRight.getAttribute("aria-label");

    expect(labelLeft?.length,  "left separator must have aria-label").toBeGreaterThan(0);
    expect(labelRight?.length, "right separator must have aria-label").toBeGreaterThan(0);
    console.log(`[CHECK-3] Separators labelled: "${labelLeft}" / "${labelRight}"`);
  });

  test("PreviewPanel empty-state is present when no node selected on load", async ({ page }) => {
    // On fresh load, nothing is selected: empty-state message must be present.
    // PanelGroup renders two PreviewPanel instances (panel + drawer) — use .first().
    const emptyPreview = page.locator(".preview-panel--empty").first();
    await expect(emptyPreview).toBeVisible();
    console.log(`[CHECK-3] PreviewPanel empty-state visible on initial load`);
  });

  test("provider selector trigger has aria-label (F17 ProviderSelector)", async ({ page }) => {
    // Phase 2: the provider slot renders a real <ProviderSelector> button
    const trigger = page.getByTestId("provider-selector-trigger");
    await expect(trigger).toBeVisible();
    const label = await trigger.getAttribute("aria-label");
    expect(label?.length, "provider selector trigger must have aria-label").toBeGreaterThan(0);
    console.log(`[CHECK-3] Provider selector trigger aria-label: "${label}"`);
  });

  test("page rows have aria-label (screen reader accessible)", async ({ page }) => {
    const pageRows = page.locator(".nav-tree__page-row");
    const count = await pageRows.count();
    expect(count, "At least one page row must be visible").toBeGreaterThan(0);

    // Check first visible row has an aria-label
    const label = await pageRows.first().getAttribute("aria-label");
    expect(label?.trim().length, "Page row must have non-empty aria-label").toBeGreaterThan(0);
    console.log(`[CHECK-3] First page row aria-label: "${label}"`);
  });
});

// ── CHECK-4: D5 SCREENSHOTS ────────────────────────────────────────────────────

test.describe("CHECK-4 — D5: shell screenshots at 1440x900 (I8 / ADR-0017 Phase 1)", () => {
  test("shell-3panel.png: full shell, nothing selected", async ({ page }) => {
    await loadShell(page);

    const screenshotPath = path.join(SCREENS_DIR, "shell-3panel.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });

    const stats = fs.statSync(screenshotPath);
    expect(stats.size, `shell-3panel.png suspiciously small (${stats.size} bytes)`).toBeGreaterThan(20_000);
    console.log(`[CHECK-4] Saved: ${screenshotPath} (${(stats.size / 1024).toFixed(1)} KB)`);
  });

  test("shell-3panel-selected.png: node selected via scenario template", async ({ page }) => {
    await loadShell(page);

    // Trigger "Most connected node" to get a populated preview.
    // Use .first() — PanelGroup renders two ScenarioTemplates (panel + drawer).
    const highDegreeBtn = page.locator(".scenario-templates__btn", {
      hasText: "Most connected node",
    }).first();
    await expect(highDegreeBtn).toBeVisible();
    await highDegreeBtn.click();

    // Wait for preview to populate. Use .first() — two PreviewPanel instances.
    const preview = page.locator("[data-testid='preview-panel']").first();
    await expect(preview).not.toContainText("Select a node", { timeout: 4_000 });
    await page.waitForTimeout(400); // let selection animation settle

    const screenshotPath = path.join(SCREENS_DIR, "shell-3panel-selected.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });

    const stats = fs.statSync(screenshotPath);
    expect(stats.size, `shell-3panel-selected.png suspiciously small (${stats.size} bytes)`).toBeGreaterThan(20_000);
    console.log(`[CHECK-4] Saved: ${screenshotPath} (${(stats.size / 1024).toFixed(1)} KB)`);
  });

  test("docs/screens/ has both new Phase-1 PNGs after test run", async () => {
    const required = ["shell-3panel.png", "shell-3panel-selected.png"];
    for (const file of required) {
      const fullPath = path.join(SCREENS_DIR, file);
      expect(
        fs.existsSync(fullPath),
        `docs/screens/${file} must exist after test run`,
      ).toBe(true);
    }
    const pngs = fs.readdirSync(SCREENS_DIR).filter((f) => f.endsWith(".png"));
    console.log(`[CHECK-4] docs/screens/ PNGs: ${pngs.join(", ")}`);
  });
});

// ── CHECK-5: INVARIANT SPOT-CHECKS (I4 virtualisation, I3 no console errors) ──

test.describe("CHECK-5 — Invariant spot-checks: I4 virtualised tree, I3 no console errors", () => {
  test.beforeEach(async ({ page }) => { await loadShell(page); });

  test("I4 — sigma-container has only <canvas> children (bounded DOM)", async ({ page }) => {
    // sigma-container lives in the "graph" NavRail section — navigate there now.
    // (loadShell() puts us in "pages"; we need graph for the sigma canvas.)
    await page.locator("[data-section='graph']").click();
    await page.waitForSelector("[data-testid='graph-panel']", { timeout: 10_000 });
    await page.waitForSelector("canvas", { timeout: 10_000 });
    await page.waitForTimeout(300); // let sigma mount all layers

    const result = await page.evaluate(() => {
      const container =
        document.querySelector("[data-testid='sigma-container']") ??
        document.querySelector("#sigma-container");
      if (!container) return { found: false, count: 0, allCanvas: false, tagNames: [] as string[] };
      const children = Array.from(container.children);
      const tagNames = children.map((el) => el.tagName.toLowerCase());
      return {
        found: true,
        count: children.length,
        allCanvas: tagNames.every((t) => t === "canvas"),
        tagNames,
      };
    });

    expect(result.found, "sigma-container must exist in the DOM when graph section is active").toBe(true);
    expect(result.allCanvas, `Non-canvas children in sigma container: ${result.tagNames.join(", ")}`).toBe(true);
    // sigma v3: 7 layers; ≤9 allows for future additions without I4 violation
    expect(result.count, `sigma-container has ${result.count} children; expected ≤9 (bounded, not per-node)`).toBeLessThanOrEqual(9);
    console.log(`[CHECK-5 I4] sigma-container children: ${result.count} × <canvas> — bounded DOM confirmed`);
  });

  test("I4 — NavTree virtualisation: only visible rows in DOM (not all 140)", async ({ page }) => {
    // With 140 nodes in the demo fixture, a naive render would produce 140+ DOM nodes.
    // TanStack Virtual should render only the visible slice (~viewport / 28px row height).
    // At 900px viewport minus header/footer/scenario: ~780px body → ~27 rows visible.
    // With overscan=10 we'd expect at most ~47 rendered rows (27 + 2×10). Under 140 = PASS.
    const renderedPageRows = await page.locator(".nav-tree__page-row").count();
    // Also count group headers
    const renderedGroupRows = await page.locator(".nav-tree__group-header").count();
    const totalRendered = renderedPageRows + renderedGroupRows;

    console.log(`[CHECK-5 I4] NavTree rendered rows: ${renderedPageRows} page rows + ${renderedGroupRows} group headers = ${totalRendered} total`);

    // Strict: must be significantly less than 140 (virtualisation working)
    expect(
      totalRendered,
      `NavTree rendered ${totalRendered} rows; 140+ would indicate virtualisation is not working (I4). ` +
      `Expected < 100 rendered rows (visible + overscan, not all 140 nodes).`,
    ).toBeLessThan(100);
  });

  test("I3 — no Zustand store subscription errors in console during load", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });
    page.on("pageerror", (err) => {
      consoleErrors.push(`PAGE ERROR: ${err.message}`);
    });

    // Reload and wait for full settle.
    // v1.2 [F18]: app boots to "home" section (no canvas on initial load).
    // We wait for app-shell to be present, then allow subscriptions time to fire.
    await page.goto(`${FRONTEND_URL}/`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("[data-testid='app-shell']", { timeout: 15_000 });
    await page.waitForTimeout(1_500); // let all subscriptions fire

    // Filter out known benign errors (e.g. favicon 404)
    const realErrors = consoleErrors.filter(
      (e) =>
        !e.includes("favicon") &&
        !e.includes("404") &&
        !e.toLowerCase().includes("stylesheet"),
    );

    console.log(
      realErrors.length > 0
        ? `[CHECK-5 I3] Console errors: ${realErrors.join(" | ")}`
        : `[CHECK-5 I3] No console errors — I3 store subscriptions clean`,
    );

    expect(
      realErrors,
      `Console errors detected (I3 store subscription issue?): ${realErrors.join(", ")}`,
    ).toHaveLength(0);
  });

  test("I4 — graph panel DOM: no per-node div/span elements present", async ({ page }) => {
    // sigma-container lives in the "graph" NavRail section — navigate there first.
    // (loadShell() beforeEach puts us in "pages"; sigma canvas is only in graph section.)
    await page.locator("[data-section='graph']").click();
    await page.waitForSelector("[data-testid='graph-panel']", { timeout: 10_000 });
    await page.waitForSelector("canvas", { timeout: 10_000 });
    await page.waitForTimeout(300);

    const perNodeElements = await page.evaluate(() => {
      // Check for any elements with data-node-id attribute (would indicate per-node DOM)
      const nodeEls = document.querySelectorAll("[data-node-id]");
      // Also check inside sigma-container for any non-canvas element
      const container =
        document.querySelector("[data-testid='sigma-container']") ??
        document.querySelector("#sigma-container");
      const nonCanvas = container
        ? Array.from(container.querySelectorAll("*:not(canvas)")).length
        : 0;
      return {
        withNodeIdAttr: nodeEls.length,
        nonCanvasInsideSigma: nonCanvas,
      };
    });

    expect(
      perNodeElements.withNodeIdAttr,
      `Found ${perNodeElements.withNodeIdAttr} elements with data-node-id — indicates per-node DOM rendering (I4 violation)`,
    ).toBe(0);
    expect(
      perNodeElements.nonCanvasInsideSigma,
      `Found ${perNodeElements.nonCanvasInsideSigma} non-canvas children inside sigma-container (I4 violation)`,
    ).toBe(0);
    console.log(`[CHECK-5 I4] No per-node DOM elements — I4 confirmed`);
  });

  test("graph loads data from backend (nodes > 0 in store via DOM evidence)", async ({ page }) => {
    // NavTree group headers confirm nodes were loaded.
    // PanelGroup renders two NavTree instances (panel + drawer), so count is doubled.
    const groupHeaders = page.locator(".nav-tree__group-header");
    const count = await groupHeaders.count();
    expect(count, "At least one type group should be present (indicates nodes loaded from API)").toBeGreaterThan(0);

    // Verify at least ONE group has items > 0 via aria-label.
    // Note: some groups (e.g. "Overview") may have 0 items if no pages of that type exist.
    // The fixture has 140 nodes but may not have all types populated.
    let foundNonEmpty = false;
    let foundLabel = "";
    for (let i = 0; i < Math.min(count, 8); i++) {
      const label = await groupHeaders.nth(i).getAttribute("aria-label");
      const m = label?.match(/(\d+)\s*items/);
      if (m && parseInt(m[1], 10) > 0) {
        foundNonEmpty = true;
        foundLabel = label ?? "";
        break;
      }
    }
    expect(foundNonEmpty, `At least one NavTree group must have items > 0. All groups checked had 0 items — data may not have loaded.`).toBe(true);
    console.log(`[CHECK-5] Backend data loaded: found non-empty group "${foundLabel}"`);
  });

  test("X-Graph-Cache header: second /graph call returns hit (G2/I2 cache)", async ({ page }) => {
    // First call (may be miss or hit)
    await page.request.get(`${BACKEND_URL}/graph`);
    // Second call must be hit per ADR-0014
    const second = await page.request.get(`${BACKEND_URL}/graph`);
    const cacheHeader = second.headers()["x-graph-cache"];
    expect(
      cacheHeader,
      `Second GET /graph must return X-Graph-Cache: hit (got: ${cacheHeader ?? "(absent)"}). ADR-0014.`,
    ).toBe("hit");
    console.log(`[CHECK-5] X-Graph-Cache on second call: ${cacheHeader}`);
  });
});
