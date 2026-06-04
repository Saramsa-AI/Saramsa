import { test } from '@playwright/test';

test('Check UI state', async ({ page }) => {
  console.log('\n🔍 Checking UI state...\n');
  
  await page.goto('http://localhost:3001');
  await page.waitForTimeout(3000);
  
  // Check if on dashboard
  const url = page.url();
  console.log('Current URL:', url);
  
  if (!url.includes('/projects/') || !url.includes('/dashboard')) {
    console.log('Not on dashboard, navigating...');
    const goBtn = page.locator('button:has-text("Go to Analysis")').first();
    if (await goBtn.count() > 0) {
      await goBtn.click();
      await page.waitForTimeout(3000);
    }
  }
  
  console.log('Final URL:', page.url());
  
  // Check for "Analyzing..." items specifically
  const analyzingItems = await page.locator('text=/Analyzing/i').all();
  console.log(`\n🔄 "Analyzing..." items found: ${analyzingItems.length}`);
  
  for (let i = 0; i < analyzingItems.length; i++) {
    const text = await analyzingItems[i].textContent();
    console.log(`\n  Analyzing #${i + 1}: ${text?.trim()}`);
  }
  
  // Check all feedback review items
  const feedbackItems = await page.locator('text=/Feedback Review/i').all();
  console.log(`\n📋 "Feedback Review" items found: ${feedbackItems.length}`);
  
  for (let i = 0; i < Math.min(5, feedbackItems.length); i++) {
    const text = await feedbackItems[i].textContent();
    console.log(`  ${i + 1}. ${text?.trim().substring(0, 60)}`);
  }
  
  console.log('\n✅ Check complete!');
});
