import axios, { AxiosRequestConfig, AxiosResponse, Method, AxiosError } from 'axios';
import {
  getValidAccessToken,
  getTokens,
  refreshAccessToken,
  logout as logoutAndRedirect,
} from './auth';

// Extend AxiosRequestConfig to include our custom skipAuth property
declare module 'axios' {
  export interface AxiosRequestConfig {
    skipAuth?: boolean;
  }
}

// Compute API base in a robust way to support either NEXT_PUBLIC_API_URL (including /api)
// or NEXT_PUBLIC_API_BASE_URL (server root without /api)
const RAW_API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '');
const RAW_API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, '');

const API_BASE: string = (
  RAW_API_URL || (RAW_API_BASE_URL ? `${RAW_API_BASE_URL}/api` : 'http://127.0.0.1:8000/api')
).replace(/\/$/, '');

// Create axios instance with interceptors
const axiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 120000, // 2 minutes - reasonable for LLM operations
});

// Flag to prevent multiple refresh attempts
let isRefreshing = false;
let failedQueue: Array<{
  resolve: (value?: any) => void;
  reject: (error?: any) => void;
}> = [];

const processQueue = (error: any, token: string | null = null) => {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) {
      reject(error);
    } else {
      resolve(token);
    }
  });
  
  failedQueue = [];
};

// Request interceptor to add auth token (only if not explicitly disabled)
axiosInstance.interceptors.request.use(
  (config) => {
    // Check if auth is explicitly disabled for this request
    if (config.skipAuth !== true) {
      const token = getValidAccessToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle token refresh and auth errors
axiosInstance.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as any;
    
    // Only handle 401 errors for requests that were supposed to be authenticated
    if (error.response?.status === 401 && !originalRequest._retry && !originalRequest.skipAuth) {
      if (isRefreshing) {
        // If already refreshing, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return axiosInstance(originalRequest);
        }).catch((err) => {
          return Promise.reject(err);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const tokens = getTokens();
        if (!tokens?.refresh) {
          // No refresh token, redirect to login
          handleAuthFailure();
          return Promise.reject(error);
        }

        // Attempt to refresh the token using the auth service
        const access = await refreshAccessToken();
        
        // Process queued requests
        processQueue(null, access);
        
        // Retry original request
        originalRequest.headers.Authorization = `Bearer ${access}`;
        return axiosInstance(originalRequest);
        
      } catch (refreshError) {
        // Refresh failed, clear tokens and redirect to login
        processQueue(refreshError, null);
        handleAuthFailure();
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

function buildUrl(pathOrUrl: string): string {
  if (!pathOrUrl) {
    console.error('buildUrl called with undefined path');
    return API_BASE;
  }
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
  const path = pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`;
  // If caller passed a path beginning with /api/, avoid duplicating /api
  if (path.startsWith('/api/')) {
    const root = API_BASE.replace(/\/api$/, '');
    return `${root}${path}`;
  }
  return `${API_BASE}${path}`;
}

export function buildApiUrl(pathOrUrl: string): string {
  return buildUrl(pathOrUrl);
}

/**
 * Triggered when a refresh fails inside the response interceptor — at
 * that point the user's session is unrecoverable and they need to log
 * in again.
 *
 * Delegates to the canonical `logout` in auth.ts so cleanup matches
 * every other logout path (token clear + cookie clear + sa_user clear +
 * integration-key clear + redirect). Before consolidation this function
 * only cleared tokens + sa_user, leaving Azure/Jira credentials behind
 * in localStorage between users on the same browser.
 *
 * Redux cleanup is NOT done here — apiRequest.ts has no access to a
 * dispatch function. The page reload triggered by the redirect inside
 * logout() will reset Redux state regardless, which is good enough for
 * a sign-out path.
 */
function handleAuthFailure(): void {
  logoutAndRedirect();
}

export async function apiRequest(
  method: Method,
  path: string,
  data?: any,
  withAuth: boolean = true,
  isMultipart: boolean = false,
  extraConfig: AxiosRequestConfig = {}
): Promise<AxiosResponse<any>> {
  const url = buildUrl(path);
  const headers: Record<string, string> = {
    Accept: 'application/json',
  };

  // Only set Content-Type when not sending FormData; letting the browser/axios set
  // the boundary automatically is safer for multipart requests
  if (!isMultipart && (method === 'post' || method === 'put' || method === 'patch' || method === 'delete')) {
    headers['Content-Type'] = 'application/json';
  }
  if (isMultipart) {
    headers['Content-Type'] = 'multipart/form-data';
  }

  const config: AxiosRequestConfig = {
    url,
    method,
    headers,
    skipAuth: !withAuth, // Skip auth if withAuth is false
    ...extraConfig,
  };

  if (method.toLowerCase() === 'get') {
    config.params = data;
  } else {
    config.data = data;
  }

  // Use the axios instance with interceptors
  return axiosInstance(config);
}

export default apiRequest;


