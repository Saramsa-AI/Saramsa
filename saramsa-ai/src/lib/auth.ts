

export type Organization = {
  id: string;
  name?: string;
  slug?: string;
  role?: string;
};

export type User = {
  id?: string;
  email?: string;
  role?: string;
  user_id?: string;
  first_name?: string;
  last_name?: string;
  is_staff?: boolean;
  active_organization_id?: string;
  active_organization?: Organization | null;
  organizations?: Organization[];
  // Backend sets this when /me or /login could not load the workspace context.
  // Lets the UI distinguish "load failed" from "no memberships" so we can show
  // a retry banner instead of pretending the user has no workspaces.
  organization_context_error?: string | null;
};

type LoginParams = { email: string; password: string };
type RegisterParams = {
  email: string;
  password: string;
  confirmPassword: string;
  first_name?: string;
  last_name?: string;
  invite_token: string;
  role?: 'admin' | 'user' | 'restricted user';
};

export type InviteContext = {
  id: string;
  email: string;
  role: string;
  organization: { id: string; name?: string; slug?: string };
  expires_at: string;
};

type Tokens = { access: string; refresh: string };

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, '') || 'http://localhost:8000';
const AUTH_BASE = `${API_BASE_URL}/api/auth`;

// Standardized token storage keys. Exported so callers (e.g. the axios
// interceptor in apiRequest.ts) can remove or read them via the canonical
// names rather than re-inlining the literal string.
export const ACCESS_TOKEN_KEY = 'sa_access_token';
export const REFRESH_TOKEN_KEY = 'sa_refresh_token';
export const USER_STORAGE_KEY = 'sa_user';

// Cookie names expected by middleware
const ACCESS_TOKEN_COOKIE = 'saramsa_access_token';
const REFRESH_TOKEN_COOKIE = 'saramsa_refresh_token';

// Single source of truth for the login route. 7+ places previously
// inlined the literal '/login' — pull them into this constant so a
// future route rename is a one-line change.
export const LOGIN_PATH = '/login';

// localStorage keys that are auth-adjacent and should be cleared on
// logout. Centralizing the list means new integration writes only need
// to add their key here (instead of in 3 different cleanup handlers).
// Tokens (`sa_access_token`, `sa_refresh_token`) are handled separately
// by clearTokens(); `sa_user` by setStoredUser(null).
const AUTH_ADJACENT_LOCALSTORAGE_KEYS = [
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
] as const;

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof localStorage !== 'undefined';
}

// Check if token is expired.
// Decodes the JWT payload (without verifying the signature — backend is the
// authoritative check) and compares `exp` to now. Returns true on any of:
//   - token can't be split / base64-decoded / JSON-parsed (malformed)
//   - payload has no `exp` claim or it isn't a number (malformed)
//   - exp <= now (expired or expiring this instant)
//
// We deliberately use <= for the time comparison: if the claim is "expires
// at exactly 12:00:00.000" and clock is 12:00:00.000, the token is dead.
function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const exp = payload?.exp;
    if (typeof exp !== 'number') {
      // Missing or non-numeric exp = treat as expired. A JWT without exp
      // is a malformed token from our backend's perspective.
      return true;
    }
    const currentTime = Date.now() / 1000;
    return exp <= currentTime;
  } catch {
    return true;
  }
}

/**
 * Will this token expire within `withinSeconds` from now?
 *
 * Used by the proactive-refresh background tick in useAuth: rather than
 * reactively refreshing AFTER a 401 (which surfaces a failed request in
 * the user's session), we check every minute and refresh ~5 minutes
 * before expiry so the user never sees a 401.
 *
 * Returns true for any unparseable/malformed token (safer to assume the
 * token is about to die than to keep using something we can't validate).
 */
export function isTokenExpiringSoon(token: string, withinSeconds: number = 5 * 60): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const exp = payload?.exp;
    if (typeof exp !== 'number') return true;
    const currentTime = Date.now() / 1000;
    return exp - currentTime <= withinSeconds;
  } catch {
    return true;
  }
}

export function setTokens(tokens: Tokens): void {
  if (!isBrowser()) return;
  
  // Store tokens in localStorage
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access);
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh);
  
  // Also set cookies so Next.js middleware can detect auth on server edge
  // Access token ~1 hour, Refresh token ~7 days
  const oneHour = 60 * 60;
  const sevenDays = 7 * 24 * 60 * 60;
  
  document.cookie = `${ACCESS_TOKEN_COOKIE}=${encodeURIComponent(tokens.access)}; Path=/; Max-Age=${oneHour}; SameSite=Lax`;
  document.cookie = `${REFRESH_TOKEN_COOKIE}=${encodeURIComponent(tokens.refresh)}; Path=/; Max-Age=${sevenDays}; SameSite=Lax`;
}

