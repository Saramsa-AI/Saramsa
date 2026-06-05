import { test, expect } from '@playwright/test';
import path from 'path';

/**
 * Complete E2E Flow Test:
 * 1. Login with credentials
 * 2. Skip integration page
 * 3. Go to dashboard
 * 4. Select project
 * 5. Upload file
 * 6. Monitor upload progress through all states
 */

test.describe('Full Upload Flow E2E', () => {
  test('Complete flow: Login → Dashboard → Upload → Monitor', async ({ page }) => {
    // Track all analysis state changes
    const stateChanges: Array<{ timestamp: string; state: string; source: string }> = [];

    // Monitor network requests
    const apiCalls: Array<{ method: string; url: string; status: number }> = [];

    page.on('response', async (response) => {
      const url = response.url();

      // Track API calls
      if (url.includes('/api/')) {
        apiCalls.push({
          method: response.request().method(),
          url: url.replace(/^.*\/api/, '/api'),
          status: response.status()
        });
      }

      // Monitor history/analysis endpoints
      if (url.includes('/feedback/history') || url.includes('/feedback/analysis') || url.includes('/insights/tasks')) {
        console.log(`📡 ${response.request().method()} ${url.split('/api')[1]} - ${response.status()}`);
      }
    });

    // ============================================================================
    // STEP 1: LOGIN
    // ============================================================================
    console.log('\n🔐 STEP 1: Login');
    // Use production for real data
    await page.goto('https://localhost:3001/auth/login');

    // Wait for login page to load
    await page.waitForSelector('input[type="email"], input[name="email"]', { timeout: 10000 });

    // Fill credentials
    const emailInput = page.locator('input[type="email"], input[name="email"]').first();
    const passwordInput = page.locator('input[type="password"], input[name="password"]').first();

    await emailInput.fill('rakeshmahendran99@gmail.com');
    await passwordInput.fill('R@kesh99');

    console.log('   ✅ Credentials entered');

    // Wait a bit for any client-side validation
    await page.waitForTimeout(1000);

    // Take screenshot before login
    await page.screenshot({ path: 'login-before.png' });

    // Click login button
    const loginButton = page.locator('button:has-text("Sign In"), button:has-text("Login"), button[type="submit"]').first();

    console.log(`   🔘 Login button visible: ${await loginButton.isVisible()}`);
    console.log(`   🔘 Login button enabled: ${await loginButton.isEnabled()}`);

    await loginButton.click();

    console.log('   🔄 Logging in...');

    // Wait a bit to see response
    await page.waitForTimeout(3000);

    // Take screenshot after clicking login
    await page.screenshot({ path: 'login-after.png' });

    // Check current URL
    const urlAfterClick = page.url();
    console.log(`   📍 URL after login click: ${urlAfterClick}`);

    // Check for error messages
    const errorMessage = await page.locator('text=/error|invalid|incorrect/i').first().textContent().catch(() => null);
    if (errorMessage) {
      console.log(`   ❌ Error message: ${errorMessage}`);
    }

    // Wait for navigation after login
    await page.waitForURL(/\/(dashboard|integration|projects|onboarding)/, { timeout: 15000 });

    const currentUrl = page.url();
    console.log(`   ✅ Logged in! Current URL: ${currentUrl}`);

    // ============================================================================
    // STEP 2: SKIP INTEGRATION (if shown)
    // ============================================================================
    console.log('\n⏭️  STEP 2: Handle integration page');

    await page.waitForTimeout(2000);

    // Check if we're on integration/onboarding page
    const onIntegrationPage = page.url().includes('integration') || page.url().includes('onboarding');

    if (onIntegrationPage) {
      console.log('   On integration page - looking for skip button');

      // Try multiple skip button variations
      const skipButton = page.locator(
        'button:has-text("Skip"), button:has-text("Later"), a:has-text("Skip"), [data-skip="true"]'
      ).first();

      const hasSkipButton = await skipButton.count() > 0;

      if (hasSkipButton) {
        await skipButton.click();
        console.log('   ✅ Clicked skip button');
        await page.waitForTimeout(2000);
      } else {
        // Try navigating directly to dashboard
        console.log('   No skip button found - navigating directly to dashboard');
        await page.goto('http://localhost:3001/dashboard');
      }
    } else {
      console.log('   ✅ Already past integration page');
    }

    // ============================================================================
    // STEP 3: GO TO DASHBOARD
    // ============================================================================
    console.log('\n📊 STEP 3: Navigate to dashboard');

    // If not already on dashboard, navigate
    if (!page.url().includes('dashboard')) {
      await page.goto('http://localhost:3001/dashboard');
    }

    await page.waitForLoadState('networkidle');
    console.log('   ✅ On dashboard');

    // ============================================================================
    // STEP 4: SELECT PROJECT
    // ============================================================================
    console.log('\n🎯 STEP 4: Select project');

    // Wait for projects to load
    await page.waitForTimeout(3000);

    // Look for project selector/dropdown
    const projectSelector = page.locator(
      'select, [role="combobox"], button:has-text("Select Project"), [data-project-selector]'
    ).first();

    const hasProjectSelector = await projectSelector.count() > 0;

    if (hasProjectSelector) {
      console.log('   Found project selector');
      await projectSelector.click();
      await page.waitForTimeout(1000);

      // Select first project option
      const firstProject = page.locator('[role="option"], option').first();
      await firstProject.click();

      console.log('   ✅ Project selected');
    } else {
      console.log('   ℹ️  No project selector found - may be auto-selected');

      // Check if we need to navigate to a specific project
      const currentUrl = page.url();
      if (!currentUrl.includes('/projects/')) {
        // Try to find and click first project card/link
        const projectCard = page.locator('a[href*="/projects/"], [data-project-card]').first();
        const hasProjectCard = await projectCard.count() > 0;

        if (hasProjectCard) {
          await projectCard.click();
          await page.waitForTimeout(2000);
          console.log('   ✅ Clicked project card');
        }
      }
    }

    // Verify we're on a project dashboard
    await page.waitForURL(/\/projects\/.*\/dashboard/, { timeout: 10000 });
    console.log(`   ✅ On project dashboard: ${page.url()}`);

    // ============================================================================
    // STEP 5: UPLOAD FILE
    // ============================================================================
    console.log('\n📤 STEP 5: Upload file');

    // Wait for upload panel to be ready
    await page.waitForTimeout(2000);

    // Create a test CSV file
    const testCsvPath = path.join(process.cwd(), 'test-upload.csv');
    const fs = require('fs');
    const testCsvContent = `comment,rating,date
"Great product, love it!",5,2024-01-01
"Terrible experience, very disappointed",1,2024-01-02
"It's okay, nothing special",3,2024-01-03
"Amazing! Highly recommend",5,2024-01-04
"Not worth the money",2,2024-01-05
"Best purchase ever!",5,2024-01-06
"Poor quality, broke after one use",1,2024-01-07
"Decent product for the price",4,2024-01-08
"Would not buy again",2,2024-01-09
"Exceeded my expectations!",5,2024-01-10`;

    fs.writeFileSync(testCsvPath, testCsvContent);
    console.log(`   ✅ Test CSV created: ${testCsvPath}`);

    // Find file input or browse button
    const fileInput = page.locator('input[type="file"]');
    const browseButton = page.locator('button:has-text("Browse")');

    const hasFileInput = await fileInput.count() > 0;
    const hasBrowseButton = await browseButton.count() > 0;

    if (hasBrowseButton) {
      console.log('   Found Browse button');
      // Some implementations hide the file input and use a button
      await fileInput.setInputFiles(testCsvPath);
    } else if (hasFileInput) {
      console.log('   Found file input');
      await fileInput.setInputFiles(testCsvPath);
    } else {
      console.log('   ❌ No file upload element found');
      throw new Error('Cannot find file upload input');
    }

    console.log('   ✅ File selected');

    // Wait a bit for file to be processed
    await page.waitForTimeout(1000);

    // Click upload/analyze button
    const uploadButton = page.locator(
      'button:has-text("Upload"), button:has-text("Analyze"), button:has-text("Start Analysis")'
    ).first();

    const hasUploadButton = await uploadButton.count() > 0;

    if (hasUploadButton) {
      await uploadButton.click();
      console.log('   ✅ Upload initiated!');
    } else {
      console.log('   ℹ️  No explicit upload button - may auto-upload');
    }

    // ============================================================================
    // STEP 6: MONITOR PROGRESS
    // ============================================================================
    console.log('\n📊 STEP 6: Monitor upload progress');

    // Expected state flow: QUEUED → INGESTING → ANALYZING → SYNTHESIZING → GENERATING_WORKITEMS → COMPLETED
    const expectedStates = ['QUEUED', 'INGESTING', 'ANALYZING', 'SYNTHESIZING', 'GENERATING_WORKITEMS', 'COMPLETED'];
    const observedStates: string[] = [];

    // Monitor sidebar for "Analyzing..." item
    let analyzing = true;
    let checkCount = 0;
    const maxChecks = 60; // 60 * 2s = 2 minutes max

    while (analyzing && checkCount < maxChecks) {
      checkCount++;

      // Check sidebar for "Analyzing..." indicator
      const analyzingItem = page.locator('text=/Analyzing|In Progress/').first();
      const isAnalyzing = await analyzingItem.count() > 0;

      // Check center panel for progress states
      const progressTexts = await page.locator('text=/Queued|Reading file|Analyzing feedback|Generating insights|Creating work items|Completed/').allTextContents();

      if (progressTexts.length > 0) {
        const currentState = progressTexts[0];
        if (observedStates[observedStates.length - 1] !== currentState) {
          observedStates.push(currentState);
          console.log(`   📍 State: ${currentState}`);
        }
      }

      if (!isAnalyzing) {
        analyzing = false;
        console.log('   ✅ Analysis completed!');
        break;
      }

      await page.waitForTimeout(2000);
    }

    if (checkCount >= maxChecks) {
      console.log('   ⚠️  Timeout: Analysis did not complete in 2 minutes');
    }

    // Wait for final state
    await page.waitForTimeout(3000);

    // ============================================================================
    // VERIFICATION
    // ============================================================================
    console.log('\n✅ VERIFICATION:');

    // Check if analysis is in completed list
    const completedAnalyses = await page.locator('text=/Feedback Review|Analysis 202/').count();
    console.log(`   Completed analyses in list: ${completedAnalyses}`);

    // Check if results are displayed
    const hasResults = await page.locator('text=/Positive|Negative|Neutral|sentiment/i').count() > 0;
    console.log(`   Results displayed: ${hasResults}`);

    // Check sidebar and center are in sync
    const sidebarItems = await page.locator('[role="button"]:has-text("Feedback Review"), [role="button"]:has-text("Analysis")').count();
    console.log(`   Sidebar items: ${sidebarItems}`);

    console.log('\n📊 OBSERVED STATES:');
    observedStates.forEach((state, i) => console.log(`   ${i + 1}. ${state}`));

    console.log('\n📡 API CALLS SUMMARY:');
    const ingestCalls = apiCalls.filter(c => c.url.includes('/ingest'));
    const historyCalls = apiCalls.filter(c => c.url.includes('/history'));
    const taskCalls = apiCalls.filter(c => c.url.includes('/tasks'));

    console.log(`   Ingest calls: ${ingestCalls.length}`);
    console.log(`   History calls: ${historyCalls.length}`);
    console.log(`   Task calls: ${taskCalls.length}`);

    // Assertions
    expect(hasResults, 'Results should be displayed after completion').toBe(true);
    expect(completedAnalyses, 'Should have at least one completed analysis').toBeGreaterThan(0);

    // Clean up test file
    fs.unlinkSync(testCsvPath);
    console.log('\n🧹 Test file cleaned up');

    console.log('\n✅ TEST COMPLETE!');
  });
});
