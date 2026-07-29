/**
 * Delete must actually delete. The dashboard used to drop the row from Redux
 * only, so the item reappeared after a refresh. These specs delete through the
 * real UI and then hard-reload to prove the row is gone server-side.
 *
 *   E2E_EMAIL=... E2E_PASSWORD=... npx playwright test e2e/user-story-delete.spec.ts
 */
import { test, expect, type Page } from '@playwright/test'

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const PID = process.env.E2E_PROJECT_ID || '11d1d1ff-ec5a-4c7f-8b87-7a04d16a4c09'

async function login(page: Page) {
  await page.goto('/login/')
  await page.getByRole('textbox', { name: 'Email Address' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Login' }).click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 120_000 })
}

/** Open the newest analysis and switch to the User stories tab. */
async function openUserStories(page: Page) {
  await page.goto(`/projects/${PID}/dashboard/`)
  await page.waitForTimeout(12_000)
  const analysis = page.getByText(/^Analysis \d+$/).first()
  if (await analysis.count()) {
    await analysis.click()
    await page.waitForTimeout(10_000)
  }
  const tab = page.getByText(/^User stories$/i).first()
  if (await tab.count()) {
    await tab.click()
    await page.waitForTimeout(10_000)
  }
}

/** Titles of the rendered draft rows. */
async function rowTitles(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('span.truncate'))
      .map((e) => e.textContent?.trim() ?? '')
      .filter((t) => t.length > 25)
  )
}

test.describe('user story delete', () => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD')
  test.setTimeout(420_000)

  test('single-row delete removes the row from the database, not just state', async ({ page }) => {
    // Watch for the delete call so we can assert it actually fired.
    const deleteCalls: { status: number; body: string }[] = []
    page.on('response', async (res) => {
      if (res.url().includes('remove-work-items')) {
        deleteCalls.push({ status: res.status(), body: (await res.text().catch(() => '')).slice(0, 200) })
      }
    })

    await login(page)
    await openUserStories(page)

    const before = await rowTitles(page)
    expect(before.length, 'need at least one user story to delete').toBeGreaterThan(0)
    const victim = before[0]
    console.log(`\nrows before      : ${before.length}`)
    console.log(`deleting         : ${victim.slice(0, 60)}`)

    // Per-row trash button on the first row.
    await page.locator('button[title="Delete this user story"]').first().click()
    await page.waitForTimeout(1500)

    // Confirm in the modal.
    const confirm = page
      .getByRole('button', { name: /^(delete|confirm|yes)/i })
      .last()
    await confirm.click()

    // The API call must happen — this is the whole point of the fix.
    await expect
      .poll(() => deleteCalls.length, { timeout: 45_000, message: 'no remove-work-items request was sent' })
      .toBeGreaterThan(0)
    console.log(`delete API calls : ${JSON.stringify(deleteCalls)}`)
    expect(deleteCalls[0].status, 'delete endpoint returned an error').toBeLessThan(400)

    await page.waitForTimeout(3000)
    const afterDelete = await rowTitles(page)
    console.log(`rows after delete: ${afterDelete.length}`)
    expect(afterDelete, 'row should disappear immediately').not.toContain(victim)

    // The real test: hard reload and re-navigate. Redux is gone; only the DB
    // decides what comes back.
    await openUserStories(page)
    const afterReload = await rowTitles(page)
    console.log(`rows after reload: ${afterReload.length}`)
    console.log(`remaining        : ${afterReload.map((t) => t.slice(0, 45)).join('\n                   ')}\n`)

    expect(afterReload, 'DELETED ROW CAME BACK AFTER RELOAD - delete was state-only').not.toContain(victim)
    expect(afterReload.length).toBe(before.length - 1)
  })

  test('bulk delete removes every selected row and survives a reload', async ({ page }) => {
    const deleteCalls: number[] = []
    page.on('response', (res) => {
      if (res.url().includes('remove-work-items')) deleteCalls.push(res.status())
    })

    await login(page)
    await openUserStories(page)

    const before = await rowTitles(page)
    test.skip(before.length < 2, 'need at least 2 user stories for a bulk delete')

    // The Checkbox renders as <div role="checkbox">, not a <button>, and a
    // blocked one carries no disabled attribute — so click and verify via
    // aria-checked rather than trusting the selector.
    const boxes = page.locator('[data-slot="checkbox"][role="checkbox"]')
    const total = await boxes.count()
    const picked: number[] = []
    for (let i = 0; i < total && picked.length < 2; i++) {
      await boxes.nth(i).click({ force: true })
      await page.waitForTimeout(400)
      if ((await boxes.nth(i).getAttribute('aria-checked')) === 'true') picked.push(i)
    }
    expect(picked.length, 'could not select 2 rows').toBe(2)

    const victims = picked.map((i) => before[i]).filter(Boolean)
    const n = victims.length
    console.log(`\nbulk deleting ${n}: ${victims.map((v) => v.slice(0, 45)).join(' | ')}`)

    await page.getByRole('button', { name: /delete selected/i }).click()
    await page.waitForTimeout(1500)
    await page.getByRole('button', { name: /^(delete|confirm|yes)/i }).last().click()

    await expect
      .poll(() => deleteCalls.length, { timeout: 45_000, message: 'no remove-work-items request was sent' })
      .toBeGreaterThan(0)
    expect(Math.max(...deleteCalls)).toBeLessThan(400)

    await openUserStories(page)
    const afterReload = await rowTitles(page)
    console.log(`rows: ${before.length} -> ${afterReload.length} after reload\n`)
    for (const v of victims) {
      expect(afterReload, `"${v.slice(0, 40)}" came back after reload`).not.toContain(v)
    }
  })
})