export function getTokens(): Tokens | null {
  if (!isBrowser()) return null;
  
  const access = localStorage.getItem(ACCESS_TOKEN_KEY);
  const refresh = localStorage.getItem(REFRESH_TOKEN_KEY);
  
  if (!access || !refresh) return null;
  
  // Check if access token is expired
  if (isTokenExpired(access)) {
    // If access token is expired but refresh token exists, try to refresh
    if (refresh && !isTokenExpired(refresh)) {
      // Trigger token refresh (this will be handled by the axios interceptor)
      return { access, refresh };
    }
    // Both tokens are expired, clear them
    clearTokens();
    return null;
  }
  
  return { access, refresh };
}

export function getValidAccessToken(): string | null {
  if (!isBrowser()) return null;
  
  const access = localStorage.getItem(ACCESS_TOKEN_KEY);
  if (!access) return null;
  
  // Check if token is expired
  if (isTokenExpired(access)) {
    return null;
  }
  
  return access;
}

export function clearTokens(): void {
  if (!isBrowser()) return;
  
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  
  // Clear cookies
  document.cookie = `${ACCESS_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
  document.cookie = `${REFRESH_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export function setStoredUser(user: User | null): void {
  if (!isBrowser()) return;
  if (user) {
    // Strip transient health signals before persisting. organization_context_error
    // is a snapshot of "did the org service fail on this particular response" —
    // re-rendering it from localStorage would surface a stale "Workspace
    // unavailable — retry" chip on every page load until /me succeeds, even
    // when the org service is currently healthy.
    const persistable: User = { ...user };
    delete persistable.organization_context_error;
    localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(persistable));
  } else {
    localStorage.removeItem(USER_STORAGE_KEY);
  }
}

export function getStoredUser(): User | null {
  if (!isBrowser()) return null;
  const raw = localStorage.getItem(USER_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export async function getCurrentUser(accessToken?: string): Promise<User> {
  const token = accessToken || getValidAccessToken();
  if (!token) {
    throw new Error('Not authenticated');
  }

  const res = await fetch(`${AUTH_BASE}/me/`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });

  if (!res.ok) {
    if (res.status === 401) {
      // Token is invalid, clear it
      clearTokens();
      throw new Error('Authentication expired');
    }
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Failed to load profile';
    throw new Error(message);
  }

  const response = (await res.json()) as {
    success: boolean;
    data: {
      user_id?: string;
      id?: string;
      email?: string;
      role?: string;
      first_name?: string;
      last_name?: string;
      is_staff?: boolean;
      active_organization_id?: string | null;
      active_organization?: Organization | null;
      organizations?: Organization[];
      organization_context_error?: string | null;
    };
    message?: string;
  };

  const data = response.data;

  const user: User = {
    id: data.user_id || data.id,
    user_id: data.user_id || data.id,
    email: data.email,
    role: data.role,
    first_name: data.first_name,
    last_name: data.last_name,
    is_staff: data.is_staff ?? false,
    active_organization_id: data.active_organization_id ?? undefined,
    active_organization: data.active_organization ?? null,
    organizations: data.organizations ?? [],
    organization_context_error: data.organization_context_error ?? null,
  };

  return user;
}

export async function refreshAccessToken(): Promise<string> {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }

  if (isTokenExpired(refreshToken)) {
    clearTokens();
    throw new Error('Refresh token expired');
  }

  const res = await fetch(`${AUTH_BASE}/refresh/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ refresh: refreshToken }),
  });

  if (!res.ok) {
    if (res.status === 401) {
      // Refresh token is invalid, clear all tokens
      clearTokens();
      throw new Error('Refresh token invalid');
    }
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Token refresh failed';
    throw new Error(message);
  }

  const data = await res.json();
  const newAccessToken: string = data.access;
  // Backend's SIMPLE_JWT is configured with ROTATE_REFRESH_TOKENS=True and
  // BLACKLIST_AFTER_ROTATION=True (apis/settings.py). That means every
  // successful refresh:
  //   1. Returns a NEW refresh token in `data.refresh`
  //   2. Blacklists the refresh token we just used
  //
  // Previously we only persisted the new access token and kept reusing the
  // OLD refresh token. On the next refresh (~1 hour later when access
  // expires again) the backend would reject the now-blacklisted refresh →
  // user gets silently logged out. Every active user hit this exactly once
  // per access-token lifetime (1h), seeing it as a "random session timeout".
  //
  // Fix: persist the rotated refresh too. Fall back to the incoming refresh
  // token if the backend ever stops rotating (e.g., dev environment with
  // ROTATE_REFRESH_TOKENS=False) — that keeps behavior identical to before.
  const rotatedRefreshToken: string | undefined = data.refresh;
  const refreshToPersist = rotatedRefreshToken ?? refreshToken;

  setTokens({ access: newAccessToken, refresh: refreshToPersist });

  return newAccessToken;
}

export async function login(params: LoginParams): Promise<{ user: User } & Tokens> {
  const res = await fetch(`${AUTH_BASE}/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });

  if (!res.ok) {
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Login failed';
    const err: any = new Error(message);
    (err.response = { status: res.status, data }), (err.code = undefined);
    throw err;
  }

  const response = (await res.json()) as {
    success: boolean;
    data: Tokens;
    message?: string;
  };
  const tokenData = response.data;
  setTokens(tokenData);

  const user = await getCurrentUser(tokenData.access);
  return { user, ...tokenData };
}

