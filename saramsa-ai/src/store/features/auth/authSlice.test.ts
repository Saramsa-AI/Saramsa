import { describe, test, expect, vi, beforeEach } from 'vitest'

// We mock @/lib/auth because some reducers (setUser, updateUser) call
// authApi.setStoredUser() which writes to localStorage. In a unit test
// we don't want real side effects — we replace those functions with no-ops.
vi.mock('@/lib/auth', () => ({
  setStoredUser: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
}))

import authReducer, {
  loginUser,
  logout,
  setUser,
  clearError,
} from './authSlice'

// A reusable fake user we'll pretend the API returned
const fakeUser = {
  id: 'u-1',
  email: 'lathiesh@corvusapp.com',
  first_name: 'Lathiesh',
  last_name: 'M',
  role: 'admin',
}

// The expected initial state — pulled from authSlice.ts
const initialState = {
  user: null,
  isAuthenticated: false,
  loading: false,
  error: null,
}

beforeEach(() => {
  // Reset all mock call history before each test so they don't bleed across
  vi.clearAllMocks()
})

describe('authSlice — initial state', () => {
  test('starts with no user and not authenticated', () => {
    // Calling the reducer with `undefined` state + an unknown action
    // returns the initial state. This is the standard Redux pattern.
    const state = authReducer(undefined, { type: 'unknown' })
    expect(state).toEqual(initialState)
  })
})

describe('authSlice — synchronous reducers', () => {
  test('logout clears user and isAuthenticated', () => {
    // ARRANGE: simulate a logged-in state
    const loggedInState = {
      user: fakeUser,
      isAuthenticated: true,
      loading: false,
      error: null,
    }

    // ACT: dispatch logout
    const state = authReducer(loggedInState, logout())

    // ASSERT
    expect(state.user).toBeNull()
    expect(state.isAuthenticated).toBe(false)
    expect(state.error).toBeNull()
  })

  test('clearError sets error back to null', () => {
    const stateWithError = { ...initialState, error: 'Something went wrong' }
    const state = authReducer(stateWithError, clearError())
    expect(state.error).toBeNull()
  })

  test('setUser stores the user and marks authenticated', () => {
    const state = authReducer(initialState, setUser(fakeUser))
    expect(state.user).toEqual(fakeUser)
    expect(state.isAuthenticated).toBe(true)
    expect(state.error).toBeNull()
  })
})

describe('authSlice — loginUser async thunk lifecycle', () => {
  test('loginUser.pending sets loading=true and clears any prior error', () => {
    // ARRANGE: a state that already has a leftover error from a previous attempt
    const previousState = { ...initialState, error: 'Old error' }

    // ACT: simulate the "pending" action firing
    // We use loginUser.pending.type instead of the magic string 'auth/loginUser/pending'
    const state = authReducer(previousState, { type: loginUser.pending.type })

    // ASSERT
    expect(state.loading).toBe(true)
    expect(state.error).toBeNull()
  })

  test('loginUser.fulfilled stores the user and marks authenticated', () => {
    // ARRANGE: we were loading
    const loadingState = { ...initialState, loading: true }

    // ACT: the thunk resolved successfully with our fake user as payload
    const state = authReducer(loadingState, {
      type: loginUser.fulfilled.type,
      payload: fakeUser,
    })

    // ASSERT
    expect(state.loading).toBe(false)
    expect(state.user).toEqual(fakeUser)
    expect(state.isAuthenticated).toBe(true)
    expect(state.error).toBeNull()
  })

  test('loginUser.rejected stores the error message and keeps user logged out', () => {
    const loadingState = { ...initialState, loading: true }

    const state = authReducer(loadingState, {
      type: loginUser.rejected.type,
      payload: 'Invalid email or password. Please check your credentials.',
    })

    expect(state.loading).toBe(false)
    expect(state.isAuthenticated).toBe(false)
    expect(state.user).toBeNull()
    expect(state.error).toBe('Invalid email or password. Please check your credentials.')
  })

  test('loginUser.rejected falls back to a default message if no payload', () => {
    const state = authReducer(initialState, {
      type: loginUser.rejected.type,
      payload: undefined,
    })
    expect(state.error).toBe('Login failed.')
  })
})
