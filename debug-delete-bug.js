/**
 * Simple browser console script to debug delete bug
 *
 * INSTRUCTIONS:
 * 1. Open browser DevTools (F12)
 * 2. Go to Console tab
 * 3. Copy-paste this entire script
 * 4. Follow prompts in console
 */

console.log('🔍 DELETE BUG DEBUGGER');
console.log('='.repeat(50));

// Get Redux store from window
const getStore = () => {
  // Try common Redux DevTools methods
  if (window.__REDUX_DEVTOOLS_EXTENSION__) {
    const state = window.__REDUX_DEVTOOLS_EXTENSION__.getState();
    return state;
  }

  // Try direct store access (if exposed)
  if (window.store) {
    return window.store.getState();
  }

  return null;
};

// Monitor Redux state changes
const monitorDeletes = () => {
  console.log('\n📊 Current Analysis State:');

  const state = getStore();

  if (!state || !state.analysis) {
    console.log('❌ Cannot access Redux store');
    console.log('ℹ️  This might be because:');
    console.log('   1. Redux DevTools extension not installed');
    console.log('   2. Store not exposed on window');
    return;
  }

  const { analysis } = state;

  console.log(`\n📋 History (${analysis.analysisHistory.length} items):`);
  analysis.analysisHistory.forEach((item, i) => {
    console.log(`  ${i + 1}. ${item.id} - ${item.name || 'Unnamed'} (${item.status})`);
  });

  console.log(`\n🗺️  Tasks Map (${Object.keys(analysis.tasks || {}).length} items):`);
  Object.entries(analysis.tasks || {}).forEach(([id, task]) => {
    console.log(`  ${id} → ${task.state}`);
  });

  console.log(`\n✅ Selected: ${analysis.selectedAnalysisId}`);

  return analysis;
};

// Test delete and monitor
window.testDelete = async (analysisId) => {
  console.log(`\n🗑️  TESTING DELETE: ${analysisId}`);
  console.log('='.repeat(50));

  console.log('\n📊 BEFORE DELETE:');
  const before = monitorDeletes();

  if (!before) return;

  const historyCountBefore = before.analysisHistory.length;
  const tasksCountBefore = Object.keys(before.tasks || {}).length;

  console.log(`\n🔄 Deleting...`);

  // Call delete API
  try {
    const response = await fetch(
      `/api/feedback/analysis/${encodeURIComponent(analysisId)}/`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include'
      }
    );

    console.log(`📡 DELETE API response: ${response.status}`);

    if (!response.ok) {
      console.log('❌ Delete failed on backend');
      return;
    }

    // Wait for Redux to update
    await new Promise(resolve => setTimeout(resolve, 1000));

    console.log('\n📊 AFTER DELETE:');
    const after = monitorDeletes();

    const historyCountAfter = after.analysisHistory.length;
    const tasksCountAfter = Object.keys(after.tasks || {}).length;

    console.log('\n📈 CHANGES:');
    console.log(`  History: ${historyCountBefore} → ${historyCountAfter} (${historyCountAfter - historyCountBefore})`);
    console.log(`  Tasks: ${tasksCountBefore} → ${tasksCountAfter} (${tasksCountAfter - tasksCountBefore})`);

    const inHistory = after.analysisHistory.some(h => h.id === analysisId);
    const inTasks = analysisId in (after.tasks || {});

    console.log('\n🔍 ITEM STATUS:');
    console.log(`  Still in history: ${inHistory ? '❌ YES (BUG!)' : '✅ NO'}`);
    console.log(`  Still in tasks: ${inTasks ? '❌ YES (BUG!)' : '✅ NO'}`);

    if (inHistory || inTasks) {
      console.log('\n🐛 BUG DETECTED: Item not fully removed!');
    } else {
      console.log('\n✅ Delete worked correctly');
    }

  } catch (err) {
    console.error('❌ Error:', err);
  }
};

// Monitor history refetches
let historyFetchCount = 0;
const originalFetch = window.fetch;

window.fetch = function(...args) {
  const url = args[0];

  if (typeof url === 'string' && url.includes('/feedback/history')) {
    historyFetchCount++;
    console.log(`📡 HISTORY FETCH #${historyFetchCount}: ${url}`);
    console.trace('Called from:');
  }

  return originalFetch.apply(this, args);
};

console.log('\n✅ Debugger ready!');
console.log('\nCOMMANDS:');
console.log('  monitorDeletes()     - Show current state');
console.log('  testDelete("id")     - Test delete and monitor changes');
console.log('\nEXAMPLE:');
console.log('  1. monitorDeletes()  - Get list of IDs');
console.log('  2. testDelete("insight_123")  - Test deleting one');
console.log('\n📡 History fetches are now being logged automatically!');
