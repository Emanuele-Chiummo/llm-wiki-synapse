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
 * The script attempts to find CHANGELOG.md by:
 * 1. Resolving relative to the script's own directory (../../CHANGELOG.md)
 * 2. Walking up to find a repo-root marker (.git, CLAUDE.md)
 * 3. Failing loudly in CI/prebuild contexts if the file doesn't exist
 *    (silent fallback only in genuine "no changelog" checkout scenarios)
 */

import { copyFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Try primary path first: ../../CHANGELOG.md from scripts/
let src = resolve(__dirname, "../../CHANGELOG.md");
let found = existsSync(src);

// If not found, walk up looking for repo-root markers (.git or CLAUDE.md)
if (!found) {
  let current = resolve(__dirname, "..");
  let depth = 0;
  const maxDepth = 10; // Prevent infinite loops

  while (depth < maxDepth) {
    const gitMarker = join(current, ".git");
    const claudeMarker = join(current, "CLAUDE.md");
    
    if (existsSync(gitMarker) || existsSync(claudeMarker)) {
      // Found the repo root, try for CHANGELOG.md there
      const potentialSrc = join(current, "CHANGELOG.md");
      if (existsSync(potentialSrc)) {
        src = potentialSrc;
        found = true;
        break;
      }
    }

    // Walk up one level
    const parent = dirname(current);
    if (parent === current) {
      // Reached filesystem root
      break;
    }
    current = parent;
    depth++;
  }
}

// Handle the result
if (!found) {
  const isCI = process.env.CI === "true" || process.env.CI === "1";
  const isPrebuild = process.env.npm_lifecycle_event === "prebuild";
  
  console.warn(
    `[copy-changelog] CHANGELOG.md not found. Searched: ${src}`
  );

  // In CI prebuild contexts (like desktop-release.yml), fail loudly
  // to prevent shipping a build with missing changelog.
  if (isCI && isPrebuild) {
    console.error(
      "[copy-changelog] CI prebuild: CHANGELOG.md is required for release builds."
    );
    process.exit(1);
  }

  // In other contexts (dev, or CI without prebuild), exit gracefully
  console.warn(
    "[copy-changelog] Skipping copy (this is OK for development or non-release contexts)."
  );
  process.exit(0);
}

// Copy the file
const dest = resolve(__dirname, "../public/CHANGELOG.md");

try {
  await mkdir(dirname(dest), { recursive: true });
  await copyFile(src, dest);
  console.log(`[copy-changelog] Copied ${src} → public/CHANGELOG.md`);
} catch (err) {
  console.error(
    `[copy-changelog] Failed to copy: ${err.message}`
  );
  process.exit(1);
}
