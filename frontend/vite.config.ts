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
        target: process.env["VITE_API_BASE"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/pages": {
        target: process.env["VITE_API_BASE"] ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  // VITE_API_BASE is injected via import.meta.env at build time.
  // Default: http://localhost:8000 — never hardcode secrets here.
  define: {
    __DEV__: JSON.stringify(process.env["NODE_ENV"] !== "production"),
  },
});
