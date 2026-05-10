import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';
import type { User } from '@/lib/auth';
import * as authApi from '@/lib/auth';

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null;
}

const initialState: AuthState = {
  user: null,
  isAuthenticated: false,
  loading: false,
  error: null,
};

// Async thunk for login
export const loginUser = createAsyncThunk<
  User,
  { email: string; password: string },
  { rejectValue: string }
>('auth/loginUser', async (credentials, { rejectWithValue }) => {
  try {
    const { user } = await authApi.login(credentials);
    authApi.setStoredUser(user);
    return user;
  } catch (err: any) {
    let errorMessage = 'Login failed. Please try again.';
    if (err.code === 'ECONNREFUSED' || err.code === 'ERR_NETWORK') {
      errorMessage = 'Unable to connect to the server. Please check if the backend is running.';
    } else if (err.response?.status === 401) {
      errorMessage = 'Invalid email or password. Please check your credentials.';
    } else if (err.response?.status === 400) {
      errorMessage = err.response?.data?.detail ||
        err.response?.data?.non_field_errors?.[0] ||
        'Invalid login data. Please check your input.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for register (invite-only)
export const registerUser = createAsyncThunk<
  User,
  {
    email: string;
    password: string;
    confirmPassword: string;
    invite_token: string;
    first_name?: string;
    last_name?: string;
    role?: 'admin' | 'user' | 'restricted user';
  },
  { rejectValue: string }
>('auth/registerUser', async (data, { rejectWithValue }) => {
  try {
    const { user } = await authApi.register({
      email: data.email,
      password: data.password,
      confirmPassword: data.confirmPassword,
      invite_token: data.invite_token,
      first_name: data.first_name,
      last_name: data.last_name,
      // Default role to admin if omitted
      role: (data.role || 'admin') as any,
    });
    authApi.setStoredUser(user);
    return user;
  } catch (err: any) {
    let errorMessage = 'Registration failed. Please try again.';
    if (err.code === 'ECONNREFUSED' || err.code === 'ERR_NETWORK') {
      errorMessage = 'Unable to connect to the server. Please check if the backend is running.';
    } else if (err.response?.status === 400) {
      const errors = err.response?.data;
      if (errors?.email) {
        errorMessage = `Email: ${errors.email[0]}`;
      } else if (errors?.password) {
        errorMessage = `Password: ${errors.password[0]}`;
      } else if (errors?.confirmPassword) {
        errorMessage = `Confirm Password: ${errors.confirmPassword[0]}`;
      } else if (errors?.detail) {
        errorMessage = errors.detail;
      } else {
        errorMessage = 'Please check your input and try again.';
      }
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    logout: (state) => {
      state.user = null;
      state.isAuthenticated = false;
      state.error = null;
    },
    updateUser: (state, action: PayloadAction<Partial<User>>) => {
      if (state.user) {
        state.user = { ...state.user, ...action.payload };
        authApi.setStoredUser(state.user);
      }
    },
    setUser: (state, action: PayloadAction<User>) => {
      state.user = action.payload;
      state.isAuthenticated = true;
      state.error = null;
      authApi.setStoredUser(action.payload);
    },
    clearError: (state) => {
      state.error = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loginUser.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(loginUser.fulfilled, (state, action) => {
        state.loading = false;
        state.user = action.payload;
        state.isAuthenticated = true;
        state.error = null;
      })
      .addCase(loginUser.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Login failed.';
        state.isAuthenticated = false;
      })
      .addCase(registerUser.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(registerUser.fulfilled, (state, action) => {
        state.loading = false;
        state.user = action.payload;
        state.isAuthenticated = true;
        state.error = null;
      })
      .addCase(registerUser.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Registration failed.';
        state.isAuthenticated = false;
      });
  },
});

export const {
  logout,
  updateUser,
  setUser,
  clearError,
} = authSlice.actions;

export default authSlice.reducer; 
