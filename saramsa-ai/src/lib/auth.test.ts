import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  login,
  logout,
  setTokens,
  getTokens,
  clearTokens,
  getValidAccessToken,
  isTokenExpiringSoon,
  refreshAccessToken,
  ACCESS_TOKEN_KEY,
  REFRESH_TOKEN_KEY,
  USER_STORAGE_KEY,
} from './auth'

// Helper: build a fetch-style Response object for our mocked fetch
function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  return {
    ok: init.ok ?? (init.status ?? 200) < 400,
    status: init.status ?? 200,
    json: async () => body,
  } as Response
}

// A fake JWT with exp far in the future (year 9999). login() ultimately calls
// getCurrentUser(token), which reads the token but doesn't validate it — and
// setTokens() decodes the token's payload, so we need something parseable.
const FAKE_ACCESS = 'header.' + btoa(JSON.stringify({ exp: 9999999999 })) + '.sig'
const FAKE_REFRESH = 'header.' + btoa(JSON.stringify({ exp: 9999999999 })) + '.sig'

const fakeUser = {
  user_id: 'u-1',
  email: 'lathiesh@corvusapp.com',
  role: 'admin',
  first_name: 'Lathiesh',
  last_name: 'M',
  is_staff: false,
  active_organization_id: 'org-1',
  active_organization: { id: 'org-1', name: 'Corvus' },
  organizations: [{ id: 'org-1', name: 'Corvus' }],
}

beforeEach(() => {
  // Fresh localStorage for every test (jsdom provides one but it persists)
  localStorage.clear()
  // Stub global fetch — we'll customize per test
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('login() — happy path', () => {
  test('returns user + tokens and persists tokens to localStorage', async () => {
    // ARRANGE: fetch is called twice during login():
    //   1) POST /login    → returns access + refresh tokens
    //   2) GET  /me       → returns user profile
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          data: { access: FAKE_ACCESS, refresh: FAKE_REFRESH },
        })
      )
      .mockResolvedValueOnce(
        jsonResponse({ success: true, data: fakeUser })
      )

    // ACT
    const result = await login({
      email: 'lathiesh@corvusapp.com',
      password: 'secret123',
    })

    // ASSERT — return value
    expect(result.access).toBe(FAKE_ACCESS)
    expect(result.refresh).toBe(FAKE_REFRESH)
    expect(result.user.email).toBe('lathiesh@corvusapp.com')

    // ASSERT — tokens were persisted
    expect(localStorage.getItem('sa_access_token')).toBe(FAKE_ACCESS)
    expect(localStorage.getItem('sa_refresh_token')).toBe(FAKE_REFRESH)

    // ASSERT — fetch was called with correct URL + body
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [loginUrl, loginInit] = fetchMock.mock.calls[0]
    expect(loginUrl).toMatch(/\/api\/auth\/login\/$/)
    expect(loginInit?.method).toBe('POST')
    expect(JSON.parse(loginInit?.body as string)).toEqual({
      email: 'lathiesh@corvusapp.com',
      password: 'secret123',
    })
  })
})

describe('login() — error paths', () => {
  test('throws with status 401 when credentials are wrong', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        { error: 'Invalid credentials' },
        { status: 401, ok: false }
      )
    )

    // We expect login() to reject. .rejects lets us assert on the thrown error.
    await expect(
      login({ email: 'x@y.com', password: 'wrong' })
    ).rejects.toMatchObject({
      message: 'Invalid credentials',
      response: { status: 401 },
    })

    // Tokens should NOT be stored on failure
    expect(localStorage.getItem('sa_access_token')).toBeNull()
  })

  test('throws with status 500 on server error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        { detail: 'Internal Server Error' },
        { status: 500, ok: false }
      )
    )

    await expect(
      login({ email: 'x@y.com', password: 'p' })
    ).rejects.toMatchObject({
      message: 'Internal Server Error',
      response: { status: 500 },
    })
  })

  test('falls back to generic message when error body is empty', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => {
        throw new Error('not json')
      },
    } as unknown as Response)

    await expect(
      login({ email: 'x@y.com', password: 'p' })
    ).rejects.toMatchObject({ message: 'Login failed' })
  })
})

// ─────────────────────────────────────────────────────────────────────
// Token storage helpers
//
// Before: 0% coverage on setTokens/getTokens/clearTokens. These manage
// both localStorage AND cookies (for Next.js middleware), so an
// off-by-one in either store breaks the auth flow silently.
// ─────────────────────────────────────────────────────────────────────

