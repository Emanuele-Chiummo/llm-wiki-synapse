/**
 * settings-formatters.test.ts — vitest tests for settingsStore formatting helpers.
 *
 * Tests:
 *   - formatTokenCount: 1024-based divisors (powers-of-two labels)
 *   - computeBudgetSplit: 60/20/5/15 percentages
 */

import { describe, it, expect } from "vitest";
import {
  formatTokenCount,
  computeBudgetSplit,
  CONTEXT_WINDOW_OPTIONS,
} from "../store/settingsStore";

describe("formatTokenCount (1024-based, powers-of-two clean labels)", () => {
  it("4096 → '4K'", () => {
    expect(formatTokenCount(4_096)).toBe("4K");
  });

  it("8192 → '8K'", () => {
    expect(formatTokenCount(8_192)).toBe("8K");
  });

  it("16384 → '16K'", () => {
    expect(formatTokenCount(16_384)).toBe("16K");
  });

  it("32768 → '32K'", () => {
    expect(formatTokenCount(32_768)).toBe("32K");
  });

  it("65536 → '64K'", () => {
    expect(formatTokenCount(65_536)).toBe("64K");
  });

  it("131072 → '128K'", () => {
    expect(formatTokenCount(131_072)).toBe("128K");
  });

  it("262144 → '256K'", () => {
    expect(formatTokenCount(262_144)).toBe("256K");
  });

  it("524288 → '512K'", () => {
    expect(formatTokenCount(524_288)).toBe("512K");
  });

  it("1048576 → '1M'", () => {
    expect(formatTokenCount(1_048_576)).toBe("1M");
  });

  it("all 9 CONTEXT_WINDOW_OPTIONS produce the expected labels", () => {
    const expected = ["4K", "8K", "16K", "32K", "64K", "128K", "256K", "512K", "1M"];
    const actual = [...CONTEXT_WINDOW_OPTIONS].map(formatTokenCount);
    expect(actual).toEqual(expected);
  });

  it("does not produce decimal labels for power-of-two values", () => {
    for (const opt of CONTEXT_WINDOW_OPTIONS) {
      const label = formatTokenCount(opt);
      expect(label, `Option ${opt} must not contain a decimal: got ${label}`).not.toMatch(/\./);
    }
  });
});

describe("computeBudgetSplit (60/20/5/15)", () => {
  it("sums to input token count (allow ±4 due to rounding)", () => {
    for (const tokens of CONTEXT_WINDOW_OPTIONS) {
      const split = computeBudgetSplit(tokens);
      const total = split.history + split.retrieved + split.system + split.generation;
      expect(
        Math.abs(total - tokens),
        `Budget split for ${tokens} sums to ${total}, expected ~${tokens}`,
      ).toBeLessThanOrEqual(4);
    }
  });

  it("32768 splits to approximately 60/20/5/15", () => {
    const split = computeBudgetSplit(32_768);
    expect(split.history).toBe(Math.round(32_768 * 0.6));    // 19661
    expect(split.retrieved).toBe(Math.round(32_768 * 0.2));  // 6554
    expect(split.system).toBe(Math.round(32_768 * 0.05));    // 1638
    expect(split.generation).toBe(Math.round(32_768 * 0.15)); // 4915
  });

  it("history is always the largest slice", () => {
    for (const tokens of CONTEXT_WINDOW_OPTIONS) {
      const split = computeBudgetSplit(tokens);
      expect(split.history).toBeGreaterThan(split.retrieved);
      expect(split.history).toBeGreaterThan(split.system);
      expect(split.history).toBeGreaterThan(split.generation);
    }
  });
});
