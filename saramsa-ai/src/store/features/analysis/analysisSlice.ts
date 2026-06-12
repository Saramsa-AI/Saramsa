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
 * Poll task status until completion and fetch the analysis result
 * This is the critical missing piece - without it, uploads just hang!
 */
async function waitForAnalysisTask(taskId: string, dispatch: any): Promise<any> {
  return new Promise((resolve, reject) => {
    let pollCount = 0;
    const maxPolls = 900; // 30 minutes max (2s interval)

    const pollInterval = setInterval(async () => {
      try {
        pollCount++;
        console.log(`[waitForAnalysisTask] Poll #${pollCount} for task ${taskId}`);

        const response = await apiRequest('get', `/insights/task-status/${taskId}/`, undefined, true);
        const statusData = response.data.data;
        const status = statusData.status;

        console.log(`[waitForAnalysisTask] Status: ${status}`);

        // PARTIAL is terminal too — a partial run produced results (some comments
        // failed). Without this branch it would poll until the 30-min timeout.
        if (status === 'COMPLETED' || status === 'SUCCESS' || status === 'PARTIAL') {
          clearInterval(pollInterval);
          const isPartial = status === 'PARTIAL';

          // Fetch the full analysis data
          const insightId = statusData.result?.insight_id;
          if (insightId) {
            const analysisRes = await apiRequest('get', `/feedback/analysis/${insightId}/`, undefined, true);
            const analysisData = analysisRes.data?.data;

            console.log(`[waitForAnalysisTask] Analysis loaded:`, insightId, isPartial ? '(partial)' : '');
            resolve({
              id: insightId,
              analysisData: analysisData,
              taskId: taskId,
              partial: isPartial,
            });
          } else {
            resolve({
              id: `analysis_${Date.now()}`,
              analysisData: statusData.result,
              taskId: taskId,
              partial: isPartial,
            });
          }
        } else if (status === 'FAILURE' || status === 'FAILED') {
          clearInterval(pollInterval);
          reject(new Error(statusData.error || 'Analysis failed'));
        } else if (pollCount >= maxPolls) {
          clearInterval(pollInterval);
          reject(new Error('Analysis timeout'));
        }
      } catch (error) {
        clearInterval(pollInterval);
        reject(error);
      }
    }, 2000); // Poll every 2 seconds
  });
}

/**
 * Normalize API response to expected frontend structure
 * The API can return data in many formats - handle them all
 */
