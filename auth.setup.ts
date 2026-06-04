import { test as setup, expect } from '@playwright/test';
import path from 'path';

const authFile = path.join(__dirname, 'playwright/.auth/user.json');

/**
 * Authentication setup - runs once to login and save cookies
 * All other tests will reuse this auth state
 */
setup('authenticate', async ({ page }) => {
  console.log('\n🔐 AUTHENTICATION SETUP');
  console.log('='.repeat(50));

  // Try going to dashboard first - if already logged in, this will work
  console.log('📍 Checking if already authenticated...');
  await page.goto('http://localhost:3001/dashboard');
  await page.waitForTimeout(3000);

  // Check if we're actually on dashboard (not redirected to login)
  const currentUrl = page.url();

  if (currentUrl.includes('/dashboard') || currentUrl.includes('/projects')) {
    console.log('✅ Already authenticated!');
    console.log(`📍 At: ${currentUrl}`);
  } else if (currentUrl.includes('/auth/') || currentUrl.includes('/login')) {
    console.log('🔐 Not authenticated - logging in...');

    // Wait for login form
    await page.waitForSelector('input[type="email"], input[name="email"]', { timeout: 10000 });

    // Fill credentials
    await page.fill('input[type="email"], input[name="email"]', 'rakeshmahendran99@gmail.com');
    await page.fill('input[type="password"], input[name="password"]', 'R@kesh99');

    console.log('✅ Credentials entered');

    // Click login
    const loginButton = page.locator('button:has-text("Sign In"), button:has-text("Login"), button[type="submit"]').first();
    await loginButton.click();

    console.log('🔄 Logging in...');

    // Wait for redirect
    await page.waitForURL(/\/(config|integration|onboarding|dashboard|projects)/, { timeout: 15000 });

    console.log('✅ Login successful!');
    console.log(`📍 Redirected to: ${page.url()}`);
  }

  // Check if we're on config/integration page
  const finalUrl = page.url();
  const onConfigPage = finalUrl.includes('/config') ||
                       finalUrl.includes('/integration') ||
                       finalUrl.includes('/onboarding');

  if (onConfigPage) {
    console.log('📋 On config/integration page - looking for skip button...');

    // Wait a bit for page to load
    await page.waitForTimeout(2000);

    // Look for skip button
    const skipButton = page.locator(
      'button:has-text("Skip"), button:has-text("Later"), a:has-text("Skip"), button:has-text("Skip for now")'
    ).first();

    const hasSkip = await skipButton.count() > 0;

    if (hasSkip) {
      await skipButton.click();
      console.log('✅ Clicked skip button');

      // Wait for navigation to dashboard
      await page.waitForURL(/\/(dashboard|projects)/, { timeout: 10000 });
      console.log(`📍 Now at: ${page.url()}`);
    } else {
      console.log('⚠️  No skip button found - trying direct navigation');
      await page.goto('http://localhost:3001/dashboard');
      await page.waitForLoadState('networkidle');
    }
  }

  console.log(`📍 Final URL: ${page.url()}`);

  // Save authenticated state
  await page.context().storageState({ path: authFile });

  console.log(`💾 Auth state saved to: ${authFile}`);
  console.log('\n✅ Setup complete - all tests will now reuse this login!\n');
});
