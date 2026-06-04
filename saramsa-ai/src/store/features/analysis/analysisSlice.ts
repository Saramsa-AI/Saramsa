/**
 * CLEAN STATE MANAGEMENT - Version 2.0
 *
 * Principles:
 * 1. Backend is the ONLY source of truth
 * 2. No localStorage caching of analysis data
 * 3. Force refetch on every mount
 * 4. Simple loading states
 * 5. No optimistic updates
 * 6. No race conditions
 */

import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';
import type { AnalysisData } from '@/types/analysis';
import type { RootState } from '@/store/store';

// ============================================================================
// TYPES
// ============================================================================

export interface AnalysisHistoryEntry {
  id: string;
  analysis_date: string;
  comments_count: number;
  positive_pct: number;
  status: string;
  display_number?: number;
  name?: string;
  task_id?: string;
  file_name?: string;
}

interface AnalysisState {
  // History list for sidebar
  analysisHistory: AnalysisHistoryEntry[];
  historyLoading: boolean;
  historyError: string | null;

  // Currently selected analysis
  selectedAnalysisId: string | null;
  selectedAnalysisData: AnalysisData | null;
  selectedAnalysisLoading: boolean;
  selectedAnalysisError: string | null;

  // Currently running analysis (upload → analyze flow)
  currentTaskId: string | null;
  currentTaskStatus: 'idle' | 'uploading' | 'analyzing' | 'completed' | 'failed';
  currentTaskError: string | null;

  // Project context
  projectId: string | null;

  // BACKWARD COMPATIBILITY - for components not yet migrated
  analysisData: AnalysisData | null;
  deepAnalysis: any | null;
  loadedComments: string[] | null;
}

const initialState: AnalysisState = {
  analysisHistory: [],
  historyLoading: false,
  historyError: null,

  selectedAnalysisId: null,
  selectedAnalysisData: null,
  selectedAnalysisLoading: false,
  selectedAnalysisError: null,

  currentTaskId: null,
  currentTaskStatus: 'idle',
  currentTaskError: null,

  projectId: null,

  // BACKWARD COMPATIBILITY
  analysisData: null,
  deepAnalysis: null,
  loadedComments: null,
};

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Normalize API response to expected frontend structure
 * The API can return data in two formats:
 * 1. New format: { id, analysisData: { overall, counts, features } }
 * 2. Old format: { overall, counts, features } (at top level)
 */
function normalizeAnalysisData(input: any): AnalysisData {
  if (!input) return input;

  // If already in new format with analysisData nested, use as-is
  if (input.analysisData) {
    return input as AnalysisData;
  }

  // If in old format, wrap it under analysisData
  if (input.overall || input.counts || input.features) {
    return {
      ...input,
      analysisData: {
        overall: input.overall || {},
        counts: input.counts || {},
        features: input.features || [],
        positive_keywords: input.positive_keywords || [],
        negative_keywords: input.negative_keywords || [],
      }
    } as AnalysisData;
  }

  // Otherwise return as-is
  return input as AnalysisData;
}

// ============================================================================
// ASYNC THUNKS
// ============================================================================

/**
 * Fetch analysis history for a project
 * ALWAYS fetches fresh data from backend - no caching
 */
export const fetchAnalysisHistory = createAsyncThunk<
  AnalysisHistoryEntry[],
  { projectId: string },
  { rejectValue: string }
>(
  'analysis/fetchHistory',
  async ({ projectId }, { rejectWithValue }) => {
    try {
      console.log(`[fetchHistory] Fetching for project: ${projectId}`);

      // Use the correct endpoint from the old API
      const response = await apiRequest('get', `/feedback/history/list/?project_id=${projectId}`, undefined, true);

      const analyses: any[] = response.data?.data?.analyses ?? [];
      console.log(`[fetchHistory] Received ${analyses.length} items`);

      // Map to AnalysisHistoryEntry format
      return analyses.map((a: any): AnalysisHistoryEntry => ({
        id: a.id,
        analysis_date: a.created_at ?? '',
        comments_count: a.comments_count ?? 0,
        positive_pct: a.positive_pct ?? 0,
        status: a.status ?? 'completed',
        display_number: a.display_number,
        name: a.name,
        task_id: a.task_id,
        file_name: a.file_name,
      }));
    } catch (error: any) {
      console.error('[fetchHistory] Error:', error);
      return rejectWithValue(error.message || 'Failed to fetch history');
    }
  }
);

/**
 * Fetch single analysis by ID
 */
