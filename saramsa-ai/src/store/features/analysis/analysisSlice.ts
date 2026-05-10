import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';
import type { AnalysisData } from '@/types/analysis';

interface ProjectContext {
  project_id: string;
  project_status?: string;
  config_state?: string;
  is_draft?: boolean;
}

export interface AnalysisHistoryEntry {
  id: string;
  analysis_date: string;
  comments_count: number;
  positive_pct: number;
  status: string;
  name?: string;
}

interface AnalysisState {
  analysisData: AnalysisData | null;
  deepAnalysis: any | null;
  loading: boolean;
  error: string | null;
  isAnalyzing: boolean;
  analyzingByProject: Record<string, boolean>;
  loadedComments: string[] | null;
  latestAnalysis: any | null;
  latestLoading: boolean;
  latestError: string | null;
  projectContext: ProjectContext | null;
  // Async task tracking for Celery
  taskId: string | null;
  analysisStatus: 'idle' | 'pending' | 'processing' | 'success' | 'failure';
  pollingInterval: number | null;
  // Analysis history
  analysisHistory: AnalysisHistoryEntry[];
  historyLoading: boolean;
  historyError: string | null;
  selectedAnalysisId: string | null;
  fetchingAnalysisById: boolean;
}

const MAX_HISTORY_RUNS = 15;

const initialState: AnalysisState = {
  analysisData: null,
  deepAnalysis: null,
  loading: false,
  error: null,
  isAnalyzing: false,
  analyzingByProject: {},
  loadedComments: null,
  latestAnalysis: null,
  latestLoading: false,
  latestError: null,
  projectContext: null,
  // Async task tracking
  taskId: null,
  analysisStatus: 'idle',
  pollingInterval: null,
  // Analysis history
  analysisHistory: [],
  historyLoading: false,
  historyError: null,
  selectedAnalysisId: null,
  fetchingAnalysisById: false,
};


// Async thunk for polling task status
export const pollTaskStatus = createAsyncThunk<
  any,
  string,
  { rejectValue: string }
>('analysis/pollTaskStatus', async (taskId, { rejectWithValue }) => {
  try {
    const response = await apiRequest('get', `/insights/task-status/${taskId}/`, undefined, true);
    return response.data.data; // Returns { status, result, error }
  } catch (err: any) {
    let errorMessage = 'Failed to check task status.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Task not found.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

async function waitForAnalysisTask(taskId: string, dispatch: any): Promise<{ id: string; analysisData: any }> {
  const resolveAnalysis = async (statusResult: any) => {
    const taskResult = statusResult.result;
    if (taskResult?.insight_id) {
      const analysisRes = await apiRequest(
        'get',
        `/feedback/analysis/${taskResult.insight_id}/`,
        undefined,
        true
      );
      const analysisData = analysisRes.data?.data;
      if (analysisData?.exists && analysisData?.analysis) {
        const analysis = analysisData.analysis;
        return {
          id: analysis.id || taskResult.insight_id,
          analysisData: analysis.analysisData ?? analysis.result ?? analysis
        };
      }
      throw new Error('Analysis saved but not found by ID.');
    }
    const rawData = taskResult?.result ?? taskResult;
    return {
      id: taskResult?.insight_id || `analysis_${Date.now()}`,
      analysisData: rawData
    };
  };

  const { getValidAccessToken: getToken } = await import('@/lib/auth');
  const token = await getToken();
  const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '')
    || (process.env.NEXT_PUBLIC_API_BASE_URL ? `${process.env.NEXT_PUBLIC_API_BASE_URL.replace(/\/$/, '')}/api` : 'http://127.0.0.1:8000/api');
  const sseUrl = `${API_BASE}/insights/task-status/${taskId}/`;

  return new Promise(async (resolve, reject) => {
    try {
      const sseResponse = await fetch(sseUrl, {
        headers: {
          'Accept': 'text/event-stream',
          'Authorization': `Bearer ${token}`,
        },
      });

      if (sseResponse.ok && sseResponse.headers.get('content-type')?.includes('text/event-stream')) {
        const reader = sseResponse.body?.getReader();
        if (!reader) throw new Error('No stream reader');
        const decoder = new TextDecoder();
        let buffer = '';
        const timeout = setTimeout(() => { reader.cancel(); reject('Analysis timeout'); }, 900000);

        const processStream = async () => {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
              if (!line.startsWith('data: ')) continue;
              try {
                const statusResult = JSON.parse(line.slice(6));
                dispatch(pollTaskStatus.fulfilled(statusResult, '', taskId));
                const s = statusResult.status;
                if (s === 'SUCCESS' || s === 'PARTIAL') {
                  clearTimeout(timeout);
                  resolve(await resolveAnalysis(statusResult));
                  return;
                }
                if (s === 'FAILURE' || s === 'FAILED') {
                  clearTimeout(timeout);
                  reject(statusResult.error || 'Analysis failed');
                  return;
                }
              } catch { /* skip malformed line */ }
            }
          }
          clearTimeout(timeout);
          reject('SSE stream ended without terminal status');
        };
        await processStream();
        return;
      }
      throw new Error('SSE not supported');
    } catch {
      // Fallback: classic polling
      const pollInterval = setInterval(async () => {
        try {
          const statusResult = await dispatch(pollTaskStatus(taskId)).unwrap();
          const s = statusResult.status;
          if (s === 'SUCCESS' || s === 'PARTIAL') {
            clearInterval(pollInterval);
            resolve(await resolveAnalysis(statusResult));
          } else if (s === 'FAILURE' || s === 'FAILED') {
            clearInterval(pollInterval);
            reject(statusResult.error || 'Analysis failed');
          }
        } catch (error) {
          clearInterval(pollInterval);
          reject(error);
        }
      }, 2000);
      setTimeout(() => { clearInterval(pollInterval); reject('Analysis timeout'); }, 900000);
    }
  });
}

