import { defineConfig } from "vite";
import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as {
  version: string;
};
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/**
 * Content Security Policy for the Vite dev server and preview server (ADR-0087 / SEC-CSP-1).
 *
 * This header is served by Vite in development and CI (``vite preview`` mode).
 * Production uses the same directives via nginx.conf.template; see that file for per-directive
 * rationale.
 *
 * Dev/preview divergence from production nginx policy:
 *   connect-src adds ``http://localhost:* ws://localhost:* wss://localhost:*`` — in CI the
 *   frontend is served at :5173 and the API at :8000 (different origins), so cross-origin
 *   fetch/XHR/SSE calls to localhost:8000 would otherwise be blocked by ``connect-src 'self'``.
 *   In production nginx, the API is proxied on the same origin and ``'self'`` is sufficient.
 *
 * style-src 'unsafe-inline' explanation:
 *   Required by three independent sources: (1) the inline <style> block in index.html,
 *   (2) KaTeX's HTML+MathML output which sets inline ``style`` attributes on every rendered span,
 *   (3) React's inline style={{}} props used extensively in the app (react-resizable-panels,
 *   type-color badges, etc.). Removing unsafe-inline would require significant refactoring; it
 *   is accepted because style-src unsafe-inline does NOT enable JavaScript execution.
 */
const _DEV_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data: blob:",
  // Broader connect-src for dev/CI: allow cross-origin localhost (frontend :5173, API :8000).
  "connect-src 'self' http://localhost:* ws://localhost:* wss://localhost:*",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
].join("; ");

/**
 * API path prefixes that MUST bypass the service worker cache (NetworkOnly).
 * Any request whose URL pathname starts with one of these is never served
 * from cache — data freshness is mandatory (I1 dataVersion model).
 *
 * Keeps in sync with the dev-proxy block below: every proxied path is also
 * listed here so the SW never intercepts it.
 */
