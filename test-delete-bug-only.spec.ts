import { test, expect } from '@playwright/test';

/**
 * Focused test: ONLY test the delete bug
 * Assumes you're already logged in
 */

test('Delete Bug Detection', async ({ page }) => {
  console.log('\n🔍 DELETE BUG DETECTOR');
  console.log('='.repeat(70));

  // Go to dashboard and navigate to a project
  console.log('\n📍 Navigating to dashboard...');
  await page.goto('http://localhost:3001');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);

  // Ensure we're on a project dashboard (not projects list)
  let retries = 0;
  while (!page.url().includes('/projects/') || !page.url().includes('/dashboard')) {
    console.log(`   Attempt ${retries + 1}: Navigating to project...`);

    // Click "Go to Analysis" to enter a project
    const goToAnalysisBtn = page.locator('button:has-text("Go to Analysis")').first();
    const hasBut = await goToAnalysisBtn.count() > 0;

    if (hasBut) {
      await goToAnalysisBtn.click();
      await page.waitForTimeout(3000);
    } else {
      console.log('   No "Go to Analysis" button - trying to click project card...');
      const projectCard = page.locator('[class*="card"]').first();
      if (await projectCard.count() > 0) {
        await projectCard.click();
        await page.waitForTimeout(3000);
      }
    }

    retries++;
    if (retries >= 3) {
      console.log('   ⚠️  Could not navigate to project dashboard after 3 attempts');
      break;
    }
  }

  console.log(`   Current URL: ${page.url()}`);

  // Wait for sidebar to load with items
  console.log('   Waiting for analyses to load...');
  await page.waitForSelector('text=/Feedback Review|Analysis 202/', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);

  console.log(`✅ On dashboard`);

  // Inject monitoring script into page
  await page.evaluate(() => {
    // Monitor history refetches
    const originalFetch = window.fetch;
    (window as any).historyFetches = [];

    window.fetch = function(...args: any[]) {
      const url = args[0];
      if (typeof url === 'string' && url.includes('/feedback/history')) {
        (window as any).historyFetches.push({
          url,
          timestamp: new Date().toISOString(),
          stack: new Error().stack
        });
        console.log(`📡 HISTORY FETCH: ${url}`);
      }
      return originalFetch.apply(this, args);
    };

    console.log('✅ Monitoring installed');
  });

  // Get Redux state helper
  const getReduxState = () => page.evaluate(() => {
    const state = (window as any).store?.getState?.() ||
                  (window as any).__REDUX_DEVTOOLS_EXTENSION__?.getState?.();
    return {
      history: state?.analysis?.analysisHistory || [],
      tasks: state?.analysis?.tasks || {},
      selectedId: state?.analysis?.selectedAnalysisId
    };
  });

  // STEP 1: Get initial state
  console.log('\n📊 STEP 1: Get initial history');

  // Check UI first
  const uiItems = await page.locator('text=/Feedback Review|Analysis 202/').count();
  console.log(`   Items visible in UI: ${uiItems}`);

  const initialState = await getReduxState();
  console.log(`   Items in Redux history: ${initialState.history.length}`);
  console.log(`   Items in Redux tasks map: ${Object.keys(initialState.tasks).length}`);

  // Check if Redux is accessible
  const reduxAccessible = await page.evaluate(() => {
    return !!(window as any).store || !!(window as any).__REDUX_DEVTOOLS_EXTENSION__;
  });
  console.log(`   Redux accessible: ${reduxAccessible}`);

  if (!reduxAccessible) {
    console.log('\n⚠️  Redux not accessible - testing via UI only');
  }

  if (uiItems < 2 && initialState.history.length < 2) {
    console.log('\n⚠️  Need at least 2 items. Test skipped.');
    console.log('   Tip: Upload some files first to create analyses');
    return;
  }

  // Use UI count if Redux is empty
  const itemCount = Math.max(uiItems, initialState.history.length);
  console.log(`\n   Using ${itemCount} items for test`);

  initialState.history.forEach((item: any, i: number) => {
    console.log(`   ${i + 1}. ${item.id} - ${item.name || 'Unnamed'} (${item.status})`);
  });

  // Find last item - from Redux if available, otherwise from UI
  let targetId = 'unknown';
  let targetName = 'unknown';

  if (initialState.history.length > 0) {
    const lastItem = initialState.history[initialState.history.length - 1];
    targetId = lastItem.id;
    targetName = lastItem.name || 'Unnamed';
  } else {
    // Get from UI
    const items = await page.locator('text=/Feedback Review|Analysis 202/').all();
    const lastItemElement = items[items.length - 1];
    targetName = (await lastItemElement.textContent()) || 'Unnamed';
  }

  console.log(`\n🎯 Target to delete: "${targetName}"`);

  // STEP 2: Delete the last item
  console.log('\n🗑️  STEP 2: Deleting last item...');

  // Find and click delete button
  const items = await page.locator('text=/Feedback Review|Analysis 202/').all();
  const lastItemElement = items[items.length - 1];

  await lastItemElement.hover();
  await page.waitForTimeout(500);

  const deleteBtn = page.locator('[title="Delete"]').last();
  await deleteBtn.click();
  await page.waitForTimeout(500);

  const confirmBtn = page.locator('button:has-text("Delete")').first();
  await confirmBtn.click();

  console.log('   ✅ Delete clicked');
  await page.waitForTimeout(2000);

  // STEP 3: Check state after delete
  console.log('\n📊 STEP 3: State AFTER delete');

  // Check UI
  const uiItemsAfterDelete = await page.locator('text=/Feedback Review|Analysis 202/').count();
  console.log(`   Items in UI: ${uiItemsAfterDelete}`);

  const visibleAfterDelete = await page.locator(`text="${targetName}"`).count() > 0;
  console.log(`   Deleted item "${targetName}" still visible: ${visibleAfterDelete ? '❌ YES' : '✅ NO'}`);

  // Check Redux if accessible
  if (reduxAccessible) {
    const afterDeleteState = await getReduxState();
    console.log(`   Items in Redux history: ${afterDeleteState.history.length}`);
    console.log(`   Items in Redux tasks: ${Object.keys(afterDeleteState.tasks).length}`);

    const stillInHistory = afterDeleteState.history.some((item: any) => item.id === targetId);
    const stillInTasks = targetId in afterDeleteState.tasks;

    console.log(`   Still in Redux history: ${stillInHistory ? '❌ YES' : '✅ NO'}`);
    console.log(`   Still in Redux tasks: ${stillInTasks ? '❌ YES' : '✅ NO'}`);
  }

  // STEP 4: Click another item
  console.log('\n👆 STEP 4: Clicking another item...');

  const remainingItems = await page.locator('text=/Feedback Review|Analysis 202/').all();
  if (remainingItems.length > 0) {
    await remainingItems[0].click();
    await page.waitForTimeout(3000);
    console.log('   ✅ Clicked another item');
  }

  // STEP 5: Check if deleted item reappeared
  console.log('\n📊 STEP 5: Checking for reappearance...');

  // Check UI first
  const uiItemsAfterClick = await page.locator('text=/Feedback Review|Analysis 202/').count();
  console.log(`   Items in UI: ${uiItemsAfterClick}`);

  const visibleInUI = await page.locator(`text="${targetName}"`).count() > 0;
  console.log(`   Deleted item "${targetName}" visible in UI: ${visibleInUI ? '🐛 YES (BUG!)' : '✅ NO'}`);

  // Check Redux if accessible
  let reappearedInHistory = false;
  let reappearedInTasks = false;

  if (reduxAccessible) {
    const afterClickState = await getReduxState();
    console.log(`   Items in Redux history: ${afterClickState.history.length}`);

    reappearedInHistory = afterClickState.history.some((item: any) => item.id === targetId);
    reappearedInTasks = targetId in afterClickState.tasks;

    console.log(`   Reappeared in Redux history: ${reappearedInHistory ? '🐛 YES' : '✅ NO'}`);
    console.log(`   Reappeared in Redux tasks: ${reappearedInTasks ? '🐛 YES' : '✅ NO'}`);
  }

  // STEP 6: Check for history refetches
  console.log('\n📡 STEP 6: Network activity check');
  const historyFetches = await page.evaluate(() => (window as any).historyFetches || []);

  console.log(`   History API calls during test: ${historyFetches.length}`);
  historyFetches.forEach((fetch: any, i: number) => {
    console.log(`   ${i + 1}. ${fetch.url} at ${fetch.timestamp}`);
  });

  if (historyFetches.length > 1) {
    console.log('   ⚠️  Multiple history fetches detected - possible cause of bug');
  }

  // FINAL VERDICT
  console.log('\n' + '='.repeat(70));
  console.log('🎯 FINAL VERDICT:');
  console.log('='.repeat(70));

  if (reappearedInHistory || reappearedInTasks || visibleInUI) {
    console.log('🐛 DELETE BUG CONFIRMED!');
    console.log('   Item was deleted but reappeared after clicking another item.');

    if (historyFetches.length > 1) {
      console.log('   Root cause: History was refetched from backend');
      console.log('   The refetch brought back the deleted item');
    }

    // Try clicking the reappeared item
    if (visibleInUI) {
      console.log('\n👆 Testing: Clicking reappeared item...');
      const reappearedElement = page.locator(`text="${targetName}"`).first();
      await reappearedElement.click();
      await page.waitForTimeout(2000);

      const hasError = await page.locator('text=/not found|404|does not exist/i').count() > 0;
      if (hasError) {
        console.log('   ✅ CONFIRMED: Shows "not found" error (as expected)');
      }
    }
  } else {
    console.log('✅ NO BUG DETECTED');
    console.log('   Deleted item stayed deleted.');
  }

  console.log('\n' + '='.repeat(70));

  // Fail test if bug found
  expect(reappearedInHistory, 'Deleted item should not reappear in history').toBe(false);
  expect(reappearedInTasks, 'Deleted item should not reappear in tasks').toBe(false);
  expect(visibleInUI, 'Deleted item should not be visible in UI').toBe(false);
});
