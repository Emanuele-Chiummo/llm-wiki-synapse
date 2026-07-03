import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    __DEV__: true,
    // High sentinel so tests can mock a backend BEHIND the app (the mismatch
    // banner fires only in that direction — R12-3). "0.0.0-test" made every
    // backend look ahead, which can never trigger the banner.
    __APP_VERSION__: JSON.stringify("9.9.9-test"),
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
