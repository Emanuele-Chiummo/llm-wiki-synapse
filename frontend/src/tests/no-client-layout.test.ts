/**
 * no-client-layout.test.ts — AC-FE-2 / ADR-0015 §1 ENFORCEMENT
 *
 * This test provides a SECOND, independent enforcement layer for I2.
 * It scans the built dist/ bundle for FORBIDDEN client-side layout strings.
 *
 * The first layer is the eslint no-restricted-imports rule in eslint.config.js.
 * This layer catches anything that might slip through (dynamic import, indirect dep).
 *
 * Run AFTER `npm run build` to test the actual bundle.
 * In CI: `npm run build && npm run test`.
 *
 * A match in the bundle = P0 block: escalate to solution-architect immediately.
 *
 * ADR-0015 §1:
 *   "A vitest/grep bundle-check test scans dist/ for the forbidden strings (AC-FE-2)"
 */

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ─── Forbidden patterns (I2 / ADR-0015 §1) ───────────────────────────────────

/**
 * These strings MUST NOT appear in the built bundle.
 * Any match = client-side layout = P0 invariant violation.
 */
const FORBIDDEN_PATTERNS: Array<{ pattern: string; reason: string }> = [
  {
    pattern: "graphology-layout-forceatlas2",
    reason: "FA2 layout is server-side only (ADR-0013). Client FA2 = I2 violation.",
  },
  {
    pattern: "ForceAtlas2",
    reason: "FA2 supervisor/worker = client layout = I2 violation.",
  },
  {
    pattern: "forceAtlas2",
    reason: "FA2 call = client layout = I2 violation.",
  },
  {
    pattern: "fa2Worker",
    reason: "FA2 web worker = client layout = I2 violation.",
  },
  {
    pattern: "d3-force",
    reason: "d3-force is forbidden (ADR-0015 §1).",
  },
  {
    pattern: "forceSimulation",
    reason: "d3 force simulation = client physics layout = I2 violation.",
  },
  {
    pattern: "@antv/layout",
    reason: "@antv/layout is forbidden (ADR-0015 §1).",
  },
  {
    pattern: "graphology-layout-random",
    reason:
      "Random layout assigns client-computed positions — use server x/y directly (ADR-0015 §1).",
  },
  {
    pattern: "graphology-layout-circular",
    reason:
      "Circular layout assigns client-computed positions — use server x/y directly (ADR-0015 §1).",
  },
  {
    pattern: "graphology-layout-noverlap",
    reason: "No-overlap layout mutates node positions client-side = I2 violation.",
  },
];

// ─── Bundle discovery ─────────────────────────────────────────────────────────

const _thisDir = dirname(fileURLToPath(import.meta.url));
const DIST_DIR = join(_thisDir, "../../dist/assets");

function getBundleFiles(): string[] {
  if (!existsSync(DIST_DIR)) {
    // Bundle not built yet — skip (test is a no-op until build runs)
    return [];
  }
  return readdirSync(DIST_DIR)
    .filter((f): f is string => typeof f === "string" && f.endsWith(".js"))
    .map((f) => join(DIST_DIR, f));
}

function readBundle(filePath: string): string {
  return readFileSync(filePath, "utf-8");
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("AC-FE-2 / I2 / ADR-0015 §1 — no client-side layout in bundle", () => {
  const bundleFiles = getBundleFiles();

  if (bundleFiles.length === 0) {
    it.skip("dist/ not found — run `npm run build` first, then re-run tests", () => {
      // intentionally skipped when bundle does not exist
    });
    return;
  }

  it("dist/assets/ contains at least one JS bundle file", () => {
    expect(bundleFiles.length).toBeGreaterThan(0);
  });

  // One test per forbidden pattern — makes it easy to identify the violation
  for (const { pattern, reason } of FORBIDDEN_PATTERNS) {
    it(`bundle does NOT contain "${pattern}"`, () => {
      const violations: string[] = [];

      for (const filePath of bundleFiles) {
        const content = readBundle(filePath);
        if (content.includes(pattern)) {
          violations.push(filePath);
        }
      }

      if (violations.length > 0) {
        // Fail with a clear P0 message for the architect bundle review
        expect.fail(
          `[P0 I2 VIOLATION] Pattern "${pattern}" found in bundle.\n` +
            `Reason: ${reason}\n` +
            `Files: ${violations.join(", ")}\n` +
            `Action: escalate to solution-architect immediately. See ADR-0015 §1.`,
        );
      }

      expect(violations).toHaveLength(0);
    });
  }
});

// ─── Source-level import check (belt-and-suspenders, runs without build) ─────
//
// Scans non-test source files for ACTUAL IMPORT STATEMENTS containing the
// forbidden package names. Comments/string literals in test files that name
// the forbidden patterns are intentionally excluded (the import statement
// pattern "from \"<pkg>\"" or "import \"<pkg>\"" is the dangerous form).

describe("AC-FE-2 / I2 — no client-layout imports in source (pre-build check)", () => {
  const SRC_DIR = join(_thisDir, "../");

  /**
   * Collect non-test TS/TSX source files — test files are excluded because
   * they legitimately reference forbidden names in string constants for the
   * purposes of this very check. We check the real source code only.
   */
  function collectSourceFiles(dir: string): string[] {
    const entries = readdirSync(dir, { withFileTypes: true });
    const files: string[] = [];
    for (const entry of entries) {
      const fullPath = join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== "node_modules" && entry.name !== "tests") {
        files.push(...collectSourceFiles(fullPath));
      } else if (
        entry.isFile() &&
        (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) &&
        !entry.name.endsWith(".test.ts") &&
        !entry.name.endsWith(".test.tsx") &&
        !entry.name.endsWith(".spec.ts")
      ) {
        files.push(fullPath);
      }
    }
    return files;
  }

  /**
   * Detect whether a file IMPORTS (not just mentions in a comment/doc) a package.
   * We match: import ... from "pkg", import "pkg", require("pkg") as import forms.
   * Plain string mentions in comments or JSDoc are not matches.
   */
  function fileImportsForbiddenPackage(content: string, pkg: string): boolean {
    // Match: from "pkg", from 'pkg'  (ESM)
    const esmPattern = new RegExp(`from\\s+['"]${pkg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "m");
    // Match: import "pkg", import 'pkg'  (side-effect import)
    const sideEffectPattern = new RegExp(
      `import\\s+['"]${pkg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      "m",
    );
    // Match: require("pkg"), require('pkg')  (CJS, unlikely in TS but guard anyway)
    const requirePattern = new RegExp(
      `require\\(['"]${pkg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      "m",
    );
    return esmPattern.test(content) || sideEffectPattern.test(content) || requirePattern.test(content);
  }

  const sourceFiles = collectSourceFiles(SRC_DIR);

  it("src/ contains non-test TypeScript source files", () => {
    expect(sourceFiles.length).toBeGreaterThan(0);
  });

  for (const { pattern, reason } of FORBIDDEN_PATTERNS) {
    it(`source does NOT import "${pattern}"`, () => {
      const violations: string[] = [];
      for (const file of sourceFiles) {
        const content = readFileSync(file, "utf-8");
        if (fileImportsForbiddenPackage(content, pattern)) {
          violations.push(file);
        }
      }

      if (violations.length > 0) {
        expect.fail(
          `[P0 I2 VIOLATION] Import of "${pattern}" found in source.\n` +
            `Reason: ${reason}\n` +
            `Files: ${violations.join(", ")}\n` +
            `Action: escalate to solution-architect immediately. See ADR-0015 §1.`,
        );
      }

      expect(violations).toHaveLength(0);
    });
  }
});
