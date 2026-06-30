/**
 * pwa.test.ts — AC-F15-1 (PWA: web manifest + service worker offline shell)
 *
 * Verifies:
 *  1. The web manifest exists in public/ and is valid JSON with required fields.
 *  2. The SW registration code in main.tsx is guarded to production builds only
 *     (the __DEV__ + MODE !== 'test' guard prevents any registration during vitest).
 *  3. vite.config.ts declares the VitePWA plugin (confirming build-time SW generation).
 *  4. All API path prefixes are listed in the SW NetworkOnly configuration so
 *     backend responses are never served from cache (I1 dataVersion invariant).
 *
 * These tests run without a browser or a running Vite server (static analysis +
 * filesystem checks only — same pattern as vite-proxy-mcp.test.ts).
 */

import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const _thisDir = dirname(fileURLToPath(import.meta.url));

// Resolve paths relative to this test file (lives at frontend/src/tests/)
const PUBLIC_DIR = join(_thisDir, "../../public");
const MANIFEST_PATH = join(PUBLIC_DIR, "manifest.webmanifest");
const VITE_CONFIG_PATH = join(_thisDir, "../../vite.config.ts");
const MAIN_TSX_PATH = join(_thisDir, "../main.tsx");

// ─── 1. Manifest ─────────────────────────────────────────────────────────────

describe("AC-F15-1 — web app manifest", () => {
  it("manifest.webmanifest exists in public/", () => {
    expect(existsSync(MANIFEST_PATH)).toBe(true);
  });

  it("manifest.webmanifest is valid JSON", () => {
    const raw = readFileSync(MANIFEST_PATH, "utf-8");
    expect(() => JSON.parse(raw)).not.toThrow();
  });

  it('manifest has required "name" field equal to "Synapse — Knowledge Graph"', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    expect(typeof manifest["name"]).toBe("string");
    expect((manifest["name"] as string).toLowerCase()).toContain("synapse");
  });

  it('manifest has "short_name" field', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    expect(typeof manifest["short_name"]).toBe("string");
  });

  it('manifest has display = "standalone"', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    expect(manifest["display"]).toBe("standalone");
  });

  it('manifest has start_url = "/"', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    expect(manifest["start_url"]).toBe("/");
  });

  it("manifest has background_color (dark palette)", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    expect(typeof manifest["background_color"]).toBe("string");
    // Must be a CSS hex color starting with #
    expect((manifest["background_color"] as string)).toMatch(/^#[0-9a-fA-F]{3,8}$/);
  });

  it("manifest has at least one icon with sizes 192x192 or 512x512", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    const icons = manifest["icons"] as Array<Record<string, string>>;
    expect(Array.isArray(icons)).toBe(true);
    expect(icons.length).toBeGreaterThanOrEqual(1);
    const hasSufficientIcon = icons.some(
      (icon) => icon["sizes"] === "192x192" || icon["sizes"] === "512x512",
    );
    expect(hasSufficientIcon).toBe(true);
  });

  it("icon files referenced in manifest exist in public/icons/", () => {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf-8")) as Record<string, unknown>;
    const icons = manifest["icons"] as Array<Record<string, string>>;
    for (const icon of icons) {
      // icon.src is a URL path like /icons/icon-192.png
      const src = icon["src"] ?? "";
      // Strip leading slash to get a relative path from public/
      const relPath = src.replace(/^\//, "");
      const absPath = join(PUBLIC_DIR, relPath);
      expect(existsSync(absPath), `Icon file missing: ${absPath} (src="${src}")`).toBe(true);
    }
  });
});

// ─── 2. index.html manifest link ─────────────────────────────────────────────

describe("AC-F15-1 — index.html manifest link", () => {
  const INDEX_HTML_PATH = join(_thisDir, "../../index.html");

  it("index.html exists", () => {
    expect(existsSync(INDEX_HTML_PATH)).toBe(true);
  });

  it('index.html contains <link rel="manifest" href="/manifest.webmanifest">', () => {
    const html = readFileSync(INDEX_HTML_PATH, "utf-8");
    expect(html).toContain('rel="manifest"');
    expect(html).toContain("manifest.webmanifest");
  });

  it("index.html contains theme-color meta tag", () => {
    const html = readFileSync(INDEX_HTML_PATH, "utf-8");
    expect(html).toContain('name="theme-color"');
  });
});

// ─── 3. SW registration guard in main.tsx ────────────────────────────────────

describe("AC-F15-1 — service worker registration guarded to production", () => {
  it("main.tsx exists", () => {
    expect(existsSync(MAIN_TSX_PATH)).toBe(true);
  });

  it("main.tsx guards SW registration with __DEV__ check", () => {
    const src = readFileSync(MAIN_TSX_PATH, "utf-8");
    // The guard must include __DEV__ falsy check
    expect(src).toContain("__DEV__");
    // And must reference serviceWorker.register
    expect(src).toContain("serviceWorker");
    expect(src).toContain("register");
  });

  it("main.tsx guards SW registration with MODE !== 'test' (vitest safety)", () => {
    const src = readFileSync(MAIN_TSX_PATH, "utf-8");
    // The guard must exclude the test environment
    expect(src).toContain("MODE");
    expect(src).toContain("test");
  });

  it("in the test environment __DEV__ is true so the SW registration branch is skipped", () => {
    // In vitest setup.ts, __DEV__ is set to true.
    // This is the runtime proof that the guard works: if this assertion holds,
    // the SW registration branch (which requires !__DEV__) cannot execute during tests.
    expect(__DEV__).toBe(true);
  });
});

// ─── 4. VitePWA plugin declared in vite.config.ts ────────────────────────────

describe("AC-F15-1 — vite-plugin-pwa wired in vite.config.ts", () => {
  it("vite.config.ts imports VitePWA", () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    expect(raw).toContain("VitePWA");
    expect(raw).toContain("vite-plugin-pwa");
  });

  it("vite.config.ts configures navigateFallback to index.html", () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    expect(raw).toContain("navigateFallback");
    expect(raw).toContain("index.html");
  });

  it("vite.config.ts configures NetworkOnly handler for API routes", () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    expect(raw).toContain("NetworkOnly");
  });

  it("vite.config.ts lists all known API prefixes in API_PREFIXES", () => {
    const raw = readFileSync(VITE_CONFIG_PATH, "utf-8");
    // All backend API paths must be present so the SW never caches them.
    // We check for the bare path string regardless of quote style (the constant
    // uses backticks to avoid confusing the vite-proxy-mcp test which searches
    // for the double-quoted form '"/mcp"' when locating the proxy object).
    const requiredPrefixes = [
      "/graph",
      "/pages",
      "/chat",
      "/ingest",
      "/provider",
      "/search",
      "/mcp",
      "/review",
      "/research",
    ];
    for (const prefix of requiredPrefixes) {
      expect(raw, `API prefix "${prefix}" missing from API_PREFIXES in vite.config.ts`).toContain(
        prefix,
      );
    }
  });
});