function normalizeAnalysisData(input: any): AnalysisData {
  if (!input) return input;

  console.log('[normalizeAnalysisData] Input keys:', Object.keys(input));
  console.log('[normalizeAnalysisData] Full input:', input);

  // Extract the core analysis data from various possible structures
  let analysisData = input.analysisData || input.analysis?.analysisData || input;

  // If the data has overall/counts/features at top level, it needs wrapping
  if (analysisData.overall || analysisData.counts || analysisData.features) {
    // Already has the right inner structure, just make sure it's nested
    if (!input.analysisData) {
      analysisData = {
        overall: analysisData.overall || {},
        counts: analysisData.counts || {},
        features: analysisData.features || [],
        positive_keywords: analysisData.positive_keywords || [],
        negative_keywords: analysisData.negative_keywords || [],
      };
    }
  }

  // Build the normalized response
  const normalized = {
    id: input.id || input.analysis_id || input.analysisId,
    projectId: input.projectId || input.project_id,
    userId: input.userId || input.user_id,
    createdAt: input.createdAt || input.created_at,
    analysisType: input.analysisType || input.analysis_type || 'commentSentiment',
    analysisData: analysisData,
    userStories: input.userStories || input.user_stories,
    work_items: input.work_items || input.pipeline_work_items,  // CRITICAL: Extract work items!
    comments: input.comments,
    rawLlm: input.rawLlm || input.raw_llm,
  } as AnalysisData;

  console.log('[normalizeAnalysisData] Normalized ID:', normalized.id);
  console.log('[normalizeAnalysisData] Work items count:', (normalized as any).work_items?.length || 0);

  return normalized;
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

      // Map the backend's durable status to the sidebar's vocabulary.
      const statusMap: Record<string, string> = {
        partially_completed: 'partial',
        in_progress: 'analyzing',
        started: 'analyzing',
        successful: 'completed',
      };

      // Map to AnalysisHistoryEntry format
      return analyses.map((a: any): AnalysisHistoryEntry => ({
        id: a.id,
        analysis_date: a.created_at ?? '',
        comments_count: a.comments_count ?? 0,
        positive_pct: a.positive_pct ?? 0,
        status: statusMap[a.status] ?? a.status ?? 'completed',
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
      console.log(`[fetchById] RAW response.data:`, response.data);
      console.log(`[fetchById] Extracted data:`, data);

      // CRITICAL: API returns {exists, analysis} - extract the analysis object!
      const analysisData = data.analysis ?? data;
      console.log(`[fetchById] Analysis object:`, analysisData);
      console.log(`[fetchById] Loaded analysis ID:`, analysisData.id);

      return analysisData as AnalysisData;
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

      if (response.status < 200 || response.status >= 300) {
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

      if (uploadResponse.status < 200 || uploadResponse.status >= 300) {
        throw new Error(`Upload failed: ${uploadResponse.statusText}`);
      }

      const uploadData = uploadResponse.data;
      console.log(`[upload] File uploaded, analysis ID: ${uploadData.analysis_id}`);

      // Step 2: Start analysis
      const analyzeResponse = await apiRequest('POST', '/analyze/', {
        project_id: projectId,
        analysis_id: uploadData.analysis_id,
        file_name: file.name,
      });

      if (analyzeResponse.status < 200 || analyzeResponse.status >= 300) {
        throw new Error(`Analysis failed: ${analyzeResponse.statusText}`);
      }

      const analyzeData = analyzeResponse.data;
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

      if (response.status < 200 || response.status >= 300) {
        throw new Error(`Failed to poll status`);
      }

      const data = response.data;
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

    // Add placeholder to history (for "Analyzing..." state)
    prependToHistoryAction: (state, action: PayloadAction<AnalysisHistoryEntry>) => {
      console.log(`[prependToHistory] Adding placeholder:`, action.payload.id);
      state.analysisHistory.unshift(action.payload);
    },

    // Remove from history (cleanup placeholders)
    removeFromHistoryAction: (state, action: PayloadAction<string>) => {
      console.log(`[removeFromHistory] Removing:`, action.payload);
      state.analysisHistory = state.analysisHistory.filter(e => e.id !== action.payload);
    },

    // Reset entire state
    resetAnalysisState: () => {
      console.log(`[reset] Resetting all state`);
      return initialState;
    },

    // BACKWARD COMPATIBILITY: Dashboard calls setAnalysisData after fetchAnalysisById
    setAnalysisDataAction: (state, action: PayloadAction<any>) => {
      console.log(`[setAnalysisData] Setting analysis data:`, action.payload?.id);

      // Dashboard already normalizes data before calling this
      // Don't normalize again - just store it
      const data = action.payload;

      // Set both new and old fields for compatibility
      state.selectedAnalysisData = data;
      state.analysisData = data;
      // CRITICAL: WorkItemsPanel expects { work_items: [...] }, not just the array!
      if (data?.work_items) {
        console.log(`[setAnalysisData] ✅ Setting deepAnalysis from work_items (${data.work_items.length} items)`);
        state.deepAnalysis = { work_items: data.work_items };
      } else {
        console.log(`[setAnalysisData] ⚠️  No work_items, using userStories:`, data?.userStories);
        state.deepAnalysis = data?.userStories ?? null;
      }
      console.log(`[setAnalysisData] deepAnalysis set to:`, state.deepAnalysis);
      state.loadedComments = data?.comments ?? null;
    },

    // BACKWARD COMPATIBILITY: Dashboard calls setDeepAnalysis for work items
    setDeepAnalysisAction: (state, action: PayloadAction<any>) => {
      console.log(`[setDeepAnalysis] Setting deep analysis`);
      state.deepAnalysis = action.payload;
    },

    // BACKWARD COMPATIBILITY: Dashboard calls setLoadedComments
    setLoadedCommentsAction: (state, action: PayloadAction<string[] | null>) => {
      console.log(`[setLoadedComments] Setting ${action.payload?.length || 0} comments`);
      state.loadedComments = action.payload;
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
      state.deepAnalysis = (normalizedData as any)?.userStories ?? null;
      state.loadedComments = (normalizedData as any)?.comments ?? null;

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

        // BACKWARD COMPATIBILITY - also clear old fields
        state.analysisData = null;
        state.deepAnalysis = null;
        state.loadedComments = null;
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

    // ========================================
    // ingestFile (upload + analyze flow)
    // ========================================
    builder.addCase(ingestFile.pending, (state) => {
      state.currentTaskStatus = 'uploading';
      state.currentTaskError = null;
      console.log(`[ingestFile.pending] Starting upload...`);
    });

    builder.addCase(ingestFile.fulfilled, (state, action) => {
      state.currentTaskStatus = 'completed';

      // The result should have the analysis data
      const result = action.payload;
      console.log(`[ingestFile.fulfilled] Upload complete:`, result?.id);

      // If we got an analysis ID, it will appear in the next history fetch
      // For now, just mark as completed
    });

    builder.addCase(ingestFile.rejected, (state, action) => {
      state.currentTaskStatus = 'failed';
      state.currentTaskError = action.payload || 'Upload failed';
      console.error(`[ingestFile.rejected]`, action.payload);
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
  prependToHistoryAction,
  removeFromHistoryAction,
  resetAnalysisState,
  setAnalysisDataAction,
  setDeepAnalysisAction,
  setLoadedCommentsAction,
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

// Backward compatibility - map old action names to new ones
export const prependToHistory = prependToHistoryAction;
export const removeFromHistory = removeFromHistoryAction;
export const setAnalysisData = setAnalysisDataAction;
export const setDeepAnalysis = setDeepAnalysisAction;
export const setLoadedComments = setLoadedCommentsAction;

// Dummy actions for components that haven't been migrated yet (no-ops)
export const clearAnalysisData = (_params?: any) => ({ type: 'analysis/clearAnalysisData' });
export const resolveAnalyzingTask = (_params?: any) => ({ type: 'analysis/resolveAnalyzingTask' });
export const clearError = (_params?: any) => ({ type: 'analysis/clearError' });
export const setTaskIdForEntry = (_params?: any) => ({ type: 'analysis/setTaskIdForEntry' });
export const replaceInHistory = (_params?: any) => ({ type: 'analysis/replaceInHistory' });

// Dummy thunks
export const resumeInFlightTask = createAsyncThunk('analysis/resumeInFlightTask', async (_params?: any) => {
  console.warn('[DEPRECATED] resumeInFlightTask called - needs migration');
  return null;
});

export const getConsolidatedDashboardData = createAsyncThunk('analysis/getConsolidatedDashboardData', async (_projectId?: string) => {
  console.warn('[DEPRECATED] getConsolidatedDashboardData called - needs migration');
  return null;
});

export const getLatestAnalysis = createAsyncThunk('analysis/getLatestAnalysis', async (_projectId?: string) => {
  console.warn('[DEPRECATED] getLatestAnalysis called - needs migration');
  return null;
});

export const ingestFile = createAsyncThunk<
  any,
  { file: File; projectId?: string },
  { rejectValue: string }
>('analysis/ingestFile', async ({ file, projectId }, { dispatch, rejectWithValue, getState }) => {
  try {
    console.log('[ingestFile] Starting upload:', file.name);

    const form = new FormData();
    form.append('file', file);
    if (projectId) {
      form.append('project_id', projectId);
    }

    const response = await apiRequest('post', '/insights/ingest/', form, true, true);
    const data = response.data.data;
    const taskId = data?.task_id;

    console.log('[ingestFile] Upload complete, task ID:', taskId);

    if (!taskId) {
      throw new Error('No task ID received from server');
    }

    // Store comments if available
    if (Array.isArray(data?.comments) && data.comments.length > 0) {
      dispatch(setLoadedComments(data.comments));
    }

    // Poll until analysis completes
    console.log('[ingestFile] Starting to poll for completion...');
    const result = await waitForAnalysisTask(taskId, dispatch);

    // Refresh history after completion so new analysis appears in sidebar
    const state: any = getState();
    const stateProjectId = state?.analysis?.projectId || projectId;
    if (stateProjectId) {
      console.log('[ingestFile] Refreshing history after completion');
      await dispatch(fetchAnalysisHistory({ projectId: stateProjectId })).unwrap();
      console.log('[ingestFile] History refreshed successfully');

      // Auto-select the new analysis so center panel displays it
      if (result?.id) {
        console.log('[ingestFile] Auto-selecting new analysis:', result.id);
        dispatch(setSelectedAnalysisId(result.id));
      }
    }

    return result;
  } catch (err: any) {
    console.error('[ingestFile] Error:', err);
    let errorMessage = 'File ingestion failed. Please try again.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 400) {
      errorMessage = err.response?.data?.detail || 'Invalid file.';
    } else if (err.response?.status === 503) {
      errorMessage = err.response?.data?.detail || 'Analysis service unavailable.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

/**
 * Re-run a failed or partially-completed analysis from its durable record.
 * Posts the retrigger endpoint, then polls the new task to completion and
 * refreshes the history so the updated run appears.
 */
export const retriggerAnalysis = createAsyncThunk<
  any,
  { analysisId: string; projectId?: string },
  { rejectValue: string }
>('analysis/retrigger', async ({ analysisId, projectId }, { dispatch, rejectWithValue, getState }) => {
  try {
    const response = await apiRequest('post', `/insights/analyses/${analysisId}/retrigger/`, undefined, true);
    const taskId = response.data?.data?.task_id;
    if (!taskId) {
      throw new Error('No task ID received from retrigger');
    }

    const result = await waitForAnalysisTask(taskId, dispatch);

    const state: any = getState();
    const stateProjectId = state?.analysis?.projectId || projectId;
    if (stateProjectId) {
      await dispatch(fetchAnalysisHistory({ projectId: stateProjectId })).unwrap();
      if (result?.id) {
        dispatch(setSelectedAnalysisId(result.id));
      }
    }
    return result;
  } catch (err: any) {
    let errorMessage = 'Re-run failed. Please try again.';
    if (err.response?.status === 429) {
      errorMessage = err.response?.data?.detail || 'Quota exceeded.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Analysis not found.';
    } else if (err.response?.status === 503) {
      errorMessage = err.response?.data?.detail || 'Analysis service unavailable.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

export const analyzeComments = createAsyncThunk('analysis/analyzeComments', async (_params?: any) => {
  console.warn('[DEPRECATED] analyzeComments called - use uploadAndAnalyze instead');
  return null;
});

export const generateUserStories = createAsyncThunk('analysis/generateUserStories', async (_params?: any) => {
  console.warn('[DEPRECATED] generateUserStories called - needs migration');
  return null;
});

export const submitUserStories = createAsyncThunk('analysis/submitUserStories', async (_params?: any) => {
  console.warn('[DEPRECATED] submitUserStories called - needs migration');
  return null;
});

export const cancelAnalysisTask = createAsyncThunk('analysis/cancelAnalysisTask', async (_params?: any) => {
  console.warn('[DEPRECATED] cancelAnalysisTask called - needs migration');
  return null;
});

// Backward compatibility wrappers
// Old API: deleteAnalysisRun(id: string)
// New API: deleteAnalysis({ analysisId, projectId })
export const deleteAnalysisRun = createAsyncThunk<
  { deletedId: string; projectId: string },
  string,
  { rejectValue: string; state: RootState }
>(
  'analysis/deleteAnalysisRun',
  async (analysisId, { rejectWithValue, dispatch, getState }) => {
    try {
      const state = getState();
      const projectId = state.analysis.projectId;

      console.log('[deleteAnalysisRun] State:', {
        projectId,
        analysisId,
        fullState: state.analysis
      });

      if (!projectId) {
        throw new Error('No project ID available');
      }

      // Call the new delete function
      const result = await dispatch(deleteAnalysis({ analysisId, projectId })).unwrap();
      return result;
    } catch (error: any) {
      return rejectWithValue(error.message || 'Failed to delete analysis');
    }
  }
);

// Alias rename function (it has the same signature so simple alias works)
export const renameAnalysisRun = renameAnalysis;

// Real selectors for UI state
export const selectIsProjectAnalyzing = (state: { analysis: AnalysisState }, _projectId?: string | null) => {
  // Check if currently uploading or analyzing
  const taskStatus = state.analysis.currentTaskStatus;
  if (taskStatus === 'uploading' || taskStatus === 'analyzing') {
    return true;
  }

  // Check if there are any "analyzing_" placeholders in history
  const hasAnalyzingPlaceholder = state.analysis.analysisHistory.some(
    entry => entry.id.startsWith('analyzing_') || entry.status === 'analyzing'
  );

  return hasAnalyzingPlaceholder;
};

export const selectIsViewingActiveAnalysis = (state: { analysis: AnalysisState }) => {
  const selectedId = state.analysis.selectedAnalysisId;
  if (!selectedId) return false;

  // Check if selected item is an analyzing placeholder
  return selectedId.startsWith('analyzing_');
};

export const selectAnalysisDisplayStatus = (state: { analysis: AnalysisState }, analysisId: string | null) => {
  if (!analysisId) return '';

  const taskStatus = state.analysis.currentTaskStatus;

  if (taskStatus === 'uploading') return 'Uploading file...';
  if (taskStatus === 'analyzing') return 'Analyzing feedback...';

  return 'Processing...';
};

// Dummy selectors that aren't critical - match expected signatures
export const selectTaskState = (_state: { analysis: AnalysisState }, _analysisId: string | null) => null;
export const selectAnalysisLifecycleState = (_state: { analysis: AnalysisState }, _analysisId: string | null) => 'idle';
export const selectIsAnyAnalysisRunning = (_state: { analysis: AnalysisState }) => false;
export const selectIsGeneratingWorkItems = (_state?: { analysis: AnalysisState }) => false;
