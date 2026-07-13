import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const themeCss = readFileSync(resolve(process.cwd(), "src/styles/theme.css"), "utf8");
const deepSearchSource = readFileSync(
  resolve(process.cwd(), "src/components/research/DeepSearchView.tsx"),
  "utf8",
);

describe("design-system input surfaces", () => {
  it("defines an input background for both light and dark themes", () => {
    expect(themeCss).toMatch(/:root\s*\{[\s\S]*--syn-input-bg:\s*#[0-9a-fA-F]{6}/);
    expect(themeCss).toMatch(
      /:root\[data-theme="dark"\]\s*\{[\s\S]*--syn-input-bg:\s*#[0-9a-fA-F]{6}/,
    );
  });

  it("defines the semantic aliases consumed by shared components", () => {
    for (const token of ["accent2", "danger", "error", "success", "font-mono"]) {
      expect(themeCss, `missing --syn-${token}`).toMatch(new RegExp(`--syn-${token}:\\s*[^;]+;`));
    }
  });

  it("defines a token for every visual page type", () => {
    for (const type of [
      "concept",
      "entity",
      "source",
      "synthesis",
      "comparison",
      "query",
      "overview",
      "index",
      "log",
      "other",
    ]) {
      expect(themeCss, `missing --syn-type-${type}`).toContain(`--syn-type-${type}:`);
    }
  });
});

describe("responsive component hooks", () => {
  it("wires Deep Research to the mobile layout selectors", () => {
    expect(deepSearchSource).toContain('className="deep-search-view"');
    expect(deepSearchSource).toContain('className="deep-search-view__detail"');
  });
});
