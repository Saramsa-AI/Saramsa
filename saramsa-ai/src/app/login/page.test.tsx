import { describe, test, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ─── Mocks ──────────────────────────────────────────────────────────────────
// The login page pulls in many things that don't make sense in a unit test:
// the Next router, search params, the useAuth hook, animations, dynamic imports.
// We replace each with a minimal fake so we can focus on the FORM behavior.

const pushMock = vi.fn()
const loginMock = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => new URLSearchParams(),  // no invite token
}))

vi.mock('@/lib/useAuth', () => ({
  useAuth: () => ({ login: loginMock }),
}))

vi.mock('@/lib/apiRequest', () => ({
  apiRequest: vi.fn().mockResolvedValue({ data: { projects: [] } }),
}))

vi.mock('@/lib/auth', () => ({
  acceptInviteAsLoggedInUser: vi.fn(),
}))

// next/dynamic returns a component that renders a placeholder.
// In tests we don't care about the actual logo/theme toggle visuals.
vi.mock('next/dynamic', () => ({
  default: () => () => null,
}))

// framer-motion has SSR quirks; replace its `motion.X` with plain divs/spans.
vi.mock('framer-motion', () => ({
  motion: new Proxy(
    {},
    { get: () => (props: any) => <div {...props} /> }
  ),
}))

// Animation components used inside the page — render nothing.
vi.mock('@/components/ui/animations', () => ({
  DataStream: () => null,
  TaskCards: () => null,
  AIProcessing: () => null,
}))

// ─── System under test (imported AFTER mocks so they take effect) ───────────
import LoginPage from './page'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('LoginPage — rendering', () => {
  test('renders email and password fields plus a Login button', async () => {
    render(<LoginPage />)

    // Wait for the inner Suspense to mount the form
    expect(await screen.findByLabelText(/email address/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument()
  })
})

describe('LoginPage — client-side validation (zod)', () => {
  test('shows error when email is invalid', async () => {
    const user = userEvent.setup()
    render(<LoginPage />)

    // `foo@bar` passes HTML5 type="email" (which jsdom enforces) but fails
    // zod's stricter .email() format check — exactly what we want to verify.
    const emailInput = await screen.findByLabelText(/email address/i)
    await user.type(emailInput, 'foo@bar')
    await user.type(screen.getByLabelText(/password/i), 'secret123')
    await user.click(screen.getByRole('button', { name: /login/i }))

    expect(
      await screen.findByText(/please enter a valid email address/i)
    ).toBeInTheDocument()
    expect(loginMock).not.toHaveBeenCalled()
  })

  test('shows error when password is too short', async () => {
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(
      await screen.findByLabelText(/email address/i),
      'a@b.com'
    )
    await user.type(screen.getByLabelText(/password/i), 'short')
    await user.click(screen.getByRole('button', { name: /login/i }))

    expect(
      await screen.findByText(/password must be at least 6 characters/i)
    ).toBeInTheDocument()
    expect(loginMock).not.toHaveBeenCalled()
  })
})

describe('LoginPage — submit flow', () => {
  test('calls login() with the form values on submit', async () => {
    loginMock.mockResolvedValue({ success: true })
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(
      await screen.findByLabelText(/email address/i),
      'lathiesh@corvusapp.com'
    )
    await user.type(screen.getByLabelText(/password/i), 'secret123')
    await user.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith({
        email: 'lathiesh@corvusapp.com',
        password: 'secret123',
      })
    })
  })

  test('shows server error message when login fails', async () => {
    loginMock.mockResolvedValue({
      success: false,
      error: 'Invalid email or password.',
    })
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(
      await screen.findByLabelText(/email address/i),
      'a@b.com'
    )
    await user.type(screen.getByLabelText(/password/i), 'wrongpass')
    await user.click(screen.getByRole('button', { name: /login/i }))

    expect(
      await screen.findByText(/invalid email or password\./i)
    ).toBeInTheDocument()
  })
})
