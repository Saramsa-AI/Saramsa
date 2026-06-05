import { test } from '@playwright/test';

test('Debug analysis state', async ({ page }) => {
  console.log('\n🔍 DEBUGGING ANALYSIS STATE\n');
  
  // Go to dashboard
  await page.goto('http://localhost:3001');
  await page.waitForTimeout(3000);
  
  // Navigate to project dashboard
  if (!page.url().includes('/projects/')) {
    const goBtn = page.locator('button:has-text("Go to Analysis")').first();
    if (await goBtn.count() > 0) {
      await goBtn.click();
      await page.waitForTimeout(3000);
    }
  }
  
  console.log('Current URL:', page.url());
  
  // Extract Redux state
  const state = await page.evaluate(() => {
    const store = (window as any).store?.getState?.() || 
                  (window as any).__REDUX_DEVTOOLS_EXTENSION__?.getState?.();
    
    if (!store?.analysis) return null;
    
    return {
      selectedAnalysisId: store.analysis.selectedAnalysisId,
      analysisHistory: store.analysis.analysisHistory.map((e: any) => ({
        id: e.id,
        name: e.name,
        display_number: e.display_number,
        status: e.status
      })),
      tasks: Object.keys(store.analysis.tasks || {}),
      deletingIds: store.analysis.deletingIds || []
    };
  });
  
  if (!state) {
    console.log('❌ Redux not accessible');
    return;
  }
  
  console.log('\n📊 REDUX STATE:');
  console.log('Selected ID:', state.selectedAnalysisId);
  console.log('Deleting IDs:', state.deletingIds);
  console.log('\n📋 History (' + state.analysisHistory.length + ' items):');
  
  state.analysisHistory.forEach((item: any, i: number) => {
    const isSelected = item.id === state.selectedAnalysisId;
    console.log(`  ${i + 1}. ${item.name || 'Unnamed'} (#${item.display_number || '?'})`);
    console.log(`     ID: ${item.id}`);
    console.log(`     Status: ${item.status}${isSelected ? ' ← SELECTED' : ''}`);
  });
  
  // Check network errors
  const errors: string[] = [];
  page.on('response', async (response) => {
    if (response.status() === 404 || response.status() === 500) {
      errors.push(`${response.status()} ${response.url()}`);
    }
  });
  
  await page.waitForTimeout(2000);
  
  if (errors.length > 0) {
    console.log('\n❌ NETWORK ERRORS:');
    errors.forEach(err => console.log('  ' + err));
  }
  
  console.log('\n✅ Debug complete');
});
