/**
 * E2E verification for the four Work Items / User Stories (review queue) fixes:
 *
 *   1. "Select All" is reachable before anything is selected
 *      (it used to live inside the `selectedIds.length > 0` guard, so it could
 *      never be the first action you took).
 *   2. Merge is wired up — the backend endpoint + service existed all along but
 *      no UI ever called it, and the GitMerge icon was an unused import.
 *   3. Dismiss/Snooze dropdowns close on outside-click and Escape, and opening
 *      one row's menu closes another row's.
 *   4. The list paginates instead of mounting every candidate at once
 *      (real queues hit 150+, each a framer-motion `layout` component).
 *
 * Backend strategy matches the other specs in this folder: everything is
 * stubbed via page.route(). That keeps the run deterministic, needs no
 * credentials, and — importantly for the merge case — never mutates real data.
 */

import { test, expect, type Page } from '@playwright/test'

const FUTURE_EXP = Math.floor(Date.now() / 1000) + 60 * 60
const ACCESS_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  btoa(JSON.stringify({ exp: FUTURE_EXP, user_id: 'u1', token_type: 'access' })),
  'sig',
].join('.')
const REFRESH_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  btoa(JSON.stringify({ exp: FUTURE_EXP + 7 * 24 * 60 * 60, user_id: 'u1', token_type: 'refresh' })),
  'sig',
].join('.')

const PROJECT_ID = '11d1d1ff-ec5a-4c7f-8b87-7a04d16a4c09'

const FAKE_USER = {
  user_id: 'u1',
  email: 'alice@example.com',
  role: 'admin',
  first_name: 'Alice',
  last_name: 'Tester',
  is_staff: false,
  active_organization_id: 'org-1',
  active_organization: { id: 'org-1', name: 'Acme', slug: 'acme' },
  organizations: [{ id: 'org-1', name: 'Acme', slug: 'acme' }],
}

/** 158 candidates — mirrors the real NewProject queue size. */
const TOTAL = 158
function makeCandidates(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `cand-${i + 1}`,
    title: `Candidate ${i + 1} — expand feedback taxonomy`,
    description: `Description for candidate ${i + 1}.`,
    priority: ['critical', 'high', 'medium', 'low'][i % 4],
    status: 'pending',
    feature_area: 'Cleanliness',
    comment_count: (i % 5) + 1,
    createdAt: '2026-04-03T00:00:00Z',
    analysis_id: `insight_${(i % 18) + 1}`,
  }))
}

/** Seed auth + stub every endpoint the review page touches. */
async function setup(page: Page, onMerge?: (body: any) => void) {
  // Rows are framer-motion `layout` components; their re-layout on open/close
  // detaches and remounts sibling nodes mid-click. Reduced motion makes the
  // DOM stable enough to assert against without arbitrary waits.
  await page.emulateMedia({ reducedMotion: 'reduce' })

  await page.route('**/api/auth/me/', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: FAKE_USER }),
    }),
  )
  await page.route('**/api/work-items/review/stats/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { pending: TOTAL, approved_this_week: 0, dismissed_this_week: 0, snoozed: 0 },
      }),
    }),
  )
  await page.route('**/api/work-items/review/merge/**', async (route) => {
    onMerge?.(route.request().postDataJSON())
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { candidate: {} } }),
    })
  })
  // Must come AFTER the more specific /review/stats/ and /review/merge/ routes.
  await page.route('**/api/work-items/review/?**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { candidates: makeCandidates(TOTAL), count: TOTAL } }),
    }),
  )

  // Two separate auth surfaces, both required:
  //  - localStorage (sa_* keys, per src/lib/auth.ts) for the client app
  //  - a `saramsa_access_token` cookie, which src/middleware.ts checks
  //    server-side before it will render a protected route at all.
  await page.addInitScript(
    ([access, refresh, user]) => {
      localStorage.setItem('sa_access_token', access as string)
      localStorage.setItem('sa_refresh_token', refresh as string)
      localStorage.setItem('sa_user', JSON.stringify(user))
    },
    [ACCESS_TOKEN, REFRESH_TOKEN, FAKE_USER],
  )
  await page.context().addCookies([
    { name: 'saramsa_access_token', value: ACCESS_TOKEN, url: 'http://localhost:3001' },
  ])

  // The route segment is normally an encrypted project id, but the page falls
  // back to using the raw segment when it isn't valid ciphertext.
  await page.goto(`/projects/${PROJECT_ID}/review/`)
  await expect(page.getByRole('heading', { level: 1, name: 'User Stories' })).toBeVisible()
  await expect(page.getByText('Candidate 1 —', { exact: false })).toBeVisible()
}

