/**
 * DIAGNOSTIC (real backend, not mocked): reproduce the review-queue count
 * mismatch — stats card says N pending while the batch bar says a different
 * number. Captures the actual API responses alongside what the DOM renders so
 * we can tell whether the backend, the Redux state, or the label is wrong.
 *
 * Credentials come from env so nothing is committed:
 *   E2E_EMAIL=... E2E_PASSWORD=... npx playwright test e2e/review-count-diagnostic.spec.ts
 */

import { test, expect } from '@playwright/test'

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const PROJECT_ID = process.env.E2E_PROJECT_ID || '11d1d1ff-ec5a-4c7f-8b87-7a04d16a4c09'

test('diagnose review queue count mismatch', async ({ page }) => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD')
  test.setTimeout(180_000)

  // Record every review API call and its payload size.
  const calls: { url: string; count?: number; body?: any }[] = []
  page.on('response', async (res) => {
    const url = res.url()
    if (!url.includes('/api/work-items/review/')) return
    try {
      const json = await res.json()
      const data = json?.data ?? {}
      calls.push({
        url: url.replace(/^.*\/api/, '/api'),
        count: Array.isArray(data.candidates) ? data.candidates.length : undefined,
        body: Array.isArray(data.candidates) ? undefined : data,
      })
    } catch {
      /* non-JSON */
    }
  })

  await page.goto('/login/')
  await page.getByRole('textbox', { name: 'Email Address' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Login' }).click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 60_000 })

  await page.goto(`/projects/${PROJECT_ID}/review/`)
  await expect(page.getByRole('heading', { level: 1, name: 'User Stories' })).toBeVisible({ timeout: 60_000 })

  // Let both fetches settle (they're slow against the remote DB).
  await page.waitForTimeout(15_000)

  const bodyText = (await page.locator('body').innerText()).replace(/\n+/g, ' | ')

  // Stats card numbers (the 4 tiles) and the batch-bar label.
  const statsPending = bodyText.match(/(\d+)\s*\|\s*Pending/i)?.[1]
  const barLabel = bodyText.match(/(\d+)\s+(pending|approved|dismissed|snoozed)\b/i)?.[0]
  const rowCount = await page.getByRole('heading', { level: 3 }).count()
  const statusFilter = await page.locator('select').first().inputValue()

  console.log('\n================ DIAGNOSTIC ================')
  console.log('status filter (dropdown) :', statusFilter)
  console.log('stats card "Pending"     :', statsPending)
  console.log('batch bar label          :', barLabel)
  console.log('rows rendered in DOM     :', rowCount)
  console.log('--- review API calls ---')
  for (const c of calls) {
    console.log(`  ${c.url}`)
    console.log(`      candidates=${c.count ?? '-'}  body=${c.body ? JSON.stringify(c.body) : '-'}`)
  }
  console.log('============================================\n')
})