export const analyzeComments = createAsyncThunk<
  any,
  { comments: string[]; projectId?: string; fileName?: string },
  { rejectValue: string }
>('analysis/analyzeComments', async (data, { dispatch, rejectWithValue }) => {
  try {
    const payload: any = {
      comments: data.comments
    };
    if (data.projectId) {
      payload.project_id = data.projectId;
    }
    if (data.fileName) {
      payload.file_name = data.fileName;
    }

    const response = await apiRequest('post', '/insights/analyze/', payload, true, false);
    const taskId = response.data.data.task_id;
    if (!taskId) {
      throw new Error('No task ID received from server');
    }
    return await waitForAnalysisTask(taskId, dispatch);
  } catch (err: any) {
    let errorMessage = 'Sentiment analysis failed. Please try again.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 400) {
      errorMessage = err.response?.data?.detail || 'Invalid input data.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Resolves to the same shape as analyzeComments — both feed waitForAnalysisTask.
export const ingestFile = createAsyncThunk<
  any,
  { file: File; projectId?: string },
  { rejectValue: string }
>('analysis/ingestFile', async ({ file, projectId }, { dispatch, rejectWithValue }) => {
  try {
    const form = new FormData();
    form.append('file', file);
    if (projectId) {
      form.append('project_id', projectId);
    }

    const response = await apiRequest('post', '/insights/ingest/', form, true, true);
    const data = response.data.data;
    const taskId = data?.task_id;
    if (!taskId) {
      throw new Error('No task ID received from server');
    }
    if (Array.isArray(data?.comments) && data.comments.length > 0) {
      dispatch(setLoadedComments(data.comments));
    }
    return await waitForAnalysisTask(taskId, dispatch);
  } catch (err: any) {
    let errorMessage = 'File ingestion failed. Please try again.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 400) {
      errorMessage = err.response?.data?.detail || 'Invalid file.';
    } else if (err.response?.status === 503) {
      errorMessage = err.response?.data?.detail || 'Analysis service unavailable. Please try again later.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for getting latest analysis for a project
export const getLatestAnalysis = createAsyncThunk<
  any,
  string,
  { rejectValue: string }
>('analysis/getLatestAnalysis', async (projectId, { rejectWithValue }) => {
  try {
    const response = await apiRequest('get', `/integrations/projects/${projectId}/analysis/latest/`, { refresh: Date.now() }, true);
    return response.data.data;
  } catch (err: any) {
    let errorMessage = 'Failed to load latest analysis.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Analysis not found.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for getting consolidated dashboard data (replaces multiple API calls)
export const getConsolidatedDashboardData = createAsyncThunk<
  any,
  string,
  { rejectValue: string }
>('analysis/getConsolidatedDashboardData', async (projectId, { rejectWithValue }) => {
  try {
    const response = await apiRequest('get', `/integrations/projects/${projectId}/analysis/latest/`, { refresh: Date.now() }, true);
    return response.data.data;
  } catch (err: any) {
    let errorMessage = 'Failed to load dashboard data.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'No data found for this project.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for generating user stories/work items from analysis data
export const generateUserStories = createAsyncThunk<
  any,
  {
    analysisData: any;
    comments: string[];
    platform: 'azure' | 'jira';
    processTemplate?: string;
    projectId?: string;
    projectMetadata?: any;
  },
  { rejectValue: string }
>('analysis/generateUserStories', async (data, { rejectWithValue }) => {
  try {
    const payload: any = {
      analysis_data: data.analysisData,
      comments: data.comments,
      platform: data.platform, // Correctly pass the platform parameter
      process_template: data.processTemplate || 'Agile'
    };

    if (data.projectId) {
      payload.project_id = data.projectId;
    }

    if (data.projectMetadata) {
      payload.project_metadata = data.projectMetadata;
    }


    // User story generation involves LLM calls which can take longer - increase timeout to 3 minutes
    const response = await apiRequest(
      'post',
      '/insights/user-story-creation/',
      payload,
      true,
      false,
      { timeout: 180000 } // 3 minutes timeout for LLM calls
    );
    // Extract work items from response
    // Backend returns: { data: { work_items: [...], summary: {...}, ... } }
    const innerData = response.data.data || response.data;
    const userStoriesData = innerData?.user_stories?.[0] || innerData;
    return userStoriesData;
  } catch (err: any) {
    console.error('❌ generateUserStories error:', err);
    const status = err.response?.status;
    const body = err.response?.data;
    const apiDetail = typeof body?.detail === 'string' ? body.detail : body?.data?.detail;

    let errorMessage = `${data.platform === 'jira' ? 'Jira' : 'Azure'} work items generation failed. Please try again.`;

    if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
      errorMessage = 'Work item generation is taking longer than expected. This may happen with large datasets. Please try again or reduce the number of comments.';
    } else if (status === 401) {
      errorMessage = apiDetail || 'Authentication required. Please login again.';
    } else if (status === 400) {
      errorMessage = body?.error || apiDetail || 'Invalid input data.';
    } else if (status === 503 || (status && status >= 500)) {
      errorMessage = apiDetail || (status === 503 ? 'Service unavailable. Please try again later.' : 'Server error. Please try again later.');
    } else if (err.message) {
      errorMessage = err.message;
    }

    console.error('❌ Final error message:', errorMessage);
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for submitting user stories to ITSM tools
export const submitUserStories = createAsyncThunk<
  any,
  {
    userId: string;
    projectId: string;
    userStories: any[];
    platform: 'azure' | 'jira';
    processTemplate?: string;
    time?: string;
  },
  { rejectValue: string }
>('analysis/submitUserStories', async (data, { rejectWithValue }) => {
  try {
    const payload = {
      user_id: data.userId,
      project_id: data.projectId,
      user_stories: data.userStories,
      platform: data.platform,
      process_template: data.processTemplate || 'Agile',
      time: data.time || new Date().toISOString()
    };


    const response = await apiRequest('post', '/insights/user-story-submission/', payload, true, false);
    return response.data;
  } catch (err: any) {
    let errorMessage = `${data.platform === 'jira' ? 'Jira' : 'Azure DevOps'} user story submission failed. Please try again.`;
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 400) {
      errorMessage = err.response?.data?.error || err.response?.data?.detail || 'Invalid input data.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Project not found. Please check your project configuration.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for fetching analysis history for a project
export const fetchAnalysisHistory = createAsyncThunk<
  AnalysisHistoryEntry[],
  string,
  { rejectValue: string }
>('analysis/fetchAnalysisHistory', async (projectId, { rejectWithValue }) => {
  try {
    const response = await apiRequest('get', `/feedback/history/?project_id=${projectId}`, undefined, true);
    const analyses: any[] = response.data?.data?.analyses ?? [];
    return analyses.map((a: any): AnalysisHistoryEntry => {
      const counts = a.analysisData?.counts ?? a.result?.counts ?? a.counts ?? {};
      const total = Number(a.comments_count ?? counts.total ?? 0);
      const positive = Number(counts.positive ?? 0);
      return {
        id: a.id,
        analysis_date: a.createdAt ?? a.analysis_date ?? a._ts ?? '',
        comments_count: total,
        positive_pct: total > 0 ? Math.round((positive / total) * 100) : 0,
        status: a.status ?? 'completed',
        name: a.name ?? a.run_name ?? a.analysis_name ?? a.file_name,
      };
    });
  } catch (err: any) {
    let errorMessage = 'Failed to load analysis history.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for fetching a single analysis by ID
export const fetchAnalysisById = createAsyncThunk<
  any,
  string,
  { rejectValue: string }
>('analysis/fetchAnalysisById', async (analysisId, { rejectWithValue }) => {
  try {
    const response = await apiRequest('get', `/feedback/analysis/${analysisId}/`, undefined, true);
    return response.data?.data ?? response.data;
  } catch (err: any) {
    let errorMessage = 'Failed to load analysis.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Analysis not found.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for deleting an analysis run
export const deleteAnalysisRun = createAsyncThunk<
  string,
  string,
  { rejectValue: string }
>('analysis/deleteAnalysisRun', async (analysisId, { rejectWithValue }) => {
  try {
    await apiRequest('delete', `/feedback/analysis/${encodeURIComponent(analysisId)}/`, undefined, true);
    return analysisId;
  } catch (err: any) {
    let errorMessage = 'Failed to delete analysis.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 403) {
      errorMessage = 'You do not have permission to delete this analysis.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Analysis not found.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for renaming an analysis run
export const renameAnalysisRun = createAsyncThunk<
  { id: string; name: string | null },
  { id: string; name: string },
  { rejectValue: string }
>('analysis/renameAnalysisRun', async ({ id, name }, { rejectWithValue }) => {
  try {
    const trimmed = (name ?? '').trim();
    const payload = { name: trimmed.length > 0 ? trimmed : null };
    const response = await apiRequest('post', `/feedback/analysis/${encodeURIComponent(id)}/rename/`, payload, true);
    const data = response.data?.data;
    if (!data || typeof data.id !== 'string' || !('name' in data)) {
      throw new Error('Rename response missing updated name.');
    }
    return {
      id: data.id,
      name: data.name === null || typeof data.name === 'string' ? data.name : null
    };
  } catch (err: any) {
    let errorMessage = 'Failed to rename analysis run.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 403) {
      errorMessage = 'You do not have permission to rename this run.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Analysis not found.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

const analysisSlice = createSlice({
  name: 'analysis',
  initialState,
  reducers: {
    clearAnalysisData: (state) => {
      state.analysisData = null;
      state.deepAnalysis = null;
      state.error = null;
      state.projectContext = null;
      state.analysisHistory = [];
      state.historyLoading = false;
      state.historyError = null;
      state.selectedAnalysisId = null;
      state.fetchingAnalysisById = false;
    },
    setSelectedAnalysisId: (state, action: PayloadAction<string | null>) => {
      state.selectedAnalysisId = action.payload;
    },
    prependToHistory: (state, action: PayloadAction<AnalysisHistoryEntry>) => {
      const entry = action.payload;
      // Remove duplicate if exists, then prepend
      state.analysisHistory = [
        entry,
        ...state.analysisHistory.filter(e => e.id !== entry.id),
      ].slice(0, MAX_HISTORY_RUNS);
    },
    replaceInHistory: (state, action: PayloadAction<{ oldId: string; entry: AnalysisHistoryEntry }>) => {
      const idx = state.analysisHistory.findIndex(e => e.id === action.payload.oldId);
      if (idx >= 0) {
        state.analysisHistory[idx] = action.payload.entry;
      } else {
        // Fallback: prepend if not found
        state.analysisHistory = [
          action.payload.entry,
          ...state.analysisHistory,
        ];
      }
      state.analysisHistory = state.analysisHistory.slice(0, MAX_HISTORY_RUNS);
    },
    removeFromHistory: (state, action: PayloadAction<string>) => {
      state.analysisHistory = state.analysisHistory.filter(e => e.id !== action.payload);
    },
    renameHistoryEntry: (state, action: PayloadAction<{ id: string; name: string }>) => {
      const entry = state.analysisHistory.find(e => e.id === action.payload.id);
      if (entry) {
        entry.name = action.payload.name;
      }
    },
    clearError: (state) => {
      state.error = null;
    },
    setAnalyzing: (state, action: PayloadAction<boolean>) => {
      state.isAnalyzing = action.payload;
    },
    setLoadedComments: (state, action: PayloadAction<string[] | null>) => {
      state.loadedComments = action.payload;
    },
    setAnalysisData: (state, action: PayloadAction<AnalysisData | null>) => {
      state.analysisData = action.payload;
    },
    setDeepAnalysis: (state, action: PayloadAction<any | null>) => {
      state.deepAnalysis = action.payload;
    },
    setProjectContext: (state, action: PayloadAction<ProjectContext | null>) => {
      state.projectContext = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(analyzeComments.pending, (state, action) => {
        state.loading = true;
        state.error = null;
        state.isAnalyzing = true;
        const key = action.meta.arg.projectId ?? 'personal';
        state.analyzingByProject[key] = true;
        state.analysisStatus = 'pending';
      })
      .addCase(analyzeComments.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        const key = action.meta.arg.projectId ?? 'personal';
        delete state.analyzingByProject[key];
        state.isAnalyzing = Object.values(state.analyzingByProject).some(Boolean);
        state.analysisStatus = 'success';
        state.taskId = null;
        // Store the comments that were analyzed
        if (action.meta.arg.comments) {
          state.loadedComments = action.meta.arg.comments;
        }
        state.projectContext = action.payload?.context ?? state.projectContext;
        // The analysis data will be set by the component after normalization
      })
      .addCase(analyzeComments.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Analysis failed.';
        const key = action.meta.arg.projectId ?? 'personal';
        delete state.analyzingByProject[key];
        state.isAnalyzing = Object.values(state.analyzingByProject).some(Boolean);
        state.analysisStatus = 'failure';
        state.taskId = null;
      })
      .addCase(ingestFile.pending, (state, action) => {
        state.loading = true;
        state.error = null;
        state.isAnalyzing = true;
        const key = action.meta.arg.projectId ?? 'personal';
        state.analyzingByProject[key] = true;
        state.analysisStatus = 'pending';
      })
      .addCase(ingestFile.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        const key = action.meta.arg.projectId ?? 'personal';
        delete state.analyzingByProject[key];
        state.isAnalyzing = Object.values(state.analyzingByProject).some(Boolean);
        state.analysisStatus = 'success';
        state.taskId = null;
        state.projectContext = action.payload?.context ?? state.projectContext;
      })
      .addCase(ingestFile.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'File ingestion failed.';
        const key = action.meta.arg.projectId ?? 'personal';
        delete state.analyzingByProject[key];
        state.isAnalyzing = Object.values(state.analyzingByProject).some(Boolean);
        state.analysisStatus = 'failure';
        state.taskId = null;
      })
      .addCase(pollTaskStatus.pending, (state) => {
        state.analysisStatus = 'processing';
      })
      .addCase(pollTaskStatus.fulfilled, (state, action) => {
        // Status is updated by analyzeComments thunk based on result
        if (action.payload.status === 'SUCCESS') {
          state.analysisStatus = 'success';
        } else if (action.payload.status === 'FAILURE') {
          state.analysisStatus = 'failure';
        }
      })
      .addCase(pollTaskStatus.rejected, (state) => {
        state.analysisStatus = 'failure';
      })
      .addCase(getLatestAnalysis.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(getLatestAnalysis.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;

        // Check if analysis exists - if not, clear all analysis data
        if (!action.payload?.exists || !action.payload?.analysis) {
          state.analysisData = null;
          state.deepAnalysis = null;
          state.loadedComments = null;
          state.projectContext = null;
        }
        state.projectContext = action.payload?.analysis?.context ?? state.projectContext;
        // The analysis data will be set by the component after normalization if exists
      })
      .addCase(getLatestAnalysis.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to load latest analysis.';
      })
      .addCase(getConsolidatedDashboardData.pending, (state) => {
        state.latestLoading = true;
        state.latestError = null;
      })
      .addCase(getConsolidatedDashboardData.fulfilled, (state, action) => {
        state.latestLoading = false;
        state.latestError = null;
        state.latestAnalysis = action.payload;

        // Check if analysis exists - if not, clear all analysis data
        if (!action.payload?.exists || !action.payload?.analysis) {
          state.analysisData = null;
          state.deepAnalysis = null;
          state.loadedComments = null;
          state.projectContext = null;
        } else {
          if (action.payload.analysis.comments) {
            state.loadedComments = action.payload.analysis.comments.comments || [];
          }
          state.projectContext = action.payload.analysis.context ?? state.projectContext;
        }
      })
      .addCase(getConsolidatedDashboardData.rejected, (state, action) => {
        state.latestLoading = false;
        state.latestError = action.payload || 'Failed to load dashboard data.';
      })
      .addCase(generateUserStories.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(generateUserStories.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        state.deepAnalysis = action.payload;
        state.projectContext = action.payload?.context ?? state.projectContext;
      })
      .addCase(generateUserStories.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'User stories generation failed.';
      })
      .addCase(submitUserStories.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(submitUserStories.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        // Optionally store submission results
      })
      .addCase(submitUserStories.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'User stories submission failed.';
      })
      // Analysis history
      .addCase(fetchAnalysisHistory.pending, (state) => {
        state.historyLoading = true;
        state.historyError = null;
      })
      .addCase(fetchAnalysisHistory.fulfilled, (state, action) => {
        state.historyLoading = false;
        state.analysisHistory = action.payload.slice(0, MAX_HISTORY_RUNS);
      })
      .addCase(fetchAnalysisHistory.rejected, (state, action) => {
        state.historyLoading = false;
        state.historyError = action.payload || 'Failed to load history.';
      })
      // Fetch analysis by ID
      .addCase(fetchAnalysisById.pending, (state) => {
        state.fetchingAnalysisById = true;
      })
      .addCase(fetchAnalysisById.fulfilled, (state, action) => {
        state.fetchingAnalysisById = false;
        // Analysis data will be processed by the Dashboard component
      })
      .addCase(fetchAnalysisById.rejected, (state, action) => {
        state.fetchingAnalysisById = false;
        state.error = action.payload || 'Failed to load analysis.';
      })
      // Delete analysis run
      .addCase(deleteAnalysisRun.fulfilled, (state, action) => {
        state.analysisHistory = state.analysisHistory.filter(e => e.id !== action.payload);
        // Clear current analysis if it was the deleted one
        if (state.selectedAnalysisId === action.payload) {
          state.selectedAnalysisId = null;
          state.analysisData = null;
          state.deepAnalysis = null;
          state.loadedComments = null;
        }
      })
      // Rename analysis run
      .addCase(renameAnalysisRun.fulfilled, (state, action) => {
        const entry = state.analysisHistory.find(e => e.id === action.payload.id);
        if (entry) {
          entry.name = action.payload.name || undefined;
        }
      });
  },
});

export const {
  clearAnalysisData,
  clearError,
  setAnalyzing,
  setLoadedComments,
  setAnalysisData,
  setDeepAnalysis,
  setProjectContext,
  setSelectedAnalysisId,
  prependToHistory,
  replaceInHistory,
  removeFromHistory,
  renameHistoryEntry,
} = analysisSlice.actions;

export default analysisSlice.reducer; 

