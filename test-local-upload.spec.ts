import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

/**
 * Local upload test - uses saved auth state from auth.setup.ts
 * Tests: Upload → Monitor states → Check delete bug
 */

test('Local: Upload and monitor state machine', async ({ page }) => {
  console.log('\n🚀 Starting LOCAL upload test');
  console.log('Using saved authentication state');

  // Track state changes
  const observedStates: string[] = [];
  const apiCalls: string[] = [];

  page.on('response', async (response) => {
    const url = response.url();

    if (url.includes('/api/')) {
      const shortUrl = url.split('/api')[1];
      apiCalls.push(`${response.request().method()} ${shortUrl} → ${response.status()}`);

      if (url.includes('/feedback/history') || url.includes('/insights/tasks')) {
        console.log(`📡 ${response.request().method()} ${shortUrl} → ${response.status()}`);
      }
    }
  });

  // Navigate to dashboard (use your local project URL)
  console.log('\n📍 Step 1: Navigate to dashboard');
  await page.goto('http://localhost:3001');

  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);

  console.log('   ✅ Page loaded');

  // Check if we're on dashboard
  const currentUrl = page.url();
  console.log(`   Current URL: ${currentUrl}`);

  // If not on project dashboard, try to navigate
  if (!currentUrl.includes('/projects/') || !currentUrl.includes('/dashboard')) {
    console.log('   ℹ️  Not on project dashboard yet');

    // Click "Go to Analysis" button on first project card
    const goToAnalysisBtn = page.locator('button:has-text("Go to Analysis")').first();
    const hasButton = await goToAnalysisBtn.count() > 0;

    if (hasButton) {
      await goToAnalysisBtn.click();
      await page.waitForTimeout(3000);
      console.log('   ✅ Clicked "Go to Analysis"');

      // Verify we're on dashboard now
      const newUrl = page.url();
      console.log(`   📍 Now at: ${newUrl}`);
    } else {
      console.log('   ⚠️  No "Go to Analysis" button found');
    }
  }

  // Use existing Data-30.json for faster testing (30 comments)
  console.log('\n📝 Step 2: Prepare test file');
  const testFilePath = path.join(process.cwd(), 'Saramsa-Data', 'Data-30.json');

  // Check if file exists
  if (!fs.existsSync(testFilePath)) {
    throw new Error(`Data-30.json not found at ${testFilePath}`);
  }

  console.log(`   ✅ Using: ${testFilePath} (30 comments)`);

  // Upload file
  console.log('\n📤 Step 3: Upload file');

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(testFilePath);
  console.log('   ✅ File selected (Data-30.json - 30 comments)');

  await page.waitForTimeout(1000);

  // File might auto-upload or need button click
  const uploadButton = page.locator('button:has-text("Upload"), button:has-text("Analyze")').first();
  const hasUploadButton = await uploadButton.count() > 0;

  if (hasUploadButton) {
    await uploadButton.click();
    console.log('   ✅ Upload button clicked');
  }

  // Monitor progress
  console.log('\n📊 Step 4: Monitor state machine transitions');

  let analyzing = true;
  let checkCount = 0;
  const maxChecks = 60; // 60 * 3s = 3 minutes (enough for Data30.json)

  console.log('   Monitoring state transitions...');

  while (analyzing && checkCount < maxChecks) {
    checkCount++;

    // Check for "Analyzing..." in sidebar
    const analyzingIndicator = await page.locator('text=/Analyzing|In Progress/').count() > 0;

    // Check ALL possible progress states
    const progressSelectors = [
      'text=/Queued/i',
      'text=/Reading file/i',
      'text=/Ingesting/i',
      'text=/Analyzing feedback/i',
      'text=/Processing/i',
      'text=/Generating insights/i',
      'text=/Synthesizing/i',
      'text=/Creating work items/i',
      'text=/Generating work items/i',
      'text=/Completed/i'
    ];

    for (const selector of progressSelectors) {
      const elem = page.locator(selector).first();
      const hasText = await elem.count() > 0;

      if (hasText) {
        const text = await elem.textContent().catch(() => null);
        if (text && observedStates[observedStates.length - 1] !== text) {
          observedStates.push(text);
          console.log(`   📍 STATE: ${text} (check ${checkCount}/${maxChecks})`);
        }
      }
    }

    if (!analyzingIndicator) {
      console.log('   ✅ Analysis completed!');
      analyzing = false;
      break;
    }

    await page.waitForTimeout(3000); // 3 seconds between checks
  }

  if (checkCount >= maxChecks) {
    console.log('   ⏱️  Timeout - still analyzing after 3 minutes');
  }

  // Check final state
  await page.waitForTimeout(2000);

  console.log('\n📊 Step 5: Verify results');

  // Count items in sidebar
  const sidebarItems = await page.locator('text=/Feedback Review|Analysis 202/').count();
  console.log(`   Sidebar items: ${sidebarItems}`);

  // Check if results displayed
  const hasResults = await page.locator('text=/Positive|Negative|sentiment/i').count() > 0;
  console.log(`   Results displayed: ${hasResults}`);

  // BONUS: Test delete
  console.log('\n🗑️  Step 6: Test delete');

  if (sidebarItems > 1) {
    console.log('   Testing delete on last item...');

    // Find all items
    const items = await page.locator('text=/Feedback Review|Analysis 202/').all();
    const lastItem = items[items.length - 1];
    const lastItemText = await lastItem.textContent();

    console.log(`   Target: "${lastItemText}"`);

    // Hover and click delete
    await lastItem.hover();
    await page.waitForTimeout(500);

    const deleteIcon = page.locator('[title="Delete"]').last();
    const hasDelete = await deleteIcon.count() > 0;

    if (hasDelete) {
      await deleteIcon.click();
      await page.waitForTimeout(500);

      // Confirm
      const confirmBtn = page.locator('button:has-text("Delete")').first();
      await confirmBtn.click();

      console.log('   ✅ Delete clicked');
      await page.waitForTimeout(2000);

      // Count and record deleted item text
      const itemsAfterDelete = await page.locator('text=/Feedback Review|Analysis 202/').count();
      console.log(`   Items after delete: ${itemsAfterDelete}`);
      console.log(`   Deleted item text: "${lastItemText}"`);

      // Check if deleted item is still visible
      const deletedItemStillVisible = await page.locator(`text="${lastItemText}"`).count() > 0;
      console.log(`   Deleted item visible after delete: ${deletedItemStillVisible ? '❌ YES' : '✅ NO'}`);

      // Now click another item
      console.log('   Clicking another item to check if deleted one reappears...');

      const remainingItems = await page.locator('text=/Feedback Review|Analysis 202/').all();
      if (remainingItems.length > 0) {
        // Click first item (NOT the last one we just deleted)
        await remainingItems[0].click();
        await page.waitForTimeout(3000);

        // Check if deleted item reappeared by TEXT (not just count)
        const deletedItemReappeared = await page.locator(`text="${lastItemText}"`).count() > 0;
        const itemsAfterClick = await page.locator('text=/Feedback Review|Analysis 202/').count();

        console.log(`   Items after clicking another: ${itemsAfterClick}`);
        console.log(`   Deleted item ("${lastItemText}") reappeared: ${deletedItemReappeared ? '🐛 YES (BUG!)' : '✅ NO'}`);

        if (deletedItemReappeared) {
          console.log('   🐛 BUG DETECTED: Deleted item REAPPEARED in the list!');

          // Try clicking the reappeared item
          console.log('   Testing: Click reappeared item...');
          const reappearedItem = page.locator(`text="${lastItemText}"`).first();
          await reappearedItem.click();
          await page.waitForTimeout(2000);

          // Check for "not found" error
          const hasError = await page.locator('text=/not found|404|does not exist/i').count() > 0;
          if (hasError) {
            console.log('   🐛 CONFIRMED: Clicking reappeared item shows "not found" error');
          }
        } else {
          console.log('   ✅ No bug: Deleted item stayed deleted');
        }
      }
    } else {
      console.log('   ⚠️  No delete button found');
    }
  } else {
    console.log('   ⏭️  Skipped (need at least 2 items)');
  }

  // Summary
  console.log('\n📋 SUMMARY:');
  console.log('━'.repeat(50));
  console.log('States observed:', observedStates.join(' → '));
  console.log('API calls:', apiCalls.length);
  console.log('Results displayed:', hasResults ? '✅' : '❌');

  console.log('\n✅ TEST COMPLETE!');
});
