#!/usr/bin/env node
/**
 * copy-changelog.mjs — prebuild/predev lifecycle step.
 *
 * Copies the repo-root CHANGELOG.md into frontend/public/ so Vite picks it up
 * as a static asset served at /CHANGELOG.md in both the dev server and the
 * nginx production build (dist/).
 *
 * Deployment safety:
 *   - Dev  : Vite serves public/ verbatim; predev hook runs this before `vite`
 *            starts, so the file is there when the dev server initialises.
 *   - Prod : `vite build` copies public/ into dist/; nginx serves dist/ as
 *            document root, so GET /CHANGELOG.md returns the file directly via
 *            `try_files $uri …` — no API proxy entry needed.
 *
 * The script exits 0 silently if CHANGELOG.md is not found (CI safety).
 */

import { copyFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Repo root CHANGELOG.md (one level above frontend/)
const src  = resolve(__dirname, "../../CHANGELOG.md");
// frontend/public/CHANGELOG.md — served as /CHANGELOG.md
const dest = resolve(__dirname, "../public/CHANGELOG.md");

if (!existsSync(src)) {
  console.warn("[copy-changelog] CHANGELOG.md not found at", src, "— skipping.");
  process.exit(0);
}

await mkdir(dirname(dest), { recursive: true });
await copyFile(src, dest);
console.log("[copy-changelog] Copied CHANGELOG.md → public/CHANGELOG.md");
