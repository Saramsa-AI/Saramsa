
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@/store/hooks';
import { loginUser, registerUser, setUser, logout as sliceLogout } from '@/store/features/auth/authSlice';
import { getStoredUser, getTokens, getCurrentUser, setStoredUser, logout as clientLogout, switchActiveOrganization, type User } from '@/lib/auth';
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
    try {
      // Clear client-side auth state
      clientLogout();
      // Clear Redux state
      dispatch(sliceLogout());
      // Force redirect to login page
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    } catch (error) {
      console.error('Logout error:', error);
      // Force redirect even if logout fails
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    }
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


