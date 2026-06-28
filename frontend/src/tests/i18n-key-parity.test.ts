/**
 * i18n-key-parity.test.ts — vitest parity test for i18n locales (ADR-0018 §6).
 *
 * Asserts that en.json and it.json have:
 *   1. Exactly the same top-level namespace keys.
 *   2. Exactly the same leaf keys within each namespace (recursive).
 *   3. No empty string values (every key has a non-empty translation).
 *
 * This prevents silent "missing key" fallbacks in production.
 */

import { describe, it, expect } from "vitest";
import en from "../i18n/locales/en.json";
import itLocale from "../i18n/locales/it.json";

// ─── Helpers ─────────────────────────────────────────────────────────────────

type JsonObject = { [key: string]: JsonValue };
type JsonValue = string | number | boolean | null | JsonObject;

/**
 * Recursively extract all dot-separated leaf key paths from a JSON object.
 * e.g. { nav: { pages: "Pages" } } → ["nav.pages"]
 */
function extractLeafPaths(obj: JsonObject, prefix = ""): string[] {
  const paths: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      paths.push(...extractLeafPaths(value as JsonObject, full));
    } else {
      paths.push(full);
    }
  }
  return paths.sort();
}

/**
 * Recursively collect all leaf values as a flat map from path → value.
 */
function extractLeafValues(obj: JsonObject, prefix = ""): Record<string, string> {
  const map: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      Object.assign(map, extractLeafValues(value as JsonObject, full));
    } else {
      map[full] = String(value);
    }
  }
  return map;
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("i18n locale key parity (en ↔ it)", () => {
  const enPaths = extractLeafPaths(en as JsonObject);
  const itPaths = extractLeafPaths(itLocale as JsonObject);

  it("en.json has at least 30 leaf keys", () => {
    expect(enPaths.length).toBeGreaterThanOrEqual(30);
  });

  it("it.json has the same number of leaf keys as en.json", () => {
    expect(itPaths.length).toBe(enPaths.length);
  });

  it("it.json has no keys missing from en.json", () => {
    const enSet = new Set(enPaths);
    const missing = itPaths.filter((k) => !enSet.has(k));
    expect(missing).toEqual([]);
  });

  it("en.json has no keys missing from it.json", () => {
    const itSet = new Set(itPaths);
    const missing = enPaths.filter((k) => !itSet.has(k));
    expect(missing).toEqual([]);
  });

  it("en.json has no empty string values", () => {
    const enValues = extractLeafValues(en as JsonObject);
    const empty = Object.entries(enValues)
      .filter(([, v]) => v.trim() === "")
      .map(([k]) => k);
    expect(empty).toEqual([]);
  });

  it("it.json has no empty string values", () => {
    const itValues = extractLeafValues(itLocale as JsonObject);
    const empty = Object.entries(itValues)
      .filter(([, v]) => v.trim() === "")
      .map(([k]) => k);
    expect(empty).toEqual([]);
  });

  it("en.json and it.json leaf paths are in exact parity", () => {
    expect(enPaths).toEqual(itPaths);
  });

  // Spot-check required keys from ADR-0018 §6
  const REQUIRED_KEYS = [
    "nav.pages",
    "nav.graph",
    "nav.ingest",
    "nav.settings",
    "nav.chat",
    "ingest.runIngest",
    "ingest.cost",
    "ingest.status.running",
    "ingest.status.completed",
    "ingest.status.failed",
    "ingest.status.convergedFalse",
    "ingest.costAnomaly",
    "ingest.noRunSelected",
    "provider.label",
    "provider.scope.vault",
    "provider.scope.global",
    "provider.capability.orchestrated",
    "provider.capability.orchestratedTools",
    "provider.capability.delegated",
    "settings.contextWindow",
    "settings.budgetSplit",
    "settings.language",
    "settings.reset",
    "common.loading",
    "common.retry",
  ];

  it.each(REQUIRED_KEYS)("en.json has required key: %s", (key) => {
    const enValues = extractLeafValues(en as JsonObject);
    expect(enValues[key]).toBeDefined();
    expect(enValues[key]).not.toBe("");
  });

  it.each(REQUIRED_KEYS)("it.json has required key: %s", (key) => {
    const itValues = extractLeafValues(itLocale as JsonObject);
    expect(itValues[key]).toBeDefined();
    expect(itValues[key]).not.toBe("");
  });
});
