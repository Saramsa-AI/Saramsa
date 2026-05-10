

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

// Standardized token storage keys
const ACCESS_TOKEN_KEY = 'sa_access_token';
const REFRESH_TOKEN_KEY = 'sa_refresh_token';
const USER_STORAGE_KEY = 'sa_user';

// Cookie names expected by middleware
const ACCESS_TOKEN_COOKIE = 'saramsa_access_token';
const REFRESH_TOKEN_COOKIE = 'saramsa_refresh_token';

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof localStorage !== 'undefined';
}

// Check if token is expired
function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const currentTime = Date.now() / 1000;
    return payload.exp < currentTime;
  } catch {
    return true;
  }
}

// Check if token will expire soon (within 5 minutes)
function isTokenExpiringSoon(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const currentTime = Date.now() / 1000;
    const fiveMinutes = 5 * 60;
    return payload.exp < (currentTime + fiveMinutes);
  } catch {
    return true; // If we can't decode, assume expiring soon
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
  const newAccessToken = data.access;
  
  // Update stored access token
  localStorage.setItem(ACCESS_TOKEN_KEY, newAccessToken);
  
  // Update cookie
  const oneHour = 60 * 60;
  document.cookie = `${ACCESS_TOKEN_COOKIE}=${encodeURIComponent(newAccessToken)}; Path=/; Max-Age=${oneHour}; SameSite=Lax`;
  
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

export function logout(): void {
  clearTokens();
  setStoredUser(null);
  
  // Clear any other auth-related localStorage items
  if (typeof window !== 'undefined') {
    // Clear project selections and other session data
    localStorage.removeItem('project_id');
    localStorage.removeItem('selected_platform');
    localStorage.removeItem('azure_organization');
    localStorage.removeItem('azure_pat_token');
    localStorage.removeItem('azure_process_template');
    localStorage.removeItem('jira_email');
    localStorage.removeItem('jira_api_token');
    localStorage.removeItem('jira_domain');
    
    // Redirect to login page
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
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

