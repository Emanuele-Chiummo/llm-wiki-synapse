/**
 * SectionChangelog.test.tsx — unit tests for the Changelog settings section.
 *
 * Covers:
 *   Parser (parseChangelog):
 *     - Extracts version, date, codename, body from Keep a Changelog text.
 *     - Handles Unreleased entries (no date).
 *     - Handles entries with empty bodies.
 *     - Returns entries in file order (newest / Unreleased first).
 *
 *   Component (SectionChangelog):
 *     - Loading state shown immediately on mount.
 *     - Accordion cards render: one card per version in the fixture.
 *     - First card (Unreleased) auto-expanded; others collapsed by default.
 *     - Card body hidden until the toggle is clicked; shown after click.
 *     - Clicking an open card collapses it.
 *     - Empty body shows the localised emptyBody placeholder.
 *     - "Unavailable" note + refresh button on fetch error.
 *     - "Unavailable" note on non-ok HTTP response.
 *     - Refresh button re-triggers fetch.
 *     - data-testid="section-changelog" always present.
 *     - Version count label rendered.
 *
 * renderMarkdown is mocked to avoid DOMPurify/marked/KaTeX overhead and keep
 * the test focused on the component's parse + state machine logic.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  SectionChangelog,
  parseChangelog,
  VISIBLE_MAX,
} from "../components/settings/sections/SectionChangelog";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock renderMarkdown ──────────────────────────────────────────────────────
// Returns a predictable sentinel so we can assert it appeared without running
// the full DOMPurify / marked / KaTeX pipeline in jsdom.

vi.mock("../components/chat/renderMarkdown", () => ({
  renderMarkdown: (text: string) => `<p data-testid="md-rendered">${text.slice(0, 60)}</p>`,
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const SAMPLE_CHANGELOG = `# Changelog

All notable changes here.

## [Unreleased]

### Added
- New feature X

## [1.3.16] — 2026-07-09

### Fixed
- Bug A

## [1.3.15] — 2026-06-01 Some codename

### Changed
- Behaviour B

## [0.1.0] — 2025-01-01

`;

// CHANGELOG with one version that has an empty body
const EMPTY_BODY_CHANGELOG = `## [Unreleased]

## [1.0.0] — 2025-01-01

### Added
- First release
`;

// CHANGELOG with 15 versioned entries (no Unreleased) — more than VISIBLE_MAX (10)
function makeBigChangelog(count: number): string {
  let out = "# Changelog\n\n";
  for (let i = count; i >= 1; i--) {
    out += `## [1.0.${i}] — 2025-01-${String(i).padStart(2, "0")}\n\n### Added\n- Item ${i}\n\n`;
  }
  return out;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeOkFetch(body: string) {
  return vi.fn().mockResolvedValue({
    ok: true,
    text: () => Promise.resolve(body),
  });
}

function makeNotOkFetch(status = 404) {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    text: () => Promise.resolve(""),
  });
}

function makeErrorFetch() {
  return vi.fn().mockRejectedValue(new Error("network error"));
}

// ─── Parser tests ─────────────────────────────────────────────────────────────

describe("parseChangelog — Keep a Changelog parser", () => {
  it("returns an empty array for text with no ## [...] headers", () => {
    const result = parseChangelog("# Changelog\n\nSome intro text.");
    expect(result).toHaveLength(0);
  });

  it("parses an Unreleased entry with no date", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    const unreleased = result.find((e) => e.version === "Unreleased");
    expect(unreleased).toBeDefined();
    expect(unreleased!.date).toBeNull();
    expect(unreleased!.codename).toBeNull();
  });

  it("parses the Unreleased body correctly", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    const unreleased = result.find((e) => e.version === "Unreleased")!;
    expect(unreleased.body).toContain("### Added");
    expect(unreleased.body).toContain("New feature X");
  });

  it("parses a versioned entry with date", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    const v1 = result.find((e) => e.version === "1.3.16");
    expect(v1).toBeDefined();
    expect(v1!.date).toBe("2026-07-09");
    expect(v1!.codename).toBeNull();
    expect(v1!.body).toContain("Bug A");
  });

  it("parses a versioned entry with date AND codename", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    const v2 = result.find((e) => e.version === "1.3.15");
    expect(v2).toBeDefined();
    expect(v2!.date).toBe("2026-06-01");
    expect(v2!.codename).toBe("Some codename");
  });

  it("preserves file order (Unreleased first, then newest to oldest)", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    expect(result[0]!.version).toBe("Unreleased");
    expect(result[1]!.version).toBe("1.3.16");
    expect(result[2]!.version).toBe("1.3.15");
    expect(result[3]!.version).toBe("0.1.0");
  });

  it("produces 4 entries for the sample fixture", () => {
    const result = parseChangelog(SAMPLE_CHANGELOG);
    expect(result).toHaveLength(4);
  });

  it("handles an entry with an empty body", () => {
    const result = parseChangelog(EMPTY_BODY_CHANGELOG);
    const unreleased = result.find((e) => e.version === "Unreleased")!;
    expect(unreleased.body).toBe("");
  });

  it("still creates a ChangelogEntry for an empty-body version", () => {
    const result = parseChangelog(EMPTY_BODY_CHANGELOG);
    expect(result).toHaveLength(2);
    expect(result[0]!.version).toBe("Unreleased");
    expect(result[1]!.version).toBe("1.0.0");
  });

  it("handles em-dash (—) in the date separator", () => {
    const text = `## [2.0.0] — 2026-12-01\n\n### Added\n- Something\n`;
    const result = parseChangelog(text);
    expect(result[0]!.date).toBe("2026-12-01");
  });

  it("handles en-dash (–) in the date separator", () => {
    const text = `## [2.0.0] – 2026-12-01\n\n### Added\n- Something\n`;
    const result = parseChangelog(text);
    expect(result[0]!.date).toBe("2026-12-01");
  });
});

// ─── Component tests ──────────────────────────────────────────────────────────

describe("SectionChangelog — component fetch states", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("always renders data-testid='section-changelog'", async () => {
    vi.stubGlobal("fetch", makeOkFetch(SAMPLE_CHANGELOG));
    render(<SectionChangelog />);
    expect(document.querySelector('[data-testid="section-changelog"]')).not.toBeNull();
  });

  it("shows loading state immediately before fetch resolves", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    render(<SectionChangelog />);
    // i18n mock: "settings.changelog.loading" → "loading"
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("shows 'unavailable' on network error", async () => {
    vi.stubGlobal("fetch", makeErrorFetch());
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByText("unavailable")).toBeTruthy());
  });

  it("shows 'unavailable' on non-ok HTTP response", async () => {
    vi.stubGlobal("fetch", makeNotOkFetch(404));
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByText("unavailable")).toBeTruthy());
  });

  it("shows refresh button in error state", async () => {
    vi.stubGlobal("fetch", makeErrorFetch());
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-refresh-btn")).toBeTruthy());
  });

  it("fetch is called with /CHANGELOG.md", async () => {
    const mockFetch = makeOkFetch(SAMPLE_CHANGELOG);
    vi.stubGlobal("fetch", mockFetch);
    render(<SectionChangelog />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith("/CHANGELOG.md"));
  });

  it("clicking refresh re-fetches (called twice total)", async () => {
    const mockFetch = makeOkFetch(SAMPLE_CHANGELOG);
    vi.stubGlobal("fetch", mockFetch);
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-refresh-btn")).toBeTruthy());
    fireEvent.click(screen.getByTestId("changelog-refresh-btn"));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  });
});

describe("SectionChangelog — accordion cards (ok state)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function renderAndWait(fixture = SAMPLE_CHANGELOG) {
    vi.stubGlobal("fetch", makeOkFetch(fixture));
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-count")).toBeTruthy());
  }

  it("renders one card per version in the fixture (4 total)", async () => {
    await renderAndWait();
    // Each entry has data-testid="changelog-entry-<version>"
    expect(screen.getByTestId("changelog-entry-Unreleased")).toBeTruthy();
    expect(screen.getByTestId("changelog-entry-1.3.16")).toBeTruthy();
    expect(screen.getByTestId("changelog-entry-1.3.15")).toBeTruthy();
    expect(screen.getByTestId("changelog-entry-0.1.0")).toBeTruthy();
  });

  it("shows the version count label", async () => {
    await renderAndWait();
    const countEl = screen.getByTestId("changelog-count");
    // Fixture has 4 entries; i18n mock returns "versions" for the label
    expect(countEl.textContent).toContain("4");
    expect(countEl.textContent).toContain("versions");
  });

  it("first card (Unreleased) is auto-expanded on load", async () => {
    await renderAndWait();
    // aria-expanded=true on the Unreleased toggle
    const toggleBtn = screen.getByTestId("changelog-toggle-Unreleased");
    expect(toggleBtn.getAttribute("aria-expanded")).toBe("true");
  });

  it("body is visible for the auto-expanded first card", async () => {
    await renderAndWait();
    expect(screen.getByTestId("changelog-body-Unreleased")).toBeTruthy();
  });

  it("non-first cards are collapsed by default", async () => {
    await renderAndWait();
    // 1.3.16 should be collapsed
    const toggle = screen.getByTestId("changelog-toggle-1.3.16");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("changelog-body-1.3.16")).toBeNull();
  });

  it("clicking a collapsed card expands it and shows the body", async () => {
    await renderAndWait();
    const toggle = screen.getByTestId("changelog-toggle-1.3.16");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByTestId("changelog-body-1.3.16")).toBeTruthy();
    });
  });

  it("clicking an expanded card collapses it and hides the body", async () => {
    await renderAndWait();
    // Unreleased is already open
    const toggle = screen.getByTestId("changelog-toggle-Unreleased");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByTestId("changelog-body-Unreleased")).toBeNull();
    });
  });

  it("multiple cards can be expanded independently", async () => {
    await renderAndWait();
    // Expand 1.3.16 (Unreleased is already open)
    fireEvent.click(screen.getByTestId("changelog-toggle-1.3.16"));
    await waitFor(() => {
      expect(screen.getByTestId("changelog-body-Unreleased")).toBeTruthy();
      expect(screen.getByTestId("changelog-body-1.3.16")).toBeTruthy();
    });
  });

  it("date is visible in the card header for versioned entries", async () => {
    await renderAndWait();
    expect(screen.getByTestId("changelog-date-1.3.16").textContent).toBe("2026-07-09");
  });

  it("codename is visible in the card header when present", async () => {
    await renderAndWait();
    // 1.3.15 has codename "Some codename"
    const entry = screen.getByTestId("changelog-entry-1.3.15");
    expect(entry.textContent).toContain("Some codename");
  });

  it("Unreleased card has no date element", async () => {
    await renderAndWait();
    expect(screen.queryByTestId("changelog-date-Unreleased")).toBeNull();
  });

  it("version badge shows 'unreleased' label for Unreleased (leaf i18n key)", async () => {
    await renderAndWait();
    // i18n mock: "settings.changelog.unreleased" → "unreleased"
    const badge = screen.getByTestId("changelog-version-Unreleased");
    expect(badge.textContent).toBe("unreleased");
  });

  it("version badge shows 'vX.Y.Z' for numbered versions", async () => {
    await renderAndWait();
    const badge = screen.getByTestId("changelog-version-1.3.16");
    expect(badge.textContent).toBe("v1.3.16");
  });

  it("empty body shows emptyBody placeholder instead of crashing", async () => {
    await renderAndWait(EMPTY_BODY_CHANGELOG);
    // Unreleased auto-expands; its body is empty
    await waitFor(() => {
      expect(screen.getByTestId("changelog-body-Unreleased")).toBeTruthy();
    });
    // i18n mock: "settings.changelog.emptyBody" → "emptyBody"
    const body = screen.getByTestId("changelog-body-Unreleased");
    expect(body.textContent).toContain("emptyBody");
  });

  it("refresh button is visible in the ok state", async () => {
    await renderAndWait();
    expect(screen.getByTestId("changelog-refresh-btn")).toBeTruthy();
  });

  it("footer element is visible in the ok state", async () => {
    await renderAndWait();
    expect(screen.getByTestId("changelog-footer")).toBeTruthy();
  });

  it("footer contains the 'footer' i18n key text", async () => {
    await renderAndWait();
    // i18n mock: "settings.changelog.footer" → "footer"
    const footer = screen.getByTestId("changelog-footer");
    expect(footer.textContent).toContain("footer");
  });

  it("footer contains the footerLink text linking to GitHub Releases", async () => {
    await renderAndWait();
    const footer = screen.getByTestId("changelog-footer");
    // i18n mock: "settings.changelog.footerLink" → "footerLink"
    expect(footer.textContent).toContain("footerLink");
    const link = footer.querySelector("a");
    expect(link).not.toBeNull();
    expect(link!.getAttribute("href")).toContain("github.com");
  });
});

// ─── Top-10 cap tests ─────────────────────────────────────────────────────────

describe("SectionChangelog — VISIBLE_MAX cap (at most 10 cards)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("VISIBLE_MAX constant equals 10", () => {
    expect(VISIBLE_MAX).toBe(10);
  });

  it("renders exactly 4 cards for the 4-entry SAMPLE_CHANGELOG fixture", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(SAMPLE_CHANGELOG),
      }),
    );
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-count")).toBeTruthy());
    // SAMPLE_CHANGELOG has 4 entries (< VISIBLE_MAX) — all 4 rendered
    expect(screen.getByTestId("changelog-count").textContent).toContain("4");
    const listItems = document.querySelectorAll('[role="listitem"]');
    expect(listItems).toHaveLength(4);
  });

  it("renders at most VISIBLE_MAX cards when the source has more than 10 versions", async () => {
    const bigCl = makeBigChangelog(15); // 15 versions
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(bigCl),
      }),
    );
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-count")).toBeTruthy());
    const listItems = document.querySelectorAll('[role="listitem"]');
    expect(listItems.length).toBeLessThanOrEqual(VISIBLE_MAX);
    expect(listItems).toHaveLength(VISIBLE_MAX); // exactly 10
  });

  it("shows the newest entry first (most recent semver) when capped at 10", async () => {
    const bigCl = makeBigChangelog(15); // 1.0.15 down to 1.0.1
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(bigCl),
      }),
    );
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-count")).toBeTruthy());
    // 1.0.15 should be present (first/newest); 1.0.5 absent (11th, beyond cap)
    expect(screen.getByTestId("changelog-entry-1.0.15")).toBeTruthy();
    expect(screen.queryByTestId("changelog-entry-1.0.5")).toBeNull();
  });

  it("count label shows the number of displayed cards (capped at 10, not the total)", async () => {
    const bigCl = makeBigChangelog(15);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve(bigCl),
      }),
    );
    render(<SectionChangelog />);
    await waitFor(() => expect(screen.getByTestId("changelog-count")).toBeTruthy());
    // The count label shows how many cards are actually rendered, not the total
    const countEl = screen.getByTestId("changelog-count");
    expect(countEl.textContent).toContain("10");
  });
});