describe('setTokens / getTokens / clearTokens', () => {
  // JWTs whose payload base64-decodes to a future exp. Real tokens too,
  // but we just need parseable bytes here.
  const ACCESS = 'h.' + btoa(JSON.stringify({ exp: 9999999999 })) + '.s'
  const REFRESH = 'h.' + btoa(JSON.stringify({ exp: 9999999999 })) + '.s'

  test('setTokens writes both localStorage entries and both cookies', () => {
    setTokens({ access: ACCESS, refresh: REFRESH })

    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(ACCESS)
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBe(REFRESH)
    // jsdom maintains document.cookie state, so we can spot-check that
    // setTokens populated the middleware-readable cookies.
    expect(document.cookie).toContain('saramsa_access_token=')
    expect(document.cookie).toContain('saramsa_refresh_token=')
  })

  test('getTokens returns null when nothing is stored', () => {
    expect(getTokens()).toBeNull()
  })

  test('getTokens returns the pair when both are present and access is fresh', () => {
    setTokens({ access: ACCESS, refresh: REFRESH })
    expect(getTokens()).toEqual({ access: ACCESS, refresh: REFRESH })
  })

  test('clearTokens removes both localStorage entries and zeroes cookies', () => {
    setTokens({ access: ACCESS, refresh: REFRESH })
    clearTokens()
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull()
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
    // jsdom sets Max-Age=0 cookies to immediately expired; we verify
    // the cookies were rewritten to an empty value.
    expect(document.cookie).not.toContain('saramsa_access_token=h.')
  })
})

// ─────────────────────────────────────────────────────────────────────
// getValidAccessToken — what the request interceptor calls before every
// API request. Must return null for expired tokens so the interceptor
// doesn't send Authorization headers with dead bytes.
// ─────────────────────────────────────────────────────────────────────

describe('getValidAccessToken', () => {
  function makeJwt(exp: number): string {
    return 'h.' + btoa(JSON.stringify({ exp })) + '.s'
  }

  test('returns the token when not expired', () => {
    const token = makeJwt(9999999999) // year 2286
    localStorage.setItem(ACCESS_TOKEN_KEY, token)
    expect(getValidAccessToken()).toBe(token)
  })

  test('returns null when token is expired', () => {
    const token = makeJwt(1) // 1970 — definitely expired
    localStorage.setItem(ACCESS_TOKEN_KEY, token)
    expect(getValidAccessToken()).toBeNull()
  })

  test('returns null when no token exists', () => {
    expect(getValidAccessToken()).toBeNull()
  })

  test('returns null when token is malformed (no exp claim)', () => {
    // No exp in payload — Phase 2 cleanup hardened isTokenExpired to
    // treat missing exp as expired. Pin that contract.
    const token = 'h.' + btoa(JSON.stringify({ user_id: 'u1' })) + '.s'
    localStorage.setItem(ACCESS_TOKEN_KEY, token)
    expect(getValidAccessToken()).toBeNull()
  })

  test('returns null when token is garbage', () => {
    localStorage.setItem(ACCESS_TOKEN_KEY, 'not.a.valid.jwt.at.all')
    expect(getValidAccessToken()).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────
// isTokenExpiringSoon — used by the proactive-refresh tick in useAuth.
// Triggers a refresh ~5 minutes before the access token's actual
// expiry so the user never sees a 401.
// ─────────────────────────────────────────────────────────────────────

describe('isTokenExpiringSoon', () => {
  function makeJwt(exp: number): string {
    return 'h.' + btoa(JSON.stringify({ exp })) + '.s'
  }

  test('returns false when token has > 5 min remaining (default threshold)', () => {
    const tenMinutesFromNow = Math.floor(Date.now() / 1000) + 10 * 60
    expect(isTokenExpiringSoon(makeJwt(tenMinutesFromNow))).toBe(false)
  })

  test('returns true when token has < 5 min remaining', () => {
    const twoMinutesFromNow = Math.floor(Date.now() / 1000) + 2 * 60
    expect(isTokenExpiringSoon(makeJwt(twoMinutesFromNow))).toBe(true)
  })

  test('returns true for already-expired tokens', () => {
    expect(isTokenExpiringSoon(makeJwt(1))).toBe(true)
  })

  test('honors custom threshold', () => {
    // Token expires in 30 seconds — within a 60s threshold but not a 10s one.
    const thirtySecondsFromNow = Math.floor(Date.now() / 1000) + 30
    expect(isTokenExpiringSoon(makeJwt(thirtySecondsFromNow), 60)).toBe(true)
    expect(isTokenExpiringSoon(makeJwt(thirtySecondsFromNow), 10)).toBe(false)
  })

  test('returns true for malformed tokens (defense in depth)', () => {
    // No exp claim — safer to assume the token is dying than to keep
    // using something we can't validate.
    expect(isTokenExpiringSoon('not-a-jwt')).toBe(true)
    expect(isTokenExpiringSoon('h.' + btoa('{}') + '.s')).toBe(true)
  })
})

// ─────────────────────────────────────────────────────────────────────
// refreshAccessToken — pins PR #35's fix (persist rotated refresh) AND
// the rotated-refresh contract on the backend side.
// ─────────────────────────────────────────────────────────────────────

describe('refreshAccessToken', () => {
  const FAKE_REFRESH = 'h.' + btoa(JSON.stringify({ exp: 9999999999 })) + '.s'
  const NEW_ACCESS = 'h.' + btoa(JSON.stringify({ exp: 9999999998 })) + '.s'
  const NEW_REFRESH = 'h.' + btoa(JSON.stringify({ exp: 9999999997 })) + '.s'

  test('persists the ROTATED refresh token when the backend returns one (PR #35)', async () => {
    localStorage.setItem(REFRESH_TOKEN_KEY, FAKE_REFRESH)
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ access: NEW_ACCESS, refresh: NEW_REFRESH }),
    } as Response)

    const result = await refreshAccessToken()
    expect(result).toBe(NEW_ACCESS)
    // The CRITICAL assertion: the rotated refresh from the response body
    // is what gets persisted, NOT the old refresh. Before PR #35, this
    // step was a no-op and users got silently logged out every ~1 hour
    // once the backend's BLACKLIST_AFTER_ROTATION killed the old refresh.
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBe(NEW_REFRESH)
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(NEW_ACCESS)
  })

  test('falls back to the existing refresh when backend does not rotate', async () => {
    // Some backend deploys may not have ROTATE_REFRESH_TOKENS on — the
    // response then omits `refresh`. We must keep the existing refresh
    // working in that case rather than nuking it.
    localStorage.setItem(REFRESH_TOKEN_KEY, FAKE_REFRESH)
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ access: NEW_ACCESS }), // no `refresh`
    } as Response)

    await refreshAccessToken()
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBe(FAKE_REFRESH)
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(NEW_ACCESS)
  })

  test('throws and clears tokens when refresh is missing', async () => {
    // No refresh in localStorage — refreshAccessToken should not even
    // try to POST. Verifies we don't make pointless network calls.
    await expect(refreshAccessToken()).rejects.toThrow()
    expect(fetch).not.toHaveBeenCalled()
  })

  test('throws when refresh is itself expired (no network call wasted)', async () => {
    const expiredRefresh = 'h.' + btoa(JSON.stringify({ exp: 1 })) + '.s'
    localStorage.setItem(REFRESH_TOKEN_KEY, expiredRefresh)
    await expect(refreshAccessToken()).rejects.toThrow()
    expect(fetch).not.toHaveBeenCalled()
    // Should have cleared the dead tokens
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
  })

  test('throws when backend returns 401 (refresh blacklisted)', async () => {
    localStorage.setItem(REFRESH_TOKEN_KEY, FAKE_REFRESH)
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Token is blacklisted' }),
    } as Response)

    await expect(refreshAccessToken()).rejects.toThrow()
  })
})

