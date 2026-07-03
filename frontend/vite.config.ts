import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

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
  `/api`,
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
    port: 5173,
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
    },
  },
  // VITE_API_BASE: browser base (relative by default, inlined by Vite at build time).
  // BACKEND_PROXY_TARGET: Vite dev proxy target (server-only, NOT inlined).
  // Never hardcode secrets here.
  define: {
    __DEV__: JSON.stringify(process.env["NODE_ENV"] !== "production"),
  },
});
