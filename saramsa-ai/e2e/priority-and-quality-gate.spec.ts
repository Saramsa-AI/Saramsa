/**
 * Verifies two things on the project dashboard User stories tab:
 *   1. the priority tag renders as a NUMBER (1-4, 1 most urgent), not a word
 *   2. selecting a row and clicking Configure & Push clears the quality gate
 *      ("Priority is required." was firing because the row carried no priority)
 *
 *   E2E_EMAIL=... E2E_PASSWORD=... npx playwright test e2e/priority-and-quality-gate.spec.ts
 */
import { test, expect } from '@playwright/test'

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const PID = process.env.E2E_PROJECT_ID || '11d1d1ff-ec5a-4c7f-8b87-7a04d16a4c09'

test('priority tag is numeric and the push quality gate passes', async ({ page }) => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD')
  test.setTimeout(300_000)
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.goto('/login/')
  await page.getByRole('textbox', { name: 'Email Address' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Login' }).click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 120_000 })

  await page.goto(`/projects/${PID}/dashboard/`)
  await page.waitForTimeout(15_000)

  const analysis = page.getByText(/^Analysis \d+$/).first()
  if (await analysis.count()) { await analysis.click(); await page.waitForTimeout(12_000) }
  const tab = page.getByText(/^User stories$/i).first()
  if (await tab.count()) { await tab.click(); await page.waitForTimeout(12_000) }

  // ---- 1. priority badges are numbers ----
  const badges = await page.evaluate(() =>
    Array.from(document.querySelectorAll('div.inline-flex.items-center.rounded-full'))
      .map((b) => ({ text: (b.textContent || '').trim(), title: b.getAttribute('title') }))
      .filter((b) => b.text.length > 0)
      .slice(0, 10)
  )
  console.log('\n---- BADGES ----')
  console.log(JSON.stringify(badges, null, 1))

  const priorityBadges = badges.filter((b) => b.title?.startsWith('Priority '))
  expect(priorityBadges.length, 'expected priority badges to render').toBeGreaterThan(0)
  for (const b of priorityBadges) {
    expect(b.text, `priority badge should be a number, got ${b.text}`).toMatch(/^[1-4]$/)
  }
  // The word form must be gone from the badge face.
  expect(badges.some((b) => /^(critical|high|medium|low)$/i.test(b.text))).toBe(false)

  // ---- 2. quality gate passes on push ----
  const title = page.getByText(/Eliminate room odors/).first()
  await expect(title).toBeVisible({ timeout: 30_000 })
  // The Checkbox renders as div[data-slot=checkbox]; grab the one in this row.
  const row = page.locator('div').filter({ hasText: /Eliminate room odors/ }).last()
  const checkbox = row.locator('[data-slot="checkbox"]').first()
  await checkbox.click({ timeout: 30_000 })
  await page.waitForTimeout(1500)

  await page.getByRole('button', { name: /Configure & Push/i }).click()
  await page.waitForTimeout(8000)

  const gate = page.getByText('Quality Gate Checks')
  const gateShown = await gate.isVisible().catch(() => false)
  if (gateShown) {
    const body = await page.locator('body').innerText()
    console.log('\n---- QUALITY GATE STILL BLOCKING ----')
    console.log(body.split('\n').filter((l) => l.trim()).slice(0, 25).join('\n'))
  }
  expect(gateShown, 'quality gate should not block the push').toBe(false)

  await page.screenshot({ path: 'test-results/priority-and-quality-gate.png' })
})