const API_PREFIXES = [
  `/graph`,
  `/pages`,
  `/status`,
  `/ingest`,
  `/provider`,
  `/conversations`,
  `/chat`,
  `/import-schedule`,
  `/config`,
  `/mcp`,
  `/research`,
  `/review`,
  `/search`,
  `/lint`,
  `/clip`,
  `/web-search`,
  `/sources`,
  `/overview`,
  `/links`,
  `/scenarios`,
  `/costs`,
  `/stats`,
  `/ops`,
  `/vault`,
  `/projects`,
  `/health`,
  `/export`,
  `/api`,
  // 2.1.6 (ADR-0090): MCP OAuth 2.1/PKCE authorization server (app.mcp.oauth) — root-level
  // paths, not under /mcp, matching claude.ai's observed fallback convention (see ADR-0090
  // §2). Must be proxied to the backend like every other API prefix, else nginx/vite's SPA
  // fallback swallows them into index.html (the exact bug that surfaced live).
  `/authorize`,
  `/token`,
  `/register`,
  `/.well-known`,
];

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // "generateSW" lets Workbox generate the SW from our config.
      // The plugin writes sw.js + workbox-*.js into dist/ at build time.
      strategies: "generateSW",

      // The SW file name that will be emitted and registered.
      filename: "sw.js",

      // Register the SW automatically from the generated registerSW shim.
      // We gate actual registration to production in main.tsx (see below).
      registerType: "autoUpdate",

      // Include the generated registerSW helper so main.tsx can import it.
      injectRegister: null, // we handle registration ourselves in main.tsx

      // Manifest is served from public/manifest.webmanifest directly; we
      // do NOT ask the plugin to inject/generate one (avoids duplicate).
      manifest: false,
      manifestFilename: "manifest.webmanifest",

      workbox: {
        // --- Precache: the built static shell (JS/CSS/HTML/icons) --------
        // globPatterns covers the emitted assets in dist/.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff,woff2}"],

        // Navigation fallback: when the user navigates to any path that
        // is NOT an API path and is NOT a precached file, serve index.html
        // from cache (offline shell).  The denylist excludes all API paths
        // so deep-links into the API never get the HTML fallback.
        navigateFallback: "index.html",
        navigateFallbackDenylist: API_PREFIXES.map(
          (p) => new RegExp(`^${p.replace("/", "\\/")}(\\/|$)`),
        ),

        // --- Runtime caching -----------------------------------------------
        runtimeCaching: [
          // API calls — NetworkOnly: never cache; always go to the network.
          // This protects the dataVersion / I1 invariant (no stale data).
          // We use a RegExp (not a closure) so Workbox can serialise it into
          // the generated SW without losing the variable reference.
          {
            // Matches any URL whose pathname starts with one of the API prefixes.
            urlPattern: new RegExp(
              `^(${API_PREFIXES.map((p) => p.replace("/", "\\/")).join("|")})(\\/|$)`,
            ),
            handler: "NetworkOnly" as const,
          },
        ],

        // Skip waiting and claim clients immediately on SW update so the
        // user always runs the latest shell.
        skipWaiting: true,
        clientsClaim: true,

        // Keep the SW source map for debugging; minify for production.
        sourcemap: false,
      },
    }),
  ],
  server: {
    port: Number(process.env["PORT"]) || 5173,
    // Stamp the CSP on every dev-server response (ADR-0087 / SEC-CSP-1).
    headers: {
      "Content-Security-Policy": _DEV_CSP,
    },
    proxy: {
      // In dev, proxy /graph and /pages/* to the FastAPI backend
      "/graph": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/pages": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/status": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/ingest": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/provider": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/conversations": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/chat": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/import-schedule": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/config": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/mcp": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/research": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/review": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/search": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/clip": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/web-search": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/lint": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/sources": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/overview": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/links": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/scenarios": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/costs": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/stats": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/ops": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/vault": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/projects": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/export": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      // 2.1.6 (ADR-0090): MCP OAuth 2.1/PKCE authorization server (app.mcp.oauth).
      "/authorize": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/token": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/register": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/.well-known": {
        target: process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  // Stamp the CSP on every vite preview response (used in CI E2E — ADR-0087 / SEC-CSP-1).
  // port is intentionally NOT set here: CI passes --port 5173 on the CLI.
  preview: {
    headers: {
      "Content-Security-Policy": _DEV_CSP,
    },
  },

  // VITE_API_BASE: browser base (relative by default, inlined by Vite at build time).
  // BACKEND_PROXY_TARGET: Vite dev proxy target (server-only, NOT inlined).
  // Never hardcode secrets here.
  define: {
    __DEV__: JSON.stringify(process.env["NODE_ENV"] !== "production"),
    // App version from package.json — single source of truth for the UI
    // (Header badge, Settings → About, ConnectScreen footer). Bumped per release.
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  build: {
    rollupOptions: {
      output: {
        /**
         * P4 vendor chunking — splits large vendors so they are independently
         * cached by the browser and paired with code-splitting (P1 lazy views):
         *
         *   vendor-react  → react + react-dom + scheduler (rarely changes)
         *   vendor-graph  → sigma + graphology* (lazy-loaded with GraphPanel)
         *   vendor-editor → @codemirror/* + @lezer/* (lazy-loaded with NoteView/PanelGroup)
         *
         * INVARIANT I2: no layout packages may appear in the graph chunk — the
         * no-client-layout bundle test (AC-FE-2) catches any violation.
         * Dev server is unaffected (manualChunks applies to build only).
         */
        manualChunks(id: string) {
          // React core — stable, high-cache-value chunk
          if (
            id.includes("/node_modules/react/") ||
            id.includes("/node_modules/react-dom/") ||
            id.includes("/node_modules/scheduler/") ||
            id.includes("/node_modules/react-is/")
          ) {
            return "vendor-react";
          }
          // sigma.js + graphology* — heavy WebGL/graph libraries (lazy with GraphPanel)
          if (id.includes("/node_modules/sigma/") || id.includes("/node_modules/graphology")) {
            return "vendor-graph";
          }
          // CodeMirror + Lezer parser — heavy editor libraries (lazy with PanelGroup/NoteView)
          if (
            id.includes("/node_modules/@codemirror/") ||
            id.includes("/node_modules/codemirror/") ||
            id.includes("/node_modules/@lezer/")
          ) {
            return "vendor-editor";
          }
        },
      },
    },
  },
});
