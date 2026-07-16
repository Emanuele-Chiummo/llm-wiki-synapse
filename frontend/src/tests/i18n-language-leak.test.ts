/**
 * i18n-language-leak.test.ts — CI gate that catches Italian strings leaking into en.json.
 *
 * Covers:
 *   (a) Key parity: en.json and it.json have exactly the same leaf-key set
 *       (extends / duplicates from i18n-key-parity.test.ts — explicit here for clarity).
 *   (b) Language-leak heuristic: no en.json value contains common Italian-only words
 *       with accents or Italian-specific phrases (perché, più, attività, così, etc.).
 *       A whitelist covers legitimate false-positives (proper nouns, technical terms).
 *
 * If this test fails it means an Italian string was accidentally placed in en.json.
 * Fix by replacing the Italian value with its English translation.
 */

import { describe, it, expect } from "vitest";
import en from "../i18n/locales/en.json";
import itLocale from "../i18n/locales/it.json";

// ─── Types ────────────────────────────────────────────────────────────────────

type JsonObject = { [key: string]: JsonValue };
type JsonValue = string | number | boolean | null | JsonObject;

// ─── Helpers ─────────────────────────────────────────────────────────────────

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

// ─── Heuristics ───────────────────────────────────────────────────────────────

/**
 * Italian indicator patterns — words that are strongly Italian-specific and
 * unlikely to appear legitimately in an English UI string.
 *
 * Rules:
 *   - Use word-boundaries (\b) to avoid false positives inside longer words
 *   - Only flag accented forms and distinctively Italian constructs
 *   - Do NOT flag ambiguous short words (e.g. "la", "di") — too many English false-positives
 */
const ITALIAN_PATTERNS: RegExp[] = [
  // Accented words (uniquely Italian when accented)
  /\bperch[eé]\b/i,
  /\bperò\b/i,
  /\battivit[àa]\b/i,   // "attività" with accent is Italian
  /\bcos[ìi]\b/i,        // "così"
  /\bpi[uù]\b/i,         // "più"
  /\bcitt[àa]\b/i,
  /\bverit[àa]\b/i,
  /\bunit[àa]\b/i,
  /\bqualit[àa]\b/i,
  /\blibert[àa]\b/i,
  /\bsicurezza\b/i,
  // Italian-specific constructs (very unlikely in English)
  /\bdell[ae]\b/i,       // "della", "delle"
  /\bdegli\b/i,
  /\bnell[ae]\b/i,       // "nella", "nelle"
  /\bnello\b/i,
  /\bsullo\b/i,
  /\bsulla\b/i,
  /\bsulle\b/i,
  /\balle\b(?!\s*right|\s*panel)/i,  // "alle" but not in English contexts
  /\bagli\b/i,
  /\bnostra\b/i,
  /\bnostro\b/i,
  /\bnostri\b/i,
  /\bnostre\b/i,
  /\bvostra\b/i,
  /\btutto\b/i,           // "tutto" (not "button" etc.)
  /\btutti\b/i,
  /\btutte\b/i,
  /\bogni\b/i,
  /\bnessun[ao]?\b/i,
  /\bqualsiasi\b/i,
  /\bsempre\b/i,
  /\bancora\b/i,
  /\boppure\b/i,
  /\banche\b/i,
  /\bquindi\b/i,
  /\binfatti\b/i,
  /\bgrazie\b/i,
  /\bprego\b/i,
  /\bavvio\b/i,
  /\bpagine\b/i,
  /\bpagina\b/i,
  /\bsezione\b/i,
  /\bsezioni\b/i,
  /\bgruppi\b/i,
  /\blavori\b/i,
  /\bstato\s+del\b/i,    // "stato del" (system status in Italian)
  /\bGRUPPI\b/,           // ALL-CAPS Italian
  /\bLAVORI\b/,
  /\bSEZIONI\b/,
  /\bSTATO\b(?!\s+status|\s+machine|\s+bar|\s+of)/,  // "STATO" not followed by English words
  /\bATTIVI\b/,
  /\bAUTOMATICI\b/,
];

/**
 * Whitelist: keys whose values are allowed to match the Italian patterns.
 * Use this for legitimate technical terms, proper nouns, or user-supplied content
 * that happens to look Italian.
 */
const WHITELIST_KEYS = new Set<string>([
  // i18next interpolation patterns like {{variabile}} look like Italian but aren't
  // (handled separately by stripping interpolations before testing)

  // "Tailscale" contains "alle" — whitelist the specific key if needed
  // Add specific keys here if the heuristic generates false positives:
  // e.g. "settings.maintenance.someKey"
]);

/**
 * Strip i18next interpolation placeholders ({{...}}) before testing,
 * to avoid false-positive matches on variable names.
 */
function stripInterpolations(s: string): string {
  return s.replace(/\{\{[^}]+\}\}/g, "").replace(/\{[^}]+\}/g, "");
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("i18n language-leak guard (en.json must not contain Italian)", () => {
  const enValues = extractLeafValues(en as JsonObject);
  const enPaths = extractLeafPaths(en as JsonObject);
  const itPaths = extractLeafPaths(itLocale as JsonObject);

  // (a) Key parity
  it("en.json and it.json have the same number of leaf keys", () => {
    expect(enPaths.length).toBe(itPaths.length);
  });

  it("en.json has no keys missing from it.json", () => {
    const itSet = new Set(itPaths);
    const missing = enPaths.filter((k) => !itSet.has(k));
    expect(missing).toEqual([]);
  });

  it("it.json has no keys missing from en.json", () => {
    const enSet = new Set(enPaths);
    const missing = itPaths.filter((k) => !enSet.has(k));
    expect(missing).toEqual([]);
  });

  // (b) Language-leak heuristic
  it("en.json values do not contain Italian words/phrases", () => {
    const leaks: string[] = [];

    for (const [key, raw] of Object.entries(enValues)) {
      if (WHITELIST_KEYS.has(key)) continue;
      const value = stripInterpolations(raw);

      for (const pattern of ITALIAN_PATTERNS) {
        if (pattern.test(value)) {
          leaks.push(`${key}: "${raw}" (matched: ${String(pattern)})`);
          break; // one report per key is enough
        }
      }
    }

    expect(leaks).toEqual([]);
  });

  // Spot-check the specific keys that were wrongly Italian before this fix
  it("home.systemStatus.title is in English (not Italian)", () => {
    expect(enValues["home.systemStatus.title"]).toBe("System Status");
  });

  it("home.sections.title is in English (not Italian)", () => {
    expect(enValues["home.sections.title"]).toBe("Sections");
  });

  it("home.groups.title is in English (not Italian)", () => {
    expect(enValues["home.groups.title"]).toBe("Automatic Groups");
  });

  it("home.activeJobs.title is in English (not Italian)", () => {
    expect(enValues["home.activeJobs.title"]).toBe("Active Jobs");
  });
});
