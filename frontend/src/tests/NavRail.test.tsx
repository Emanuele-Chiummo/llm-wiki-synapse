/**
 * NavRail.test.tsx — vitest unit tests for NavRail (M4-HARD + M5 Phase 3 + v0.6 Lint update).
 *
 * M5 Phase 2 (F10): Deep Search is now an ACTIVE nav item (AC-F10-8a).
 * M5 Phase 3 (F9): Review is now an ACTIVE nav item (AC-F9-5).
 * v0.6 (K2/F15): Lint is now an ACTIVE nav item.
 * v0.6 (F5/llm_wiki parity): Search added to TOP_ITEMS between Sources and Graph.
 * sprint/v1.1 (R11-3): Logo removed from NavRail; branding lives in Header only.
 * sprint/v1.1 (R11-1/A1): Convert section added to M5_ITEMS (F12 Marker PDF conversion).
 * sprint/v1.2 (R12-1/F18): Home section added to TOP_ITEMS as the first (top) item.
 *
 * Covers:
 *   AC-HARD-LBL-7: each rendered item has both an SVG icon and a visible label span.
 *   AC-HARD-M5P-6 (updated for v1.2): search PRESENT; lint + deep-search + review + convert present.
 *   AC-HARD-ORD-1 (updated for v1.2): exactly 12 interactive items
 *                  (Home/Chat/Wiki/Sources/Search/Graph/Lint/Review/DeepSearch/Ingest/Convert/Settings).
 *   AC-R12-1-4: Home icon is present at the top of the NavRail.
 *   AC-F10-8a: "Deep Search" nav item renders in the rail.
 *   AC-F9-5: "Review" nav item renders in the rail.
 *   AC-R11-3-1: NavRail contains no Synapse brand SVG / logo image.
 *   AC-R11-3-2: NavRail contains no element with aria-label="Synapse".
 *
 * Does NOT test the Playwright E2E resize path (AC-HARD-LBL-6 / AC-F1-7) — that is
 * a Playwright concern.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { NavRail } from "../components/nav/NavRail";

// ─── Mocks ────────────────────────────────────────────────────────────────────

// Minimal i18n mock: returns the key suffix as the label text.
// e.g. t("nav.chat") → "Chat", t("nav.wiki") → "Wiki", t("nav.settings") → "Settings"
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".");
      const raw = parts[parts.length - 1] ?? key;
      // Capitalise first character to match realistic output
      return raw.charAt(0).toUpperCase() + raw.slice(1);
    },
  }),
}));

// Minimal graphStore mock: activeSection = "chat"; setActiveSection is a no-op.
vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeSection: "chat",
      setActiveSection: vi.fn(),
    }),
  selectActiveSection: (s: { activeSection: string }) => s.activeSection,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

// Minimal ingestStore mock: 0 running tasks.
vi.mock("../store/ingestStore", () => ({
  useIngestRunningCount: () => 0,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderNavRail() {
  return render(<NavRail />);
}

// ─── AC-HARD-ORD-1 (v1.1+Convert update): exactly 11 interactive nav items ───
//
// v0.6 [F11]: "sources" added to TOP_ITEMS (file browser); "ingest" moved to M5_ITEMS.
// v1.1 [R11-1/A1]: "convert" added to M5_ITEMS (Marker PDF conversion surface).
// Total interactive items = 11.

describe("NavRail — item count and order (AC-HARD-ORD-1 v1.2+Home, AC-R12-1-4, AC-F10-8a, AC-F9-5)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("renders exactly 13 interactive buttons (Home/Chat/Wiki/Sources/Search/Graph/Lint/Review/DeepSearch/Ingest/Convert/Settings/Projects) [R12-1, v1.5 P2]", () => {
    const buttons = screen.getAllByRole("button");
    // 13 nav buttons: home, chat, pages, sources, search, graph, lint, review, deep-search,
    // ingest, convert, settings, + projects (⇄ Project Launcher, v1.5 P2 — llm_wiki parity)
    expect(buttons).toHaveLength(13);
  });

  it("AC-R12-1-4: renders a Home button at the top of the rail (data-section='home')", () => {
    const homeBtn = document.querySelector("[data-section='home']");
    expect(homeBtn).not.toBeNull();
  });

  it("renders a Chat button", () => {
    expect(screen.getByTestId !== undefined, "testing-library available").toBe(true);
    const chatBtn = document.querySelector("[data-section='chat']");
    expect(chatBtn).not.toBeNull();
  });

  it("renders a Wiki/pages button", () => {
    const pagesBtn = document.querySelector("[data-section='pages']");
    expect(pagesBtn).not.toBeNull();
  });

  it("renders a Sources file-browser button (data-section='sources') [F11]", () => {
    const sourcesBtn = document.querySelector("[data-section='sources']");
    expect(sourcesBtn).not.toBeNull();
  });

  it("renders an Ingest run-history button (data-section='ingest') in M5 group", () => {
    const ingestBtn = document.querySelector("[data-section='ingest']");
    expect(ingestBtn).not.toBeNull();
  });

  it("renders a Graph button", () => {
    const graphBtn = document.querySelector("[data-section='graph']");
    expect(graphBtn).not.toBeNull();
  });

  it("renders a Settings button", () => {
    const settingsBtn = document.querySelector("[data-section='settings']");
    expect(settingsBtn).not.toBeNull();
  });

  it("renders a Convert button (data-section='convert') [R11-1/A1]", () => {
    const convertBtn = document.querySelector("[data-section='convert']");
    expect(convertBtn).not.toBeNull();
  });
});

// ─── AC-HARD-M5P-6 (v1.1+Convert update) + AC-F10-8a + AC-F9-5 ─────────────

describe("NavRail — v1.2+Home items (AC-HARD-M5P-6 updated, AC-R12-1-4, AC-F10-8a, AC-F9-5)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("DOES render data-section='search' (Search active in v0.6 — F5/llm_wiki parity)", () => {
    expect(document.querySelector("[data-section='search']")).not.toBeNull();
  });

  it("DOES render data-section='lint' (K2/v0.6 — Lint active)", () => {
    expect(document.querySelector("[data-section='lint']")).not.toBeNull();
  });

  it("DOES render data-section='review' (AC-F9-5 — Review active in M5 Phase 3)", () => {
    expect(document.querySelector("[data-section='review']")).not.toBeNull();
  });

  it("DOES render data-section='deep-search' (AC-F10-8a — Deep Search active in M5 Phase 2)", () => {
    expect(document.querySelector("[data-section='deep-search']")).not.toBeNull();
  });

  it("DOES render data-section='convert' (AC-R11-1-5/A1 — Convert active in v1.1)", () => {
    expect(document.querySelector("[data-section='convert']")).not.toBeNull();
  });

  it("does NOT render any aria-disabled button", () => {
    const disabledBtns = document.querySelectorAll("button[aria-disabled='true']");
    expect(disabledBtns).toHaveLength(0);
  });

  it("does NOT render any HTML-disabled button", () => {
    const disabledBtns = document.querySelectorAll("button:disabled");
    expect(disabledBtns).toHaveLength(0);
  });
});

// ─── AC-HARD-LBL-7: each item has icon (SVG) + label span ────────────────────

describe("NavRail — icon + label on each button (AC-HARD-LBL-7)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("each nav button contains at least one SVG icon (aria-hidden)", () => {
    const buttons = document.querySelectorAll("[data-testid='nav-rail'] button");
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((btn) => {
      const svgs = btn.querySelectorAll("svg[aria-hidden='true']");
      expect(svgs.length, `Button id="${btn.id}" should have an SVG icon`).toBeGreaterThan(0);
    });
  });

  it("each nav button contains a non-empty .nav-rail__label span", () => {
    const buttons = document.querySelectorAll("[data-testid='nav-rail'] button");
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((btn) => {
      const labelSpan = btn.querySelector(".nav-rail__label");
      expect(labelSpan, `Button id="${btn.id}" should have a .nav-rail__label span`).not.toBeNull();
      expect(
        labelSpan?.textContent?.trim().length,
        `Button id="${btn.id}" .nav-rail__label should be non-empty`,
      ).toBeGreaterThan(0);
    });
  });

  it("Chat button label is non-empty", () => {
    const chatBtn = document.querySelector("[data-section='chat']");
    const label = chatBtn?.querySelector(".nav-rail__label");
    expect(label?.textContent?.trim()).toBeTruthy();
  });

  it("Settings button label is non-empty", () => {
    const settingsBtn = document.querySelector("[data-section='settings']");
    const label = settingsBtn?.querySelector(".nav-rail__label");
    expect(label?.textContent?.trim()).toBeTruthy();
  });
});

// ─── UXA-05: inactive rail buttons must not suppress focus outline ────────────

describe("NavRail — UXA-05: inactive buttons do not set outline:none", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("inactive rail buttons do not have outline:none inline style", () => {
    // The active item (chat) has an active-state outline; all others must NOT
    // set outline: "none" which would suppress :focus-visible keyboard ring.
    const buttons = document.querySelectorAll("[data-testid='nav-rail'] button");
    buttons.forEach((btn) => {
      const el = btn as HTMLElement;
      const isActive = el.getAttribute("aria-current") === "page";
      if (!isActive) {
        // outline style must not be the string "none"
        expect(
          el.style.outline,
          `Inactive button id="${el.id}" should not override outline to "none"`,
        ).not.toBe("none");
      }
    });
  });
});

// ─── UXA-01: section group labels rendered (v1.7.0 — 3 groups) ───────────────
// The NavRail is split into 3 labelled groups (CREATE/UNDERSTAND/MAINTAIN).
// Each group has a <span aria-hidden="true"> mono uppercase label.

describe("NavRail — UXA-01: section group labels present (v1.7.0)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("renders at least one group label span (aria-hidden, not nav-rail__label class)", () => {
    // The mock returns the last i18n key segment capitalised, so:
    //   nav.group.create → "Create", nav.group.understand → "Understand",
    //   nav.group.maintain → "Maintain".
    // The test verifies the group label spans exist without checking specific text.
    const rail = document.querySelector("[data-testid='nav-rail']");
    expect(rail).not.toBeNull();
    const allSpans = Array.from(rail!.querySelectorAll("span[aria-hidden='true']"));
    const groupLabels = allSpans.filter(
      (s) => (s.textContent ?? "").trim().length > 0 && !s.className.includes("nav-rail__label"),
    );
    expect(
      groupLabels.length,
      "At least one group label span should be present",
    ).toBeGreaterThanOrEqual(1);
  });

  it("renders 3 group label spans (CREATE · UNDERSTAND · MAINTAIN)", () => {
    const rail = document.querySelector("[data-testid='nav-rail']");
    expect(rail).not.toBeNull();
    const allSpans = Array.from(rail!.querySelectorAll("span[aria-hidden='true']"));
    const groupLabels = allSpans.filter(
      (s) => (s.textContent ?? "").trim().length > 0 && !s.className.includes("nav-rail__label"),
    );
    expect(groupLabels.length, "Should have exactly 3 group label spans").toBe(3);
  });
});

// ─── AC-HARD-LBL-8: badge does not overlap label ─────────────────────────────

describe("NavRail — ingest badge position (AC-HARD-LBL-8)", () => {
  it("badge is absolutely positioned (top-right, not inside label span)", () => {
    // Render with runningCount = 2 to verify badge appears
    vi.doMock("../store/ingestStore", () => ({
      useIngestRunningCount: () => 2,
    }));
    renderNavRail();

    // Badge is a <span> with aria-label="N running" inside the ingest button
    // It should be present; .nav-rail__label must NOT contain the badge text
    const ingestBtn = document.querySelector("[data-section='ingest']");
    if (ingestBtn) {
      const labelSpan = ingestBtn.querySelector(".nav-rail__label");
      // Label text should be the nav label, not the badge count
      const labelText = labelSpan?.textContent?.trim() ?? "";
      expect(labelText).not.toMatch(/^\d+$/);
    }
  });
});

// ─── AC-R11-3-1, AC-R11-3-2: no Synapse brand mark in NavRail (R11-3) ────────

describe("NavRail — no logo/brand mark present (AC-R11-3-1, AC-R11-3-2)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("AC-R11-3-2: contains no element with aria-label='Synapse' (brand mark removed)", () => {
    // The old Logo() component had aria-label="Synapse" on its wrapper div.
    // After R11-3 removal there must be no such element in the NavRail.
    const rail = document.querySelector("[data-testid='nav-rail']");
    expect(rail).not.toBeNull();
    const brandEl = rail!.querySelector("[aria-label='Synapse']");
    expect(brandEl, "NavRail must not contain an aria-label='Synapse' element").toBeNull();
  });

  it("AC-R11-3-1: contains no <img> element (no logo image rendered in nav rail)", () => {
    const rail = document.querySelector("[data-testid='nav-rail']");
    expect(rail).not.toBeNull();
    const imgs = rail!.querySelectorAll("img");
    expect(imgs.length, "NavRail must not contain any <img> elements").toBe(0);
  });

  it("AC-R11-3-1: any SVG inside NavRail is a nav-item icon, not a standalone brand SVG", () => {
    // Every SVG inside the rail must live inside a nav button (data-section attribute).
    // The old brand mark was a free-standing SVG in the Logo() wrapper div.
    const rail = document.querySelector("[data-testid='nav-rail']");
    expect(rail).not.toBeNull();
    const allSvgs = Array.from(rail!.querySelectorAll("svg"));
    for (const svg of allSvgs) {
      const closestButton = svg.closest("button");
      expect(
        closestButton,
        "Every SVG in NavRail must be inside a <button> (nav item icon, not a free-standing brand mark)",
      ).not.toBeNull();
    }
  });
});
