import { test, expect } from '@playwright/test';

/**
 * Test to replicate the delete bug:
 * 1. Delete Feedback #9
 * 2. Click Feedback #8
 * 3. Check if #9 reappears (BUG)
 */

test.describe('Delete Bug Replication', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to dashboard (use your encrypted project ID)
    await page.goto('http://localhost:3001/projects/U2FsdGVkX19Apz1RpCRbiDzSi8kKZR4qCL6yUhqEag1VGgyybZ51w-04dJ1AK4iGeMLRnTU6x1ayrAda1XBZGA/dashboard/');

    // Wait for history sidebar to load
    await page.waitForSelector('text=Tasks, text=Feedback Review', { timeout: 15000 });

    console.log('✅ Dashboard loaded');
  });

  test('Delete item should not reappear after clicking another item', async ({ page }) => {
    // Step 1: Find and count initial items
    const initialItems = await page.locator('[data-testid="analysis-run-item"]').count();
    console.log(`📊 Initial items count: ${initialItems}`);

    // Step 2: Find Feedback Review #9 (or last item)
    const feedback9 = page.locator('text=Feedback Review #9').first();
    const hasFeedback9 = await feedback9.count() > 0;

    if (!hasFeedback9) {
      console.log('⚠️ Feedback #9 not found, using last item instead');
      // Use the last item
      const items = await page.locator('[data-testid="analysis-run-item"]').all();
      const lastItem = items[items.length - 1];

      // Get the text of last item
      const lastItemText = await lastItem.textContent();
      console.log(`🎯 Target item to delete: "${lastItemText}"`);

      // Hover and click delete
      await lastItem.hover();
      await page.waitForTimeout(500); // Wait for delete button to appear

      const deleteBtn = lastItem.locator('[data-testid="delete-button"], button:has-text("Delete"), [aria-label="Delete"]').first();
      await deleteBtn.click();

      // Confirm deletion if modal appears
      const confirmBtn = page.locator('button:has-text("Delete"), button:has-text("Confirm")').first();
      if (await confirmBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await confirmBtn.click();
      }

      // Wait for deletion to complete
      await page.waitForTimeout(1000);

      // Step 3: Verify item is deleted
      const afterDeleteCount = await page.locator('[data-testid="analysis-run-item"]').count();
      console.log(`📊 After delete count: ${afterDeleteCount}`);
      expect(afterDeleteCount).toBe(initialItems - 1);

      // Verify the deleted item is not visible
      const deletedItemVisible = await lastItem.isVisible().catch(() => false);
      expect(deletedItemVisible).toBe(false);
      console.log('✅ Item deleted successfully');

      // Step 4: Click on previous item (Feedback #8 or second-to-last)
      const items2 = await page.locator('[data-testid="analysis-run-item"]').all();
      const secondToLast = items2[items2.length - 1]; // Now last after deletion

      const secondToLastText = await secondToLast.textContent();
      console.log(`🖱️ Clicking on: "${secondToLastText}"`);

      await secondToLast.click();

      // Wait for analysis to load
      await page.waitForTimeout(2000);

      // Step 5: CHECK FOR BUG - Does deleted item reappear?
      const afterClickCount = await page.locator('[data-testid="analysis-run-item"]').count();
      console.log(`📊 After clicking another item: ${afterClickCount}`);

      // Check if deleted item text reappears
      const deletedItemReappeared = await page.locator(`text=${lastItemText}`).count() > 0;

      if (deletedItemReappeared) {
        console.log('🐛 BUG CONFIRMED: Deleted item reappeared!');

        // Check network requests to see if history was refetched
        // (This would be captured if we set up network listeners)
      } else {
        console.log('✅ No bug: Deleted item did not reappear');
      }

      expect(deletedItemReappeared).toBe(false);
      expect(afterClickCount).toBe(initialItems - 1);

    } else {
      console.log('🎯 Found Feedback Review #9');

      // Similar logic for Feedback #9
      await feedback9.hover();
      await page.waitForTimeout(500);

      const deleteBtn = page.locator('[data-testid="delete-button"]').first();
      await deleteBtn.click();

      const confirmBtn = page.locator('button:has-text("Delete"), button:has-text("Confirm")').first();
      if (await confirmBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await confirmBtn.click();
      }

      await page.waitForTimeout(1000);

      // Verify deletion
      const feedback9AfterDelete = await page.locator('text=Feedback Review #9').count();
      expect(feedback9AfterDelete).toBe(0);
      console.log('✅ Feedback #9 deleted');

      // Click Feedback #8
      const feedback8 = page.locator('text=Feedback Review #8').first();
      await feedback8.click();
      console.log('🖱️ Clicked Feedback Review #8');

      await page.waitForTimeout(2000);

      // Check if #9 reappeared
      const feedback9Reappeared = await page.locator('text=Feedback Review #9').count() > 0;

      if (feedback9Reappeared) {
        console.log('🐛 BUG CONFIRMED: Feedback #9 reappeared!');
      } else {
        console.log('✅ No bug: Feedback #9 did not reappear');
      }

      expect(feedback9Reappeared).toBe(false);
    }
  });

  test('Track network requests during delete and click', async ({ page }) => {
    const networkRequests: any[] = [];

    // Listen to all network requests
    page.on('request', request => {
      if (request.url().includes('/feedback/') || request.url().includes('/analysis/')) {
        networkRequests.push({
          method: request.method(),
          url: request.url(),
          timestamp: new Date().toISOString()
        });
      }
    });

    page.on('response', async response => {
      if (response.url().includes('/feedback/history')) {
        console.log(`📡 History fetch: ${response.status()} - ${response.url()}`);
        try {
          const body = await response.json();
          console.log(`📦 History items count: ${body?.data?.length || body?.length || 'unknown'}`);
        } catch (e) {
          // Ignore JSON parse errors
        }
      }
    });

    // Find and delete last item
    const items = await page.locator('[data-testid="analysis-run-item"]').all();
    const lastItem = items[items.length - 1];
    const lastItemText = await lastItem.textContent();

    console.log(`\n=== DELETING: ${lastItemText} ===`);
    await lastItem.hover();
    await page.waitForTimeout(500);

    const deleteBtn = lastItem.locator('[data-testid="delete-button"], button[aria-label="Delete"]').first();
    await deleteBtn.click();

    const confirmBtn = page.locator('button:has-text("Delete"), button:has-text("Confirm")').first();
    if (await confirmBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await confirmBtn.click();
    }

    await page.waitForTimeout(2000);
    console.log(`\n=== AFTER DELETE - Network requests: ${networkRequests.length} ===`);

    // Click another item
    const items2 = await page.locator('[data-testid="analysis-run-item"]').all();
    const secondItem = items2[1]; // Second item
    const secondItemText = await secondItem.textContent();

    const requestCountBeforeClick = networkRequests.length;
    console.log(`\n=== CLICKING: ${secondItemText} ===`);
    await secondItem.click();

    await page.waitForTimeout(3000);

    const requestCountAfterClick = networkRequests.length;
    const newRequests = networkRequests.slice(requestCountBeforeClick);

    console.log(`\n=== AFTER CLICK - New requests: ${newRequests.length} ===`);
    newRequests.forEach(req => {
      console.log(`  ${req.method} ${req.url}`);
    });

    // Check if history was refetched
    const historyRefetched = newRequests.some(req =>
      req.url.includes('/feedback/history') || req.url.includes('/history/list')
    );

    if (historyRefetched) {
      console.log('🐛 BUG CAUSE: History was refetched after clicking another item!');
    } else {
      console.log('✅ History was NOT refetched');
    }
  });
});
