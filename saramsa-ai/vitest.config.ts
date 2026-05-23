import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tsconfigPaths from 'vite-tsconfig-paths'

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    css: true,
    // Keep vitest out of the Playwright e2e directory. Playwright specs
    // use `@playwright/test`'s own runner (driven by playwright.config.ts);
    // running them through vitest crashes with "Playwright Test did not
    // expect test.describe() to be called here."
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/.next/**',
      'e2e/**',
    ],
    coverage: {
      // v8 is the built-in Node coverage provider; no extra dependency
      // beyond @vitest/coverage-v8 (in devDependencies). Faster than the
      // istanbul provider for large codebases.
      provider: 'v8',
      // What output formats to emit:
      //   text   — prints a coverage summary table to the CI log so you
      //            can see the numbers without downloading anything
      //   html   — interactive coverage browser in coverage/index.html;
      //            uploaded as a GH Actions artifact
      //   lcov   — standard format Codecov / SonarCloud can ingest if we
      //            ever swap to a hosted service
      reporter: ['text', 'html', 'lcov'],
      // Only instrument the files we've actually written tests for —
      // including Dashboard.tsx or the analysis-pipeline code (zero
      // tests today) would drag the average down and create noise.
      // As more tests get added in Phase 6, expand this list.
      include: [
        'src/lib/auth.ts',
        'src/lib/apiRequest.ts',
        'src/lib/useAuth.ts',
        'src/lib/workItemPrioritySort.ts',
        'src/app/login/**/*.{ts,tsx}',
        'src/store/features/auth/**/*.ts',
      ],
      // Exclude the test files themselves from the coverage calculation
      // (a test file's lines are "covered" by their own execution, which
      // is meaningless).
      exclude: [
        '**/*.test.{ts,tsx}',
        '**/*.spec.{ts,tsx}',
      ],
      // Thresholds = 0 means "report the number but don't fail the build
      // if it drops." We chose 0% on day 1 because raising it from "tests
      // exist" to "70% of all auth code is tested" takes time. Bump these
      // up as the team writes more tests.
      thresholds: {
        lines: 0,
        functions: 0,
        branches: 0,
        statements: 0,
      },
    },
  },
})
