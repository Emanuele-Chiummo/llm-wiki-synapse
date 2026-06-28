// @ts-check
import js from "@eslint/js";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactPlugin from "eslint-plugin-react";
import reactHooksPlugin from "eslint-plugin-react-hooks";
import prettierConfig from "eslint-config-prettier";
import globals from "globals";

/**
 * INVARIANT I2 ENFORCEMENT: The rule below flags any import of client-side
 * layout libraries. These are FORBIDDEN per ADR-0015 and constitute a P0 block.
 *
 * Forbidden packages (client-side layout / physics — must NEVER appear in frontend/):
 *   - graphology-layout-forceatlas2
 *   - d3-force
 *   - @antv/layout
 *   - Any sigma ForceAtlas2 supervisor/worker
 *
 * The bundle-grep test (tests/no-client-layout.test.ts) provides a second,
 * independent enforcement layer that scans the built dist/ output.
 */
const NO_CLIENT_LAYOUT_RULE = {
  "no-restricted-imports": [
    "error",
    {
      patterns: [
        {
          group: ["graphology-layout-forceatlas2", "graphology-layout-forceatlas2/*"],
          message:
            "[I2/ADR-0015] Client-side FA2 layout is FORBIDDEN. Layout is server-side only (GET /graph returns precomputed coords).",
        },
        {
          group: ["d3-force", "d3-force/*"],
          message:
            "[I2/ADR-0015] d3-force is FORBIDDEN on the client. Layout is server-side only.",
        },
        {
          group: ["@antv/layout", "@antv/layout/*"],
          message:
            "[I2/ADR-0015] @antv/layout is FORBIDDEN on the client. Layout is server-side only.",
        },
        {
          group: ["graphology-layout", "graphology-layout/*"],
          message:
            "[I2/ADR-0015] graphology-layout is FORBIDDEN. Layout coords come from the server.",
        },
        {
          group: ["graphology-layout-random", "graphology-layout-random/*"],
          message:
            "[I2/ADR-0015] Random layout is FORBIDDEN. Assign server x/y directly.",
        },
        {
          group: ["graphology-layout-circular", "graphology-layout-circular/*"],
          message:
            "[I2/ADR-0015] Circular layout is FORBIDDEN. Assign server x/y directly.",
        },
      ],
    },
  ],
};

export default [
  // Base JS recommended
  js.configs.recommended,

  // TypeScript source files (non-test)
  {
    files: ["src/**/*.ts", "src/**/*.tsx"],
    ignores: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/**/*.spec.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      // Browser + browser-specific globals for source files
      globals: {
        ...globals.browser,
        __DEV__: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
    },
    rules: {
      // TypeScript strict rules
      ...tsPlugin.configs["recommended"].rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/explicit-module-boundary-types": "off",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-non-null-assertion": "warn",

      // React
      ...reactPlugin.configs.recommended.rules,
      "react/react-in-jsx-scope": "off", // React 19 JSX transform
      "react/prop-types": "off", // TypeScript handles this

      // React Hooks
      ...reactHooksPlugin.configs.recommended.rules,

      // I2/ADR-0015: Forbid client-side layout imports (P0 block)
      ...NO_CLIENT_LAYOUT_RULE,

      // General quality
      "no-console": ["warn", { allow: ["warn", "error", "assert"] }],
    },
    settings: {
      react: { version: "19" },
    },
  },

  // Test files — relax some rules, add Node + browser globals, allow non-null assertions
  {
    files: ["src/**/*.test.ts", "src/**/*.test.tsx", "src/**/*.spec.ts", "tests/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
      },
      globals: {
        ...globals.browser,
        ...globals.node,
        __DEV__: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
    },
    rules: {
      ...tsPlugin.configs["recommended"].rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // Allow non-null assertions in tests (accessing array elements with !)
      "@typescript-eslint/no-non-null-assertion": "off",
      "no-console": "off",
    },
  },

  // Prettier must be last to override formatting rules
  prettierConfig,
];