// ─────────────────────────────────────────────────────────────────────
// logout — pins the consolidated cleanup from Phase 2.
// Before consolidation, three different code paths cleared different
// subsets of localStorage. The audit found integration credentials
// (Azure PAT, Jira API token) surviving logout. This test pins the
// full cleanup list.
// ─────────────────────────────────────────────────────────────────────

describe('logout', () => {
  // Mock window.location.href setter so the redirect doesn't actually
  // navigate the test runner. Restored in afterEach via stubGlobal.
  beforeEach(() => {
    // jsdom's location.href is read-only by default; we override via
    // delete + Object.defineProperty so the assignment in logout()
    // doesn't crash. This is jsdom-specific boilerplate.
    Object.defineProperty(window, 'location', {
      value: {
        href: 'http://localhost/dashboard',
        pathname: '/dashboard',
        assign: vi.fn(),
        replace: vi.fn(),
      },
      writable: true,
    })
    // logout() fires a fire-and-forget POST to /api/auth/logout/ — the
    // global fetch mock from the top-level beforeEach is a bare vi.fn()
    // returning undefined, which breaks the .catch() chain. Override to
    // return a resolved Promise so the chain completes.
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    } as Response)
  })

  test('clears tokens, user, and ALL auth-adjacent localStorage keys', () => {
    // Pre-populate the localStorage keys logout should wipe — the
    // exhaustive list flagged by the audit (Azure + Jira + project keys)
    setTokens({ access: 'a', refresh: 'r' })
    localStorage.setItem(USER_STORAGE_KEY, '{"id":"u1"}')
    const keysThatShouldBeCleared = [
      'project_id',
      'selected_platform',
      'selected_project_name',
      'azure_organization',
      'azure_pat_token',
      'azure_process_template',
      'azure_selected_project',
      'azure_project_name',
      'jira_email',
      'jira_api_token',
      'jira_domain',
      'jira_project_key',
      'jira_project_id',
      'jira_project_name',
    ]
    for (const key of keysThatShouldBeCleared) {
      localStorage.setItem(key, 'sensitive-value-' + key)
    }

    logout()

    // Tokens cleared
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull()
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
    // User cleared
    expect(localStorage.getItem(USER_STORAGE_KEY)).toBeNull()
    // Every integration key cleared
    for (const key of keysThatShouldBeCleared) {
      expect(localStorage.getItem(key)).toBeNull()
    }
  })

  test('redirects to /login when not already there', () => {
    logout()
    expect(window.location.href).toBe('/login')
  })

  test('does NOT redirect if already on /login (idempotent)', () => {
    Object.defineProperty(window, 'location', {
      value: {
        href: 'http://localhost/login',
        pathname: '/login',
        assign: vi.fn(),
        replace: vi.fn(),
      },
      writable: true,
    })
    logout()
    // href stayed where it was — no unnecessary navigation
    expect(window.location.href).toBe('http://localhost/login')
  })
})
