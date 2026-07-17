import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Allow underscore-prefixed unused vars (standard convention for
  // intentionally-unused destructured args or params).
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored copy-pasted components from shadcn / AI Elements registries.
    // These are upstream code we don't own; lint them upstream, not here.
    "src/components/ai-elements/**",
    // E2E tests are plain Node scripts, not part of the Next build.
    "tests/e2e/**",
  ]),
]);

export default eslintConfig;
