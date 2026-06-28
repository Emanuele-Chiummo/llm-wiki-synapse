/**
 * ingest-formatters.test.ts — vitest unit tests for IngestRunList exported formatters.
 *
 * Tests:
 *   - formatCost: always 4 decimal places (I7)
 *   - formatRelativeTime: Intl.RelativeTimeFormat output shapes
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { formatCost, formatRelativeTime } from "../components/ingest/IngestRunList";

describe("formatCost (I7 — always 4dp)", () => {
  it("formats zero as $0.0000", () => {
    expect(formatCost(0)).toBe("$0.0000");
  });

  it("formats a small value with exactly 4 dp", () => {
    expect(formatCost(0.0023)).toBe("$0.0023");
  });

  it("formats a sub-penny value", () => {
    expect(formatCost(0.00001)).toBe("$0.0000");
  });

  it("formats an anomaly value > $1.00 still at 4dp", () => {
    expect(formatCost(1.2345)).toBe("$1.2345");
  });

  it("formats exactly $1.00 at 4dp", () => {
    expect(formatCost(1)).toBe("$1.0000");
  });

  it("formats a typical API cost", () => {
    expect(formatCost(0.0312)).toBe("$0.0312");
  });

  it("always includes $ prefix", () => {
    expect(formatCost(5.9999)).toMatch(/^\$/);
  });

  it("never shows fewer than 4 decimal places", () => {
    const result = formatCost(0.1);
    const dotIdx = result.indexOf(".");
    expect(dotIdx).toBeGreaterThan(0);
    expect(result.slice(dotIdx + 1)).toHaveLength(4);
  });
});

describe("formatRelativeTime", () => {
  let dateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // Pin "now" to a fixed point so relative time is deterministic
    const fixed = new Date("2024-01-15T12:00:00.000Z").getTime();
    dateSpy = vi.spyOn(Date, "now").mockReturnValue(fixed);
  });

  afterEach(() => {
    dateSpy.mockRestore();
  });

  it("shows 'now' or second-range for very recent times", () => {
    // 10 seconds ago
    const iso = new Date(Date.now() - 10_000).toISOString();
    const result = formatRelativeTime(iso, "en");
    // Intl.RelativeTimeFormat with numeric:"auto" may return "10 seconds ago"
    expect(result).toMatch(/second/i);
  });

  it("shows minute range for 5 minutes ago", () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    const result = formatRelativeTime(iso, "en");
    expect(result).toMatch(/minute/i);
  });

  it("shows hour range for 2 hours ago", () => {
    const iso = new Date(Date.now() - 2 * 3600_000).toISOString();
    const result = formatRelativeTime(iso, "en");
    expect(result).toMatch(/hour/i);
  });

  it("shows day range for 3 days ago", () => {
    const iso = new Date(Date.now() - 3 * 86400_000).toISOString();
    const result = formatRelativeTime(iso, "en");
    expect(result).toMatch(/day/i);
  });

  it("returns a non-empty string for Italian locale", () => {
    const iso = new Date(Date.now() - 60_000).toISOString();
    const result = formatRelativeTime(iso, "it");
    expect(result.length).toBeGreaterThan(0);
  });
});
