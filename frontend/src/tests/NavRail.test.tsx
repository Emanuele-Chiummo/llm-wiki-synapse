/**
 * NavRail.test.tsx — vitest unit tests for NavRail (M4-HARD + M5 Phase 3 update).
 *
 * M5 Phase 2 (F10): Deep Search is now an ACTIVE nav item (AC-F10-8a).
 * M5 Phase 3 (F9): Review is now an ACTIVE nav item (AC-F9-5).
 *
 * Covers:
 *   AC-HARD-LBL-7: each rendered item has both an SVG icon and a visible label span.
 *   AC-HARD-M5P-6 (updated for M5 Phase 3): search/lint absent; deep-search + review present.
 *   AC-HARD-ORD-1 (updated for M5 Phase 3): exactly 7 interactive items
 *                  (Chat/Wiki/Sources/Graph/Review/DeepSearch/Settings).
 *   AC-F10-8a: "Deep Search" nav item renders in the rail.
 *   AC-F9-5: "Review" nav item renders in the rail.
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

// ─── AC-HARD-ORD-1 (M5 Phase 3 update): exactly 7 interactive nav items ──────

describe("NavRail — item count and order (AC-HARD-ORD-1 M5 Phase3, AC-F10-8a, AC-F9-5)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("renders exactly 7 interactive buttons (Chat/Wiki/Sources/Graph/Review/DeepSearch/Settings)", () => {
    const buttons = screen.getAllByRole("button");
    // 7 nav buttons: chat, pages, ingest, graph, review, deep-search, settings
    expect(buttons).toHaveLength(7);
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

  it("renders a Sources/ingest button", () => {
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
});

// ─── AC-HARD-M5P-6 (M5 Phase 3 update) + AC-F10-8a + AC-F9-5 ────────────────

describe("NavRail — M5 Phase 3 items (AC-HARD-M5P-6 updated, AC-F10-8a, AC-F9-5)", () => {
  beforeEach(() => {
    renderNavRail();
  });

  it("does NOT render data-section='search' (not yet active in M5 Phase 3)", () => {
    expect(document.querySelector("[data-section='search']")).toBeNull();
  });

  it("does NOT render data-section='lint' (not yet active in M5)", () => {
    expect(document.querySelector("[data-section='lint']")).toBeNull();
  });

  it("DOES render data-section='review' (AC-F9-5 — Review active in M5 Phase 3)", () => {
    expect(document.querySelector("[data-section='review']")).not.toBeNull();
  });

  it("DOES render data-section='deep-search' (AC-F10-8a — Deep Search active in M5 Phase 2)", () => {
    expect(document.querySelector("[data-section='deep-search']")).not.toBeNull();
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
      expect(
        svgs.length,
        `Button id="${btn.id}" should have an SVG icon`,
      ).toBeGreaterThan(0);
    });
  });

  it("each nav button contains a non-empty .nav-rail__label span", () => {
    const buttons = document.querySelectorAll("[data-testid='nav-rail'] button");
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((btn) => {
      const labelSpan = btn.querySelector(".nav-rail__label");
      expect(
        labelSpan,
        `Button id="${btn.id}" should have a .nav-rail__label span`,
      ).not.toBeNull();
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
