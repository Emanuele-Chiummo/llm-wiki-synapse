/**
 * Vitest setup — runs before each test file.
 *
 * Sets global __DEV__ = true so dev-mode assertions in source code activate
 * during tests (matching the expected runtime behaviour).
 *
 * Also stubs window.matchMedia — jsdom does not implement it; components that
 * use it for prefers-reduced-motion checks (StatusBadge, ResearchStatusBadge)
 * will throw without this stub.
 */

// Make __DEV__ available globally in test environment
(globalThis as Record<string, unknown>)["__DEV__"] = true;

// Stub window.matchMedia for jsdom (not implemented in jsdom).
// Returns a safe MediaQueryList-like object where .matches is always false.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