test.describe('Review queue fixes', () => {
  test('1. Select All is available before anything is selected', async ({ page }) => {
    await setup(page)

    // The bug: the whole batch bar (Select All included) was hidden until
    // something was already selected.
    const selectAll = page.getByRole('button', { name: 'Select All' })
    await expect(selectAll).toBeVisible()
    await expect(page.getByText(`${TOTAL} pending`)).toBeVisible()

    // Approve All / Clear stay hidden until there IS a selection.
    await expect(page.getByRole('button', { name: 'Approve All' })).toHaveCount(0)

    await selectAll.click()
    await expect(page.getByText(`${TOTAL} selected`)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Approve All' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Clear' })).toBeVisible()
  })

  test('2. list paginates at 25 and loads more on demand', async ({ page }) => {
    await setup(page)

    const rows = page.getByRole('heading', { level: 3 })
    await expect(rows).toHaveCount(25)
    await expect(page.getByText(`Showing 25 of ${TOTAL}`)).toBeVisible()

    await page.getByRole('button', { name: /Load 25 more/ }).click()
    await expect(rows).toHaveCount(50)
    await expect(page.getByText(`Showing 50 of ${TOTAL}`)).toBeVisible()
  })

  test('3. Dismiss/Snooze menus close on outside click, Escape, and across rows', async ({ page }) => {
    await setup(page)

    const dismissButtons = page.getByRole('button', { name: 'Dismiss' })
    const snoozeButtons = page.getByRole('button', { name: 'Snooze' })

    // Outside click closes it.
    await dismissButtons.first().click()
    await expect(page.getByRole('button', { name: 'Not relevant' })).toBeVisible()
    await page.getByRole('heading', { level: 1, name: 'User Stories' }).click()
    await expect(page.getByRole('button', { name: 'Not relevant' })).toHaveCount(0)

    // Escape closes it.
    await dismissButtons.first().click()
    await expect(page.getByRole('button', { name: 'Not relevant' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Not relevant' })).toHaveCount(0)

    // Opening a menu on another row closes the first one — previously both
    // stayed open because each row owns its own state.
    await dismissButtons.first().click()
    await expect(page.getByRole('button', { name: 'Not relevant' })).toBeVisible()
    // Target a row several below: an open menu physically overlays the rows
    // immediately beneath it, so clicking those would be intercepted by the
    // menu itself rather than testing the close-on-open behaviour.
    await page.getByRole('button', { name: 'Snooze' }).nth(4).click()
    await expect(page.getByRole('button', { name: 'Not relevant' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '1 week' })).toBeVisible()
  })

  test('4. Merge appears only with a target selected and posts the right payload', async ({ page }) => {
    const mergeCalls: any[] = []
    await setup(page, (body) => mergeCalls.push(body))

    // No selection -> no Merge button anywhere (previously merge had NO UI at all).
    await expect(page.getByRole('button', { name: 'Merge' })).toHaveCount(0)

    // Select one row as the keeper/target.
    await page.getByRole('checkbox').first().check()
    await expect(page.getByText(/Merge target selected/)).toBeVisible()

    // Merge shows on the OTHER rows, but never on the target itself.
    const mergeButtons = page.getByRole('button', { name: 'Merge' })
    await expect(mergeButtons).toHaveCount(24) // 25 rendered - the selected target

    await mergeButtons.first().click()

    await expect.poll(() => mergeCalls.length).toBe(1)
    expect(mergeCalls[0]).toMatchObject({
      source_candidate_id: 'cand-2',
      target_candidate_id: 'cand-1',
      project_id: PROJECT_ID,
    })
  })
})
