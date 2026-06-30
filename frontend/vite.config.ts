import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
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
    },
  },
  // VITE_API_BASE: browser base (relative by default, inlined by Vite at build time).
  // BACKEND_PROXY_TARGET: Vite dev proxy target (server-only, NOT inlined).
  // Never hardcode secrets here.
  define: {
    __DEV__: JSON.stringify(process.env["NODE_ENV"] !== "production"),
  },
});