export async function register(
  params: RegisterParams,
): Promise<{ user: User } & Tokens> {
  const res = await fetch(`${AUTH_BASE}/register/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ 
      ...params, 
      role: params.role || 'admin'
    }),
  });

  if (!res.ok) {
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Registration failed';
    const err: any = new Error(message);
    (err.response = { status: res.status, data }), (err.code = undefined);
    throw err;
  }

  const response = (await res.json()) as {
    success: boolean;
    data: {
      access: string;
      refresh: string;
      email?: string;
      user_id?: string;
    };
    message?: string;
  };

  const tokens: Tokens = { access: response.data.access, refresh: response.data.refresh };
  setTokens(tokens);

  const user = await getCurrentUser(tokens.access);
  return { user, ...tokens };
}

export async function lookupInvite(token: string): Promise<InviteContext> {
  const res = await fetch(`${AUTH_BASE}/invites/${encodeURIComponent(token)}/`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Invite link is invalid.';
    throw new Error(message);
  }
  const response = (await res.json()) as { success: boolean; data: InviteContext };
  return response.data;
}

export async function acceptInviteAsLoggedInUser(token: string): Promise<User> {
  const access = getValidAccessToken();
  if (!access) throw new Error('Not authenticated');

  const res = await fetch(`${AUTH_BASE}/invites/${encodeURIComponent(token)}/accept/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${access}` },
  });
  if (!res.ok) {
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Failed to accept invitation.';
    throw new Error(message);
  }
  const response = (await res.json()) as {
    success: boolean;
    data: { user: User; access: string; refresh: string };
  };
  setTokens({ access: response.data.access, refresh: response.data.refresh });
  setStoredUser(response.data.user);
  return response.data.user;
}

export async function switchActiveOrganization(organizationId: string): Promise<User> {
  const token = getValidAccessToken();
  if (!token) throw new Error('Not authenticated');

  const res = await fetch(`${AUTH_BASE}/organizations/active/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ organization_id: organizationId }),
  });

  if (!res.ok) {
    const data = await safeJson(res);
    const message = (data && (data.error || data.detail)) || 'Failed to switch organization';
    throw new Error(message);
  }

  const response = (await res.json()) as {
    success: boolean;
    data: {
      user: User;
      access: string;
      refresh: string;
    };
  };

  setTokens({ access: response.data.access, refresh: response.data.refresh });
  setStoredUser(response.data.user);
  return response.data.user;
}

/**
 * Single source of truth for logging out. Three places used to do their
 * own version of this (auth.ts, useAuth.ts, apiRequest.ts handleAuthFailure)
 * with different localStorage cleanup lists — that drift produced bugs
 * like Azure/Jira project keys surviving logout. After Phase 2 cleanup,
 * every logout path funnels through here.
 *
 * Steps:
 *   1. Clear access + refresh tokens (localStorage + cookies)
 *   2. Clear stored user (sa_user)
 *   3. Clear auth-adjacent localStorage keys (single list above)
 *   4. Redirect to LOGIN_PATH unless we're already there
 *
 * Redux cleanup is NOT done here (this module has no Redux import).
 * Callers with access to dispatch should dispatch sliceLogout() too.
 */
export function logout(): void {
  // Fire-and-forget backend logout to blacklist the refresh token. We do
  // NOT await it — local cleanup must always proceed even if the network
  // is dead or the backend rejects. The user clicked logout and they
  // expect to be logged out; surfacing backend errors here would be a
  // worse UX than silently best-effort'ing the server-side revoke.
  //
  // The endpoint is idempotent (accepts missing/invalid/blacklisted
  // refresh tokens and still returns 200) so we don't need to check the
  // response status either.
  const refreshToken =
    typeof window !== 'undefined'
      ? localStorage.getItem(REFRESH_TOKEN_KEY)
      : null;
  if (refreshToken) {
    fetch(`${AUTH_BASE}/logout/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: refreshToken }),
      // keepalive lets the request survive page navigation — important
      // because we call window.location.href below to redirect.
      keepalive: true,
    }).catch(() => {
      // Network/server failure on logout is non-fatal — the local cleanup
      // already happened. Swallow the rejection so we don't surface a
      // console.error for an expected non-issue.
    });
  }

  clearTokens();
  setStoredUser(null);

  if (typeof window !== 'undefined') {
    for (const key of AUTH_ADJACENT_LOCALSTORAGE_KEYS) {
      localStorage.removeItem(key);
    }

    if (window.location.pathname !== LOGIN_PATH) {
      window.location.href = LOGIN_PATH;
    }
  }
}

async function safeJson(res: Response): Promise<any | null> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

