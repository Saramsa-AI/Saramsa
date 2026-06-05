import { test, expect } from '@playwright/test';

/**
 * Simple test to replicate delete bug
 */

test('Delete bug - item reappears after clicking another', async ({ page }) => {
  // Track network requests
  const historyRequests: string[] = [];

  page.on('response', async (response) => {
    if (response.url().includes('/feedback/history')) {
      historyRequests.push(`${response.request().method()} ${response.url()} - ${response.status()}`);
      console.log(`📡 History request: ${response.status()}`);
    }
  });

  // Navigate
  console.log('🌐 Navigating to dashboard...');
  await page.goto('http://localhost:3001/projects/U2FsdGVkX19Apz1RpCRbiDzSi8kKZR4qCL6yUhqEag1VGgyybZ51w-04dJ1AK4iGeMLRnTU6x1ayrAda1XBZGA/dashboard/');

  // Wait for page to load
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);

  console.log('\n📊 STEP 1: Count initial items');
  // Find all analysis items in sidebar
  // Try multiple selectors
  let itemsLocator = page.locator('text=/Feedback Review|Analysis 202/');
  let initialCount = await itemsLocator.count();

  if (initialCount === 0) {
    // Fallback: look for items with delete button (Trash icon)
    itemsLocator = page.locator('[title="Delete"]').locator('..');
    initialCount = await itemsLocator.count();
  }

  console.log(`   Found ${initialCount} analysis items`);

  if (initialCount < 2) {
    console.log('⚠️  Need at least 2 items to test. Skipping.');
    return;
  }

  // Get the last item's text for tracking
  const lastItem = itemsLocator.nth(initialCount - 1);
  const lastItemText = (await lastItem.textContent()) || '';
  console.log(`   Last item: "${lastItemText.trim()}"`);

  console.log('\n🗑️  STEP 2: Delete last item');
  // Hover to show delete button
  await lastItem.hover();
  await page.waitForTimeout(500);

  // Click trash icon (has title="Delete")
  const trashIcon = lastItem.locator('[title="Delete"]');
  await trashIcon.click();
  console.log('   Clicked trash icon');

  // Wait for confirmation dialog and click Delete button
  await page.waitForTimeout(500);
  const confirmDelete = page.locator('button:has-text("Delete")').first();
  await confirmDelete.click();
  console.log('   Confirmed deletion');

  // Wait for delete to process
  await page.waitForTimeout(2000);

  console.log('\n✅ STEP 3: Verify deletion');
  const afterDeleteCount = await itemsLocator.count();
  console.log(`   Items after delete: ${afterDeleteCount}`);
  console.log(`   Expected: ${initialCount - 1}`);

  const deletedItemGone = afterDeleteCount === initialCount - 1;
  if (deletedItemGone) {
    console.log('   ✅ Item deleted successfully');
  } else {
    console.log('   ❌ Delete failed - item count unchanged');
  }

  console.log('\n🖱️  STEP 4: Click another item');
  const secondToLastItem = itemsLocator.nth(initialCount - 2);
  const clickedItemText = (await secondToLastItem.textContent()) || '';
  console.log(`   Clicking: "${clickedItemText.trim()}"`);

  const historyRequestsBefore = historyRequests.length;
  await secondToLastItem.click();

  // Wait for analysis to load
  await page.waitForTimeout(3000);

  console.log('\n🔍 STEP 5: Check if deleted item reappeared');
  const afterClickCount = await itemsLocator.count();
  console.log(`   Items after click: ${afterClickCount}`);

  // Check if the deleted item's text reappears
  const deletedTextStillVisible = await page.locator(`text=${lastItemText}`).count() > 0;

  // Check if history was refetched
  const newHistoryRequests = historyRequests.slice(historyRequestsBefore);
  const historyRefetched = newHistoryRequests.length > 0;

  console.log('\n📋 RESULTS:');
  console.log(`   Items before delete: ${initialCount}`);
  console.log(`   Items after delete: ${afterDeleteCount}`);
  console.log(`   Items after clicking another: ${afterClickCount}`);
  console.log(`   Deleted text still visible: ${deletedTextStillVisible}`);
  console.log(`   History refetched during click: ${historyRefetched}`);

  if (historyRefetched) {
    console.log('\n📡 History requests during click:');
    newHistoryRequests.forEach(req => console.log(`     ${req}`));
  }

  if (afterClickCount > afterDeleteCount || deletedTextStillVisible) {
    console.log('\n🐛 BUG CONFIRMED: Deleted item reappeared!');
    if (historyRefetched) {
      console.log('   Root cause: History was refetched from backend');
    } else {
      console.log('   Root cause: Unknown (history not refetched)');
    }
  } else {
    console.log('\n✅ NO BUG: Deleted item stayed deleted');
  }

  // Assertions
  expect(afterClickCount, 'Item count should stay the same after clicking another item').toBe(afterDeleteCount);
  expect(deletedTextStillVisible, 'Deleted item text should not reappear').toBe(false);
});