export const fetchAnalysisById = createAsyncThunk<
  AnalysisData,
  { analysisId: string },
  { rejectValue: string }
>(
  'analysis/fetchById',
  async ({ analysisId }, { rejectWithValue }) => {
    try {
      console.log(`[fetchById] Fetching analysis: ${analysisId}`);

      // Use the correct endpoint from the old API
      const response = await apiRequest('get', `/feedback/analysis/${analysisId}/`, undefined, true);

      const data = response.data?.data ?? response.data;
      console.log(`[fetchById] Loaded analysis:`, data.id);

      return data as AnalysisData;
    } catch (error: any) {
      console.error('[fetchById] Error:', error);
      return rejectWithValue(error.message || 'Analysis not found');
    }
  }
);

/**
 * Delete analysis
 * After deletion, automatically refetches history to stay in sync
 */
export const deleteAnalysis = createAsyncThunk<
  { deletedId: string; projectId: string },
  { analysisId: string; projectId: string },
  { rejectValue: string }
>(
  'analysis/delete',
  async ({ analysisId, projectId }, { rejectWithValue, dispatch }) => {
    try {
      console.log(`[delete] Deleting analysis: ${analysisId}`);

      // Use the correct endpoint from the old API
      await apiRequest('delete', `/feedback/analysis/${encodeURIComponent(analysisId)}/`, undefined, true);

      console.log(`[delete] Deleted successfully`);

      // Refetch history to stay in sync with backend
      dispatch(fetchAnalysisHistory({ projectId }));

      return { deletedId: analysisId, projectId };
    } catch (error: any) {
      console.error('[delete] Error:', error);
      return rejectWithValue(error.message || 'Failed to delete analysis');
    }
  }
);

/**
 * Rename analysis
 */
export const renameAnalysis = createAsyncThunk<
  { analysisId: string; newName: string },
  { analysisId: string; newName: string },
  { rejectValue: string }
