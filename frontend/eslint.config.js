/**
 * ESLint config (`npm run lint`, run by `make lint` and CI).
 *
 * - js + typescript-eslint recommended rules for TS/TSX.
 * - react-hooks: catches broken hook usage (missing deps, hooks in conditions).
 * - react-refresh: keeps files hot-reload friendly.
 * - Generated and build output is ignored (src/api/types.ts is written by `make types`).
 */
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "dev-dist", "src/api/types.ts"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat["recommended-latest"],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
  },
]);
