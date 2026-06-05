import { test, expect } from '@playwright/test';
import path from 'path';

test.use({ storageState: '../playwright/.auth/user.json' });

test('Complete Upload → Analyze → View Flow', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════');
  console.log('🧪 COMPLETE UPLOAD → ANALYZE → VIEW FLOW TEST');
  console.log('═══════════════════════════════════════════════════\n');

  const testResults = {
    navigationSuccess: false,
    fileUploadSuccess: false,
    analyzeButtonWorks: false,
    loaderShows: false,
    analyzingPlaceholderShows: false,
    analysisCompletes: false,
    dataDisplays: false,
    errors: [] as string[]
  };

  try {
    // ========================================
    // Step 1: Navigate to project
    // ========================================
    console.log('📋 STEP 1: Navigate to NewTest project');
    console.log('─'.repeat(50));

    await page.goto('http://localhost:3001');
    await page.waitForTimeout(3000);

    // Wait for projects to load (wait for loading to disappear)
    await page.waitForSelector('text=NewTest', { timeout: 15000 });
    console.log('Projects loaded');

    // Find and click NewTest project
    const projectCard = page.locator('text=NewTest').first();
    if (await projectCard.count() === 0) {
      testResults.errors.push('NewTest project not found on projects page');
      throw new Error('Project not found');
    }

    const goBtn = page.locator('button:has-text("Go to Analysis")').first();
    await goBtn.click();
    await page.waitForURL('**/dashboard/**', { timeout: 10000 });
    await page.waitForTimeout(2000);

    testResults.navigationSuccess = true;
    console.log('✅ Navigated to project dashboard\n');

    // ========================================
    // Step 2: Count initial analyses
    // ========================================
    console.log('📋 STEP 2: Count existing analyses');
    console.log('─'.repeat(50));

    await page.waitForTimeout(3000); // Wait for history to load
    const initialCount = await page.locator('text=/Feedback Review/i').count();
    console.log(`Initial analysis count: ${initialCount}\n`);

    // ========================================
    // Step 3: Upload file
    // ========================================
    console.log('📋 STEP 3: Upload Data30.json');
    console.log('─'.repeat(50));

    const fileInput = page.locator('input[type="file"]').first();
    const filePath = path.join(__dirname, '..', '..', 'Saramsa-Data', 'Data-30.json');

    console.log(`Uploading file: ${filePath}`);
    await fileInput.setInputFiles(filePath);
    await page.waitForTimeout(1000);

    testResults.fileUploadSuccess = true;
    console.log('✅ File selected\n');

    // ========================================
    // Step 4: Click Analyze button
    // ========================================
    console.log('📋 STEP 4: Click Analyze button');
    console.log('─'.repeat(50));

    const analyzeBtn = page.locator('button:has-text("Analyze")').first();
    if (await analyzeBtn.count() === 0) {
      testResults.errors.push('Analyze button not found');
      throw new Error('Analyze button not found');
    }

    await analyzeBtn.click();
    console.log('✅ Clicked Analyze button\n');
    testResults.analyzeButtonWorks = true;

    // ========================================
    // Step 5: Check for loader
    // ========================================
    console.log('📋 STEP 5: Check for loading indicators');
    console.log('─'.repeat(50));

    await page.waitForTimeout(2000);

    // Check for "Analyzing..." text
    const analyzingText = await page.locator('text=/Analyzing/i').count();
    if (analyzingText > 0) {
      testResults.analyzingPlaceholderShows = true;
      console.log('✅ "Analyzing..." placeholder shows in sidebar');
    } else {
      testResults.errors.push('No "Analyzing..." placeholder visible');
      console.log('❌ "Analyzing..." placeholder NOT visible');
    }

    // Check for loader/spinner
    const loader = await page.locator('.animate-spin').count();
    if (loader > 0) {
      testResults.loaderShows = true;
      console.log('✅ Loader/spinner is visible');
    } else {
      testResults.errors.push('No loader/spinner visible');
      console.log('❌ Loader/spinner NOT visible');
    }

    console.log('');

    // ========================================
    // Step 6: Wait for completion
    // ========================================
    console.log('📋 STEP 6: Wait for analysis to complete');
    console.log('─'.repeat(50));

    let completed = false;
    let pollCount = 0;
    const maxPolls = 60; // 2 minutes max

    while (!completed && pollCount < maxPolls) {
      await page.waitForTimeout(2000);
      pollCount++;

      // Check if analysis count increased
      const currentCount = await page.locator('text=/Feedback Review/i').count();

      // Check if "Analyzing..." is gone
      const stillAnalyzing = await page.locator('text=/Analyzing/i').count();

      console.log(`Poll #${pollCount}: Count=${currentCount}, Analyzing=${stillAnalyzing}`);

      if (currentCount > initialCount && stillAnalyzing === 0) {
        completed = true;
        testResults.analysisCompletes = true;
        console.log('\n✅ Analysis completed!\n');
      }
    }

    if (!completed) {
      testResults.errors.push('Analysis did not complete within 2 minutes');
      console.log('❌ Analysis did NOT complete\n');
    }

    // ========================================
    // Step 7: Click on new analysis
    // ========================================
    console.log('📋 STEP 7: Click on new analysis to view data');
    console.log('─'.repeat(50));

    const analyses = await page.locator('text=/Feedback Review/i').all();
    if (analyses.length > 0) {
      await analyses[0].click();
      await page.waitForTimeout(3000);
      console.log('✅ Clicked on analysis\n');
    } else {
      testResults.errors.push('No analyses available to click');
      throw new Error('No analyses available');
    }

    // ========================================
    // Step 8: Check if data displays
    // ========================================
    console.log('📋 STEP 8: Verify data displays');
    console.log('─'.repeat(50));

    // Check for "No Analysis Data Available" error
    const noDataError = await page.locator('text=/No Analysis Data Available/i').count();

    // Check for actual data (metrics, charts, etc.)
    const hasMetrics = await page.locator('text=/Total Comments|Positive|Negative/i').count();

    if (noDataError > 0) {
      testResults.errors.push('"No Analysis Data Available" error shown');
      console.log('❌ "No Analysis Data Available" error is showing');
    } else if (hasMetrics > 0) {
      testResults.dataDisplays = true;
      console.log('✅ Analysis data is displaying (found metrics)');
    } else {
      testResults.errors.push('Unclear if data is displaying - no error but no metrics found');
      console.log('⚠️  Unclear state - no error but no metrics found');
    }

  } catch (error: any) {
    console.error('\n❌ TEST ERROR:', error.message);
    testResults.errors.push(error.message);
  }

  // ========================================
  // FINAL REPORT
  // ========================================
  console.log('\n═══════════════════════════════════════════════════');
  console.log('📊 TEST RESULTS SUMMARY');
  console.log('═══════════════════════════════════════════════════\n');

  console.log('Navigation:', testResults.navigationSuccess ? '✅ PASS' : '❌ FAIL');
  console.log('File Upload:', testResults.fileUploadSuccess ? '✅ PASS' : '❌ FAIL');
  console.log('Analyze Button:', testResults.analyzeButtonWorks ? '✅ PASS' : '❌ FAIL');
  console.log('Loader Shows:', testResults.loaderShows ? '✅ PASS' : '❌ FAIL');
  console.log('"Analyzing..." Placeholder:', testResults.analyzingPlaceholderShows ? '✅ PASS' : '❌ FAIL');
  console.log('Analysis Completes:', testResults.analysisCompletes ? '✅ PASS' : '❌ FAIL');
  console.log('Data Displays:', testResults.dataDisplays ? '✅ PASS' : '❌ FAIL');

  if (testResults.errors.length > 0) {
    console.log('\n❌ ERRORS:');
    testResults.errors.forEach((err, i) => {
      console.log(`${i + 1}. ${err}`);
    });
  }

  const passCount = [
    testResults.navigationSuccess,
    testResults.fileUploadSuccess,
    testResults.analyzeButtonWorks,
    testResults.loaderShows,
    testResults.analyzingPlaceholderShows,
    testResults.analysisCompletes,
    testResults.dataDisplays
  ].filter(Boolean).length;

  console.log(`\n📈 Overall: ${passCount}/7 tests passed`);
  console.log('═══════════════════════════════════════════════════\n');

  // Fail if critical tests didn't pass
  expect(testResults.navigationSuccess, 'Navigation failed').toBe(true);
  expect(testResults.analyzeButtonWorks, 'Analyze button failed').toBe(true);
  expect(testResults.analysisCompletes, 'Analysis did not complete').toBe(true);
  expect(testResults.dataDisplays, 'Data not displaying').toBe(true);
});
