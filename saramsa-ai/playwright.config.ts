/**
 * Playwright config — drives the end-to-end tests under saramsa-ai/e2e/.
 *
 * Design choice: tests mock the backend via `page.route()` rather than
 * spinning up the real Django server. Reasons:
 *
 *   1. CI: provisioning Django + Postgres + Redis just to log a fake
 *      user in is expensive (~2 min added to a 5 min CI run). E2E
 *      coverage of the full stack is the job of QA, not unit CI.
 *
 *   2. Determinism: real backend calls introduce flakes from clock
 *      skew, DB state leakage, and concurrent test races. The
 *      stubbed responses are fixed shapes pinned to the real backend
 *      contract — if the contract drifts, the corresponding backend
 *      unit test should catch it.
 *
 *   3. Speed: a stubbed E2E run is ~5s; a real-stack run is ~30s+.
 *
 * What this config tests therefore: the Next.js frontend's behavior
 * end-to-end against a fixed backend contract. Login form, redirect,
 * token persistence, navigation, logout — all driven by a real browser.
 */

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // Each test is its own context — no shared cookies, localStorage, etc.
  // Critical for auth tests: a logged-in state from test A must NOT
  // leak into test B.
  use: {
    baseURL: 'http://localhost:3001',
    headless: true,
    // Capture trace on failure so debugging a flaky CI run is possible
    // without rerunning. `retain-on-failure` keeps traces for failed
    // tests and discards them for passing ones (cheap on storage).
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  // Auto-boot the Next.js dev server before running tests. Reused
  // across test workers within a single `npm run test:e2e` invocation.
  webServer: {
    command: 'npm run dev',
    port: 3001,
    // Reuse an existing server if the dev is already running locally.
    // CI starts fresh each run so this defaults to "always start one."
    reuseExistingServer: !process.env.CI,
    // Next.js dev mode can take 30+ seconds to compile on first hit;
    // give it room.
    timeout: 120_000,
  },
  // Chromium only for the first cut. Adding Firefox + WebKit doubles
  // CI time for little additional signal — UI compatibility issues are
  // better caught by manual smoke checks.
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // No retries locally (fail fast for fast iteration). Two retries in
  // CI to absorb the occasional timing flake; if a test fails three
  // times, it's a real bug.
  retries: process.env.CI ? 2 : 0,
  // One worker locally for predictable test ordering during debugging.
  // CI runs in parallel.
  workers: process.env.CI ? 2 : 1,
  reporter: process.env.CI ? [['html'], ['github']] : 'list',
})
