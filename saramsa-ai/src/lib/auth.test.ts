import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'
import { login } from './auth'

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
    } as Response)

    await expect(
      login({ email: 'x@y.com', password: 'p' })
    ).rejects.toMatchObject({ message: 'Login failed' })
  })
})
