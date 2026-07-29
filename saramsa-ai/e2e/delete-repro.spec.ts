/**
 * REPRO: "IDs are required for removal." (400) on delete.
 * Captures the outgoing PUT body for both the per-row trash button and the
 * header "Delete selected" path.
 *
 *   E2E_EMAIL=... E2E_PASSWORD=... npx playwright test e2e/delete-repro.spec.ts
 */
import { test } from '@playwright/test'

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const PID = process.env.E2E_PROJECT_ID || '11d1d1ff-ec5a-4c7f-8b87-7a04d16a4c09'

test('capture delete payload for row + bulk paths', async ({ page }) => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD')
  test.setTimeout(300_000)
  await page.setViewportSize({ width: 1440, height: 900 })

  const sent: any[] = []
  page.on('request', (r) => {
    if (r.url().includes('remove-work-items')) {
      let body: any = r.postData()
      try { body = JSON.parse(body) } catch {}
      sent.push({ method: r.method(), body })
    }
  })
  page.on('response', async (r) => {
    if (r.url().includes('remove-work-items')) {
      sent.push({ status: r.status(), detail: (await r.text().catch(() => '')).slice(0, 160) })
    }
  })

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

  // ---------- path A: per-row trash button ----------
  const trash = page.locator('button[title="Delete this user story"]').first()
  console.log('row trash buttons found:', await page.locator('button[title="Delete this user story"]').count())
  if (await trash.count()) {
    await trash.click()
    await page.waitForTimeout(1500)
    const confirm = page.getByRole('button', { name: /^(Delete|Confirm)/i }).last()
    if (await confirm.count()) { await confirm.click(); await page.waitForTimeout(6000) }
    else console.log('!! confirm button not found in modal')
  }
  console.log('\n--- after ROW delete ---')
  console.log(JSON.stringify(sent, null, 1))

  // ---------- path B: checkbox + Delete selected ----------
  sent.length = 0
  const box = page.locator('button[role="checkbox"], input[type="checkbox"]').first()
  if (await box.count()) { await box.click(); await page.waitForTimeout(1200) }
  const bulk = page.getByRole('button', { name: /Delete selected/i }).first()
  if (await bulk.count()) {
    await bulk.click()
    await page.waitForTimeout(1500)
    const confirm = page.getByRole('button', { name: /^(Delete|Confirm)/i }).last()
    if (await confirm.count()) { await confirm.click(); await page.waitForTimeout(6000) }
  }
  console.log('\n--- after BULK delete ---')
  console.log(JSON.stringify(sent, null, 1))
})