>(
  'analysis/rename',
  async ({ analysisId, newName }, { rejectWithValue }) => {
    try {
      console.log(`[rename] Renaming ${analysisId} to: ${newName}`);

      const response = await apiRequest('POST', `/analyses/${analysisId}/rename/`, {
        name: newName,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      console.log(`[rename] Renamed successfully`);

      return { analysisId, newName };
    } catch (error: any) {
      console.error('[rename] Error:', error);
      return rejectWithValue(error.message || 'Failed to rename analysis');
    }
  }
);

/**
 * Upload file and start analysis
 */
export const uploadAndAnalyze = createAsyncThunk<
  { taskId: string; analysisId: string },
  { file: File; projectId: string },
  { rejectValue: string }
>(
  'analysis/uploadAndAnalyze',
  async ({ file, projectId }, { rejectWithValue }) => {
    try {
      console.log(`[upload] Starting upload: ${file.name}`);

      // Step 1: Upload file
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_id', projectId);

      const uploadResponse = await apiRequest('POST', '/ingest/', formData, true);

      if (!uploadResponse.ok) {
        throw new Error(`Upload failed: ${uploadResponse.statusText}`);
      }

      const uploadData = await uploadResponse.json();
      console.log(`[upload] File uploaded, analysis ID: ${uploadData.analysis_id}`);

      // Step 2: Start analysis
      const analyzeResponse = await apiRequest('POST', '/analyze/', {
        project_id: projectId,
        analysis_id: uploadData.analysis_id,
        file_name: file.name,
      });

      if (!analyzeResponse.ok) {
        throw new Error(`Analysis failed: ${analyzeResponse.statusText}`);
      }

      const analyzeData = await analyzeResponse.json();
      console.log(`[upload] Analysis started, task ID: ${analyzeData.task_id}`);

      return {
        taskId: analyzeData.task_id,
        analysisId: uploadData.analysis_id,
      };
    } catch (error: any) {
      console.error('[upload] Error:', error);
      return rejectWithValue(error.message || 'Upload failed');
    }
  }
);

/**
 * Poll task status
 */
export const pollTaskStatus = createAsyncThunk<
  { status: string; state: string },
  { taskId: string },
  { rejectValue: string }
>(
  'analysis/pollStatus',
  async ({ taskId }, { rejectWithValue }) => {
    try {
      const response = await apiRequest('GET', `/tasks/${taskId}/status/`, {});

      if (!response.ok) {
        throw new Error(`Failed to poll status`);
      }

      const data = await response.json();
      return { status: data.status, state: data.state };
    } catch (error: any) {
      console.error('[poll] Error:', error);
      return rejectWithValue(error.message || 'Failed to poll status');
    }
  }
);

// ============================================================================
// SLICE
// ============================================================================

const analysisSlice = createSlice({
  name: 'analysis',
  initialState,
  reducers: {
    // Set project context
    setProjectId: (state, action: PayloadAction<string>) => {
      console.log(`[setProjectId] ${action.payload}`);
      state.projectId = action.payload;
    },

    // Set selected analysis ID
    setSelectedAnalysisId: (state, action: PayloadAction<string | null>) => {
      console.log(`[setSelectedAnalysisId] ${action.payload}`);
      state.selectedAnalysisId = action.payload;

      // Clear data when deselecting
      if (action.payload === null) {
        state.selectedAnalysisData = null;
        state.selectedAnalysisError = null;

        // BACKWARD COMPATIBILITY - also clear old fields
        state.analysisData = null;
        state.deepAnalysis = null;
        state.loadedComments = null;
      }
    },

    // Clear current task
    clearCurrentTask: (state) => {
      console.log(`[clearCurrentTask]`);
      state.currentTaskId = null;
      state.currentTaskStatus = 'idle';
      state.currentTaskError = null;
    },

    // Reset entire state
    resetAnalysisState: () => {
      console.log(`[reset] Resetting all state`);
      return initialState;
    },
  },

  extraReducers: (builder) => {
    // ========================================
    // fetchAnalysisHistory
    // ========================================
    builder.addCase(fetchAnalysisHistory.pending, (state) => {
      state.historyLoading = true;
      state.historyError = null;
    });

    builder.addCase(fetchAnalysisHistory.fulfilled, (state, action) => {
      state.historyLoading = false;
      state.analysisHistory = action.payload;
      console.log(`[fetchHistory.fulfilled] Loaded ${action.payload.length} items`);
    });

    builder.addCase(fetchAnalysisHistory.rejected, (state, action) => {
      state.historyLoading = false;
      state.historyError = action.payload || 'Failed to load history';
      console.error(`[fetchHistory.rejected]`, action.payload);
    });

    // ========================================
    // fetchAnalysisById
    // ========================================
    builder.addCase(fetchAnalysisById.pending, (state) => {
      state.selectedAnalysisLoading = true;
      state.selectedAnalysisError = null;
    });

    builder.addCase(fetchAnalysisById.fulfilled, (state, action) => {
      state.selectedAnalysisLoading = false;

      // Normalize the data to ensure correct structure
      const normalizedData = normalizeAnalysisData(action.payload);

      state.selectedAnalysisData = normalizedData;

      // BACKWARD COMPATIBILITY - populate old fields for components not yet migrated
      state.analysisData = normalizedData;
      state.deepAnalysis = normalizedData?.userStories ?? null;
      state.loadedComments = normalizedData?.comments ?? null;

      console.log(`[fetchById.fulfilled] Loaded`, normalizedData?.id);
    });

    builder.addCase(fetchAnalysisById.rejected, (state, action) => {
      state.selectedAnalysisLoading = false;
      state.selectedAnalysisError = action.payload || 'Failed to load analysis';
      console.error(`[fetchById.rejected]`, action.payload);
    });

    // ========================================
    // deleteAnalysis
    // ========================================
    builder.addCase(deleteAnalysis.fulfilled, (state, action) => {
      const { deletedId } = action.payload;

      // If we deleted the currently selected item, clear it
      if (state.selectedAnalysisId === deletedId) {
        state.selectedAnalysisId = null;
        state.selectedAnalysisData = null;
      }

      console.log(`[delete.fulfilled] Deleted ${deletedId}`);
    });

    // ========================================
    // renameAnalysis
    // ========================================
    builder.addCase(renameAnalysis.fulfilled, (state, action) => {
      const { analysisId, newName } = action.payload;

      // Update in history
      const entry = state.analysisHistory.find(e => e.id === analysisId);
      if (entry) {
        entry.name = newName;
      }

      console.log(`[rename.fulfilled] Renamed ${analysisId}`);
    });

    // ========================================
    // uploadAndAnalyze
    // ========================================
    builder.addCase(uploadAndAnalyze.pending, (state) => {
      state.currentTaskStatus = 'uploading';
      state.currentTaskError = null;
    });

    builder.addCase(uploadAndAnalyze.fulfilled, (state, action) => {
      state.currentTaskId = action.payload.taskId;
      state.currentTaskStatus = 'analyzing';
      console.log(`[upload.fulfilled] Task ${action.payload.taskId} started`);
    });

    builder.addCase(uploadAndAnalyze.rejected, (state, action) => {
      state.currentTaskStatus = 'failed';
      state.currentTaskError = action.payload || 'Upload failed';
      console.error(`[upload.rejected]`, action.payload);
    });

    // ========================================
    // pollTaskStatus
    // ========================================
    builder.addCase(pollTaskStatus.fulfilled, (state, action) => {
      const { status, state: taskState } = action.payload;

      if (status === 'COMPLETED' || taskState === 'completed') {
        state.currentTaskStatus = 'completed';
      } else if (status === 'FAILURE' || status === 'FAILED') {
        state.currentTaskStatus = 'failed';
      }

      console.log(`[poll.fulfilled] Status: ${status}, State: ${taskState}`);
    });
  },
});

// ============================================================================
// EXPORTS
// ============================================================================

export const {
  setProjectId,
  setSelectedAnalysisId,
  clearCurrentTask,
  resetAnalysisState,
} = analysisSlice.actions;

export default analysisSlice.reducer;

// ============================================================================
// SELECTORS
// ============================================================================

export const selectAnalysisHistory = (state: RootState) => state.analysis.analysisHistory;
export const selectHistoryLoading = (state: RootState) => state.analysis.historyLoading;
export const selectSelectedAnalysisId = (state: RootState) => state.analysis.selectedAnalysisId;
export const selectSelectedAnalysisData = (state: RootState) => state.analysis.selectedAnalysisData;
export const selectSelectedAnalysisLoading = (state: RootState) => state.analysis.selectedAnalysisLoading;
export const selectCurrentTaskStatus = (state: RootState) => state.analysis.currentTaskStatus;

// ============================================================================
// BACKWARD COMPATIBILITY SHIMS
// These exist to prevent build errors during migration
// ============================================================================

// Dummy actions for components that haven't been migrated yet
export const clearAnalysisData = () => ({ type: 'analysis/clearAnalysisData' });
export const resolveAnalyzingTask = () => ({ type: 'analysis/resolveAnalyzingTask' });
export const prependToHistory = () => ({ type: 'analysis/prependToHistory' });
export const setLoadedComments = () => ({ type: 'analysis/setLoadedComments' });
export const setAnalysisData = () => ({ type: 'analysis/setAnalysisData' });
export const setDeepAnalysis = () => ({ type: 'analysis/setDeepAnalysis' });
export const removeFromHistory = () => ({ type: 'analysis/removeFromHistory' });
export const clearError = () => ({ type: 'analysis/clearError' });
export const setTaskIdForEntry = () => ({ type: 'analysis/setTaskIdForEntry' });
export const replaceInHistory = () => ({ type: 'analysis/replaceInHistory' });

// Dummy thunks
export const resumeInFlightTask = createAsyncThunk('analysis/resumeInFlightTask', async () => {
  console.warn('[DEPRECATED] resumeInFlightTask called - needs migration');
  return null;
});

export const getConsolidatedDashboardData = createAsyncThunk('analysis/getConsolidatedDashboardData', async () => {
  console.warn('[DEPRECATED] getConsolidatedDashboardData called - needs migration');
  return null;
});

export const getLatestAnalysis = createAsyncThunk('analysis/getLatestAnalysis', async () => {
  console.warn('[DEPRECATED] getLatestAnalysis called - needs migration');
  return null;
});

export const ingestFile = createAsyncThunk('analysis/ingestFile', async () => {
  console.warn('[DEPRECATED] ingestFile called - use uploadAndAnalyze instead');
  return null;
});

export const analyzeComments = createAsyncThunk('analysis/analyzeComments', async () => {
  console.warn('[DEPRECATED] analyzeComments called - use uploadAndAnalyze instead');
  return null;
});

export const generateUserStories = createAsyncThunk('analysis/generateUserStories', async () => {
  console.warn('[DEPRECATED] generateUserStories called - needs migration');
  return null;
});

// Dummy selectors that return safe defaults
export const selectTaskState = () => null;
export const selectAnalysisDisplayStatus = () => 'Loading...';
export const selectAnalysisLifecycleState = () => 'idle';
export const selectIsAnyAnalysisRunning = () => false;
export const selectIsProjectAnalyzing = () => false;
export const selectIsViewingActiveAnalysis = () => false;
export const selectIsGeneratingWorkItems = () => false;
