/**
 * MEASUREMENT (real backend): work out the exact height the Feature Level
 * Sentiments list should be so that (a) exactly 4 rows are visible and
 * (b) the pie chart below peeks into a 100vh viewport.
 *
 *   E2E_EMAIL=... E2E_PASSWORD=... npx playwright test e2e/measure-insights-layout.spec.ts
 */
import { test, expect } from '@playwright/test'

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const PROJECT_ID = process.env.E2E_PROJECT_ID || ''

test('measure feature list + pie chart position', async ({ page }) => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD')
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.goto('/login/')
  await page.getByRole('textbox', { name: 'Email Address' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Login' }).click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 90_000 })

  // Land on a project dashboard that has analysis results.
  if (PROJECT_ID) {
    await page.goto(`/projects/${PROJECT_ID}/dashboard/`)
  }
  await expect(page.getByText('Feature Level Sentiments')).toBeVisible({ timeout: 120_000 })
  await page.waitForTimeout(8000)

  const m = await page.evaluate(() => {
    const vh = window.innerHeight
    // The scrollable feature list is the element with max-height + overflow-y
    const list = document.querySelector('.space-y-2.overflow-y-auto') as HTMLElement | null
    const rows = list ? Array.from(list.children) as HTMLElement[] : []
    const rowH = rows.length ? rows[0].getBoundingClientRect().height : null
    const gap = rows.length > 1
      ? rows[1].getBoundingClientRect().top - rows[0].getBoundingClientRect().bottom
      : null

    // Find the pie chart container (recharts renders an svg)
    const svgs = Array.from(document.querySelectorAll('svg.recharts-surface')) as SVGElement[]
    const pie = svgs.length ? svgs[0].getBoundingClientRect() : null

    return {
      viewportHeight: vh,
      listClientHeight: list?.clientHeight ?? null,
      listScrollHeight: list?.scrollHeight ?? null,
      listTopInPage: list ? list.getBoundingClientRect().top + window.scrollY : null,
      rowCount: rows.length,
      rowHeight: rowH,
      gap,
      pieTopInPage: pie ? pie.top + window.scrollY : null,
      pieHeight: pie ? pie.height : null,
    }
  })

  console.log('\n================ LAYOUT MEASUREMENTS ================')
  console.log(JSON.stringify(m, null, 2))
  if (m.rowHeight && m.gap !== null) {
    const four = m.rowHeight * 4 + m.gap * 3
    const five = m.rowHeight * 5 + m.gap * 4
    console.log(`\n  height for exactly 4 rows : ${Math.round(four)}px`)
    console.log(`  height for 5 rows         : ${Math.round(five)}px`)
    console.log(`  => set max-h between ${Math.round(four)} and ${Math.round(five) - 1} to show 4 (+peek of 5th)`)
    if (m.pieTopInPage !== null && m.listClientHeight !== null) {
      const shrink = m.listClientHeight - four
      console.log(`  current list height       : ${m.listClientHeight}px  (shrink by ${Math.round(shrink)}px)`)
      console.log(`  pie top currently at      : ${Math.round(m.pieTopInPage)}px (viewport ${m.viewportHeight}px)`)
      console.log(`  pie top after shrink      : ${Math.round(m.pieTopInPage - shrink)}px`)
      console.log(`  => pie visible in 100vh?  : ${m.pieTopInPage - shrink < m.viewportHeight ? 'YES' : 'NO, needs more'}`)
    }
  }
  console.log('=====================================================\n')
})
