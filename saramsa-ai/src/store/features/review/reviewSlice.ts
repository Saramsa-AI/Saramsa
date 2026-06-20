import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';

export interface ReviewCandidate {
  id: string;
  title: string;
  description: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  type: 'bug' | 'feature' | 'task';
  feature_area?: string;
  acceptance_criteria?: string;
  tags?: string[];
  status: 'pending' | 'approved' | 'dismissed' | 'snoozed' | 'merged';
  status_changed_at?: string;
  push_status?: 'not_pushed' | 'pushed' | 'failed';
  external_id?: string;
  external_url?: string;
  comment_count?: number;
  sentiment_percent?: number;
  source_icon?: string;
  createdAt: string;
  evidence?: Array<{ text: string; source: string }>;
}

export interface ReviewStats {
  pending: number;
  approved_this_week: number;
  dismissed_this_week: number;
  snoozed: number;
}

interface ReviewFilters {
  status: string;
  priority: string;
  feature_area: string;
  date_from: string;
  date_to: string;
}

interface ReviewState {
  candidates: ReviewCandidate[];
  stats: ReviewStats | null;
  filters: ReviewFilters;
  selectedIds: string[];
  loading: boolean;
  statsLoading: boolean;
  error: string | null;
}

const initialState: ReviewState = {
  candidates: [],
  stats: null,
  filters: {
    status: 'pending',
    priority: '',
    feature_area: '',
    date_from: '',
    date_to: '',
  },
  selectedIds: [],
  loading: false,
  statsLoading: false,
  error: null,
};

export const fetchCandidates = createAsyncThunk<
  ReviewCandidate[],
  { projectId: string; filters?: Partial<ReviewFilters> }
>('review/fetchCandidates', async ({ projectId, filters }) => {
  const params = new URLSearchParams({ project_id: projectId });
  if (filters?.status) params.set('status', filters.status);
  if (filters?.priority) params.set('priority', filters.priority);
  if (filters?.feature_area) params.set('feature_area', filters.feature_area);
  if (filters?.date_from) params.set('date_from', filters.date_from);
  if (filters?.date_to) params.set('date_to', filters.date_to);

  const res = await apiRequest("get", `/work-items/review/?${params.toString()}`, undefined, true);
  return res.data?.data?.candidates || [];
});

export const fetchReviewStats = createAsyncThunk<ReviewStats, string>(
  'review/fetchStats',
  async (projectId) => {
    const res = await apiRequest("get", `/work-items/review/stats/?project_id=${projectId}`, undefined, true);
    return res.data?.data;
  }
);

export const approveCandidate = createAsyncThunk<
  any,
  { candidateId: string; projectId: string; edits?: Record<string, any> }
>('review/approve', async ({ candidateId, projectId, edits }) => {
  const res = await apiRequest('post', '/work-items/review/approve/', { candidate_id: candidateId, project_id: projectId, edits }, true);
  return { candidateId, data: res.data };
});

export const dismissCandidate = createAsyncThunk<
  any,
  { candidateId: string; projectId: string; reason: string }
>('review/dismiss', async ({ candidateId, projectId, reason }) => {
  const res = await apiRequest('post', '/work-items/review/dismiss/', { candidate_id: candidateId, project_id: projectId, reason }, true);
  return { candidateId, data: res.data };
});

export const snoozeCandidate = createAsyncThunk<
  any,
  { candidateId: string; projectId: string; snoozeDays: number }
>('review/snooze', async ({ candidateId, projectId, snoozeDays }) => {
  const res = await apiRequest('post', '/work-items/review/snooze/', { candidate_id: candidateId, project_id: projectId, snooze_days: snoozeDays }, true);
  return { candidateId, data: res.data };
});

export const batchApprove = createAsyncThunk<
  any,
  { candidateIds: string[]; projectId: string }
>('review/batchApprove', async ({ candidateIds, projectId }) => {
  const res = await apiRequest('post', '/work-items/review/batch-approve/', { candidate_ids: candidateIds, project_id: projectId }, true);
  return { candidateIds, data: res.data };
});

const reviewSlice = createSlice({
  name: 'review',
  initialState,
  reducers: {
    setFilters(state, action: PayloadAction<Partial<ReviewFilters>>) {
      state.filters = { ...state.filters, ...action.payload };
    },
    toggleSelected(state, action: PayloadAction<string>) {
      const id = action.payload;
      if (state.selectedIds.includes(id)) {
        state.selectedIds = state.selectedIds.filter((i) => i !== id);
      } else {
        state.selectedIds.push(id);
      }
    },
    clearSelected(state) {
      state.selectedIds = [];
    },
    selectAll(state) {
      state.selectedIds = state.candidates.map((c) => c.id);
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchCandidates.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(fetchCandidates.fulfilled, (state, action) => {
        state.loading = false;
        state.candidates = action.payload;
      })
      .addCase(fetchCandidates.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to fetch candidates';
      })
      .addCase(fetchReviewStats.pending, (state) => {
        state.statsLoading = true;
        state.error = null;
      })
      .addCase(fetchReviewStats.fulfilled, (state, action) => {
        state.statsLoading = false;
        state.stats = action.payload;
      })
      .addCase(fetchReviewStats.rejected, (state, action) => {
        state.statsLoading = false;
        state.error = action.error.message || 'Failed to load review stats';
      })
      .addCase(approveCandidate.fulfilled, (state, action) => {
        state.candidates = state.candidates.filter((c) => c.id !== action.payload.candidateId);
        state.selectedIds = state.selectedIds.filter((id) => id !== action.payload.candidateId);
      })
      .addCase(approveCandidate.rejected, (state, action) => {
        state.error = action.error.message || 'Failed to approve candidate';
      })
      .addCase(dismissCandidate.fulfilled, (state, action) => {
        state.candidates = state.candidates.filter((c) => c.id !== action.payload.candidateId);
        state.selectedIds = state.selectedIds.filter((id) => id !== action.payload.candidateId);
      })
      .addCase(dismissCandidate.rejected, (state, action) => {
        state.error = action.error.message || 'Failed to dismiss candidate';
      })
      .addCase(snoozeCandidate.fulfilled, (state, action) => {
        state.candidates = state.candidates.filter((c) => c.id !== action.payload.candidateId);
        state.selectedIds = state.selectedIds.filter((id) => id !== action.payload.candidateId);
      })
      .addCase(snoozeCandidate.rejected, (state, action) => {
        state.error = action.error.message || 'Failed to snooze candidate';
      })
      .addCase(batchApprove.fulfilled, (state, action) => {
        const ids = new Set(action.payload.candidateIds);
        state.candidates = state.candidates.filter((c) => !ids.has(c.id));
        state.selectedIds = [];
      })
      .addCase(batchApprove.rejected, (state, action) => {
        state.error = action.error.message || 'Failed to approve selected candidates';
      });
  },
});

export const { setFilters, toggleSelected, clearSelected, selectAll } = reviewSlice.actions;
export default reviewSlice.reducer;
