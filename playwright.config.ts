import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './',
  testMatch: ['**/*.spec.ts', '**/*.setup.ts'],
  testIgnore: ['**/node_modules/**', '**/saramsa-ai/**', '**/backend/**'],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 240000, // 4 minutes total timeout

  use: {
    baseURL: 'http://localhost:3001',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 30000, // 30s for individual actions
  },

  projects: [
    // Setup project - runs first to login and save auth
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
    },

    // Tests that use the saved auth state
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Use auth state from setup
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
    },
  ],
});
