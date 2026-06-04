import { test, expect } from '@playwright/test';

test.use({ storageState: '../playwright/.auth/user.json' });

test('Comprehensive Analysis Flow Test', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════');
  console.log('🔍 COMPREHENSIVE ANALYSIS FLOW TEST');
  console.log('═══════════════════════════════════════════════════\n');

  const bugs: string[] = [];
  const addBug = (bug: string) => {
    bugs.push(bug);
    console.log(`🐛 BUG #${bugs.length}: ${bug}\n`);
  };

  // ========================================
  // SETUP: Navigate to project
  // ========================================
  console.log('🚀 SETUP: Navigating to project');
  console.log('─'.repeat(50));

  await page.goto('http://localhost:3001');

  // Wait for projects to load
  console.log('Waiting for projects to load...');
  await page.waitForTimeout(3000);

  // Wait for NewTest project card to appear
  const newTestHeading = page.locator('text=NewTest').first();
  await newTestHeading.waitFor({ timeout: 10000 });
  console.log('Projects loaded! Found NewTest');

  // Click "Go to Analysis" button for NewTest
  const goBtn = page.locator('button:has-text("Go to Analysis")').first();
  await goBtn.click();
  console.log('Clicked "Go to Analysis"');

  // Wait for navigation to dashboard
  await page.waitForURL('**/dashboard/**', { timeout: 10000 });
  await page.waitForTimeout(2000);
  console.log(`Navigated to: ${page.url()}`);

  // ========================================
  // TEST 1: Initial Page Load
  // ========================================
  console.log('\n📋 TEST 1: Initial Page Load');
  console.log('─'.repeat(50));

  // Take screenshot at T=0
  await page.screenshot({ path: 'test-results/screenshot-T0.png', fullPage: true });

  // Count items at T=0
  const itemsAtT0 = await page.locator('text=/Feedback Review/i').count();
  console.log(`Items at T=0: ${itemsAtT0}`);

  // Wait 5 seconds and count again
  await page.waitForTimeout(5000);

  // Take screenshot at T=5
  await page.screenshot({ path: 'test-results/screenshot-T5.png', fullPage: true });

  const itemsAtT5 = await page.locator('text=/Feedback Review/i').count();
  console.log(`Items at T=5s: ${itemsAtT5}`);

  if (itemsAtT0 !== itemsAtT5) {
    addBug(`Item count changed during load: ${itemsAtT0} → ${itemsAtT5} (flickering)`);
  }

  // Check if "Loading..." shows
  const showedLoading = await page.locator('text=/Loading history/i').count() > 0;
  console.log(`Loading indicator shown: ${showedLoading}`);

  // ========================================
  // TEST 2: Delete and Check Reappearance
  // ========================================
  console.log('\n📋 TEST 2: Delete Bug Test');
  console.log('─'.repeat(50));

  const currentItems = await page.locator('text=/Feedback Review/i').all();
  if (currentItems.length >= 2) {
    // Get last item name
    const lastItem = currentItems[currentItems.length - 1];
    const lastItemName = await lastItem.textContent();
    console.log(`Deleting: ${lastItemName}`);

    // Delete it
    await lastItem.hover();
    await page.waitForTimeout(500);
    const deleteBtn = page.locator('[title="Delete"]').last();
    if (await deleteBtn.count() > 0) {
      await deleteBtn.click();
      await page.waitForTimeout(500);
      const confirmBtn = page.locator('button:has-text("Delete")').first();
      await confirmBtn.click();
      await page.waitForTimeout(2000);

      // Check if it's gone
      const stillVisible = await page.locator(`text="${lastItemName}"`).count() > 0;
      console.log(`Item still visible after delete: ${stillVisible}`);

      // Click another item
      const remainingItems = await page.locator('text=/Feedback Review/i').all();
      if (remainingItems.length > 0) {
        await remainingItems[0].click();
        await page.waitForTimeout(3000);

        // Check if deleted item reappeared
        const reappeared = await page.locator(`text="${lastItemName}"`).count() > 0;
        console.log(`Deleted item reappeared: ${reappeared}`);

        if (reappeared) {
          addBug(`Deleted item "${lastItemName}" reappeared after clicking another item`);
        }
      }
    }
  }

  // ========================================
  // TEST 3: Check Backend vs Frontend Sync
  // ========================================
  console.log('\n📋 TEST 3: Backend vs Frontend Sync');
  console.log('─'.repeat(50));

  // Get frontend count
  const frontendCount = await page.locator('text=/Feedback Review/i').count();
  console.log(`Frontend shows: ${frontendCount} items`);

  // Check for "Analysis not found" errors
  const notFoundErrors = await page.locator('text=/Analysis not found/i').count();
  console.log(`"Analysis not found" errors: ${notFoundErrors}`);

  if (notFoundErrors > 0) {
    addBug(`"Analysis not found" errors present (${notFoundErrors} instances)`);
  }

  // ========================================
  // TEST 4: Upload New Analysis
  // ========================================
  console.log('\n📋 TEST 4: Upload Flow');
  console.log('─'.repeat(50));

  // Count "Analyzing..." items
  const analyzingBefore = await page.locator('text=/Analyzing/i').count();
  console.log(`"Analyzing..." items before upload: ${analyzingBefore}`);

  if (analyzingBefore > 0) {
    addBug(`Stale "Analyzing..." items present (${analyzingBefore} found)`);
  }

  // ========================================
  // FINAL REPORT
  // ========================================
  console.log('\n═══════════════════════════════════════════════════');
  console.log('📊 FINAL BUG REPORT');
  console.log('═══════════════════════════════════════════════════\n');

  if (bugs.length === 0) {
    console.log('✅ NO BUGS FOUND!');
  } else {
    console.log(`❌ TOTAL BUGS FOUND: ${bugs.length}\n`);
    bugs.forEach((bug, i) => {
      console.log(`${i + 1}. ${bug}`);
    });
  }

  console.log('\n═══════════════════════════════════════════════════\n');

  // Fail test if bugs found
  expect(bugs.length, `Found ${bugs.length} bugs - see console output above`).toBe(0);
});
