import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    __DEV__: true,
    __APP_VERSION__: JSON.stringify("0.0.0-test"),
  },
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    // Exclude Playwright E2E specs (those use @playwright/test, not vitest)
    exclude: ["**/node_modules/**", "**/e2e/**", "**/*.spec.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      exclude: ["**/node_modules/**", "**/dist/**", "**/tests/**", "**/e2e/**"],
    },
  },
});
