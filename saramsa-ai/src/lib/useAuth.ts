
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@/store/hooks';
import { loginUser, registerUser, setUser, logout as sliceLogout } from '@/store/features/auth/authSlice';
import {
  ACCESS_TOKEN_KEY,
  getStoredUser,
  getTokens,
  getCurrentUser,
  isTokenExpiringSoon,
  setStoredUser,
  logout as clientLogout,
  switchActiveOrganization,
  type User,
} from '@/lib/auth';
import { authService } from './authService';

type LoginArgs = { email: string; password: string };
type RegisterArgs = {
  email: string;
  password: string;
  confirmPassword: string;
  invite_token: string;
  first_name?: string;
  last_name?: string;
  role?: 'admin' | 'user' | 'restricted user';
};

type HookResult = {
  user: User | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null | undefined;
  login: (args: LoginArgs) => Promise<{ success: true } | { success: false; error?: string }>;
  register: (
    args: RegisterArgs,
  ) => Promise<{ success: true } | { success: false; error?: string }>;
  logout: () => void;
  refreshToken: () => Promise<boolean>;
  switchOrganization: (organizationId: string) => Promise<{ success: true } | { success: false; error?: string }>;
};

export function useAuth(): HookResult {
  const dispatch = useAppDispatch();
  const auth = useAppSelector((s) => s.auth);
  const [hydrating, setHydrating] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      try {
        const tokens = getTokens();
        const storedUser = getStoredUser();

        if (!tokens) {
          return;
        }

        let validToken = await authService.getValidToken();
        if (!validToken) {
          validToken = await authService.refreshTokenIfNeeded();
        }

        if (!validToken) {
          if (!cancelled) clientLogout();
          return;
        }

        if (storedUser) {
          if (!cancelled) {
            dispatch(setUser(storedUser));
          }
          // Refresh from /me in the background so server-side changes
          // (role/staff promotion, org switches done elsewhere, name edits)
          // propagate without requiring a logout/login round-trip.
          getCurrentUser(validToken)
            .then((fresh) => {
              if (!cancelled) {
                setStoredUser(fresh);
                dispatch(setUser(fresh));
              }
            })
            .catch((err) => {
              // Stale stored user is fine; user keeps using cached state
              // until next active call surfaces the auth error. Logged
              // so a broken /me doesn't go invisible during dev.
              if (typeof console !== 'undefined') {
                console.warn('useAuth: background /me refresh failed', err);
              }
            });
        } else {
          try {
            const user = await getCurrentUser(validToken);
            if (!cancelled) {
              setStoredUser(user);
              dispatch(setUser(user));
            }
          } catch {
            if (!cancelled) clientLogout();
          }
        }
      } finally {
        if (!cancelled) setHydrating(false);
      }
    };
    hydrate();
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  // ─── Cross-tab logout sync ─────────────────────────────────────────
  // When another tab clears the access token from localStorage (because
  // the user logged out there, or hit a 401-refresh-failure that fired
  // logoutAndRedirect), this tab should follow suit. Without this listener
  // Tab B keeps using the dashboard as if logged in until its next API
  // call returns 401 — confusing UX.
  //
  // The `storage` event fires only in OTHER tabs (browsers suppress it
  // in the originating tab), so we only ever see it when something
  // external happened. Event filter: only act when our access-token key
  // was cleared (newValue === null) — ignore unrelated keys and writes.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onStorage = (e: StorageEvent) => {
      if (e.key !== ACCESS_TOKEN_KEY) return;
      if (e.newValue !== null) return;
      // Another tab logged out. Drop our Redux user immediately so the
      // current page stops rendering authenticated content, then run
      // the canonical local cleanup + redirect.
      dispatch(sliceLogout());
      clientLogout();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [dispatch]);

  // ─── Proactive token refresh ───────────────────────────────────────
  // Reactively refreshing after a 401 means EVERY active user sees one
  // failed request per access-token lifetime. We avoid that by checking
  // every ~60 s whether the access token is within 5 min of expiry, and
  // preemptively refreshing if so.
  //
  // The actual refresh logic lives in authService.refreshTokenIfNeeded
  // (which already serializes concurrent refreshes via an internal
  // singleton promise), so we just delegate. On failure we don't react —
  // the next 401 will trigger the reactive flow + handleAuthFailure as
  // before.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const tick = () => {
      const tokens = getTokens();
      if (!tokens?.access) return;
      if (!isTokenExpiringSoon(tokens.access, 5 * 60)) return;
      authService.refreshTokenIfNeeded().catch(() => {
        // Swallow — see comment block above.
      });
    };
    // Fire once on mount to handle the case where we hydrated with a
    // token that's already inside the 5-min window.
    tick();
    const id = window.setInterval(tick, 60 * 1000);
    return () => window.clearInterval(id);
  }, []);

  const login = useCallback<HookResult['login']>(async (args) => {
    try {
      await dispatch(loginUser(args)).unwrap();
      // Reset refresh attempts after successful login
      authService.resetRefreshAttempts();
      return { success: true };
    } catch (e: any) {
      return { success: false, error: e?.message || e || 'Login failed' };
    }
  }, [dispatch]);

  const register = useCallback<HookResult['register']>(async (args) => {
    try {
      await dispatch(registerUser(args)).unwrap();
      // Reset refresh attempts after successful registration
      authService.resetRefreshAttempts();
      return { success: true };
    } catch (e: any) {
      return { success: false, error: e?.message || e || 'Registration failed' };
    }
  }, [dispatch]);

  const logout = useCallback(async () => {
    // Local + cookie cleanup AND redirect both live in clientLogout
    // (auth.ts:logout) — the canonical implementation. We only add the
    // Redux dispatch here because auth.ts has no Redux access.
    // The try/catch is belt-and-suspenders: dispatch shouldn't throw,
    // but if it does we still want clientLogout to fire so the user
    // doesn't get stuck on a page they should no longer see.
    try {
      dispatch(sliceLogout());
    } catch (error) {
      console.error('Logout dispatch error:', error);
    }
    clientLogout();
  }, [dispatch]);

  const switchOrganization = useCallback<HookResult['switchOrganization']>(async (organizationId) => {
    try {
      const updatedUser = await switchActiveOrganization(organizationId);
      dispatch(setUser(updatedUser));
      return { success: true };
    } catch (e: any) {
      return { success: false, error: e?.message || 'Failed to switch organization' };
    }
  }, [dispatch]);

  const refreshToken = useCallback(async (): Promise<boolean> => {
    try {
      const newToken = await authService.refreshTokenIfNeeded();
      return !!newToken;
    } catch (error) {
      console.error('Token refresh failed:', error);
      return false;
    }
  }, []);

  return useMemo(() => ({
    user: auth.user,
    isAuthenticated: auth.isAuthenticated,
    loading: auth.loading || hydrating,
    error: auth.error,
    login,
    register,
    logout,
    refreshToken,
    switchOrganization,
  }), [auth.user, auth.isAuthenticated, auth.loading, auth.error, hydrating, login, register, logout, refreshToken, switchOrganization]);
}


