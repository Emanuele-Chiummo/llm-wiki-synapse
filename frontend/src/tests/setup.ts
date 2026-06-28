/**
 * Vitest setup — runs before each test file.
 *
 * Sets global __DEV__ = true so dev-mode assertions in source code activate
 * during tests (matching the expected runtime behaviour).
 */

// Make __DEV__ available globally in test environment
(globalThis as Record<string, unknown>)["__DEV__"] = true;
