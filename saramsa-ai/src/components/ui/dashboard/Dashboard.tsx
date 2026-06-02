'use client';

import { useEffect, useState, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
// encryptProjectId moved to WorkItemsPanel.tsx (Phase 2).
import {
  analyzeComments,
  ingestFile,
  getLatestAnalysis,
  getConsolidatedDashboardData,
  generateUserStories,
  fetchAnalysisHistory,
  fetchAnalysisById,
  setAnalysisData,
  setDeepAnalysis,
  setLoadedComments,
  clearAnalysisData,
  clearError,
  setSelectedAnalysisId,
  prependToHistory,
  replaceInHistory,
  removeFromHistory,
  renameAnalysisRun,
  deleteAnalysisRun,
  cancelAnalysisTask,
  setTaskIdForEntry,
  resolveAnalyzingTask,
  resumeInFlightTask,
} from '../../../store/features/analysis/analysisSlice';
import type { AnalysisHistoryEntry } from '../../../store/features/analysis/analysisSlice';
import {
  ANALYZING_PREFIX,
  HISTORY_STATUS,
  isAnalyzingPlaceholder,
  makeAnalyzingId,
  extractTaskIdFromPlaceholder,
} from '@/lib/analysisConstants';
import { fetchProjects } from '../../../store/features/projects/projectsSlice';
import { fetchIntegrationAccounts } from '../../../store/features/integrations/integrationsSlice';
import { 
  clearCurrentProjectUserStories,
  setCurrentProjectUserStories,
  fetchUserStoriesByProject
} from '../../../store/features/userStories/userStoriesSlice';


import type { AnalysisData } from '@/types/analysis';
import { apiRequest } from '@/lib/apiRequest';
import { Check, Loader2, CheckCircle } from 'lucide-react';
import { toast } from 'sonner';
import { UploadPanel } from './UploadPanel';
import { SlackChannelPanel } from './SlackChannelPanel';
// MetricsCards, FeatureSentimentsTable, SentimentCharts, KeywordCloud,
// AdvancedWordCloud, AlertCircle moved to InsightsPanel.tsx (Phase 3).
// UserStoryList, encryptProjectId, Sparkles moved to WorkItemsPanel.tsx (Phase 2).
// import { NavigationTabs } from './NavigationTabs'; // Inlined below

import { AnalysisRunList } from './AnalysisRunList';
import { InsightsPanel } from './InsightsPanel';
import { WorkItemsPanel } from './WorkItemsPanel';
// import { DynamicFilterBar } from '../../dashboard/DynamicFilterBar'; // TODO: Re-enable when filters are fully implemented

// Local interface for the component
interface LocalFeatureSentiment {
  name: string;
  description: string;
  sentiment: {
    positive: number;
    negative: number;
    neutral: number;
  };
  keywords: string[];
  comment_count?: number;
  isEdited?: boolean;
}

interface DashboardProps {
  data?: AnalysisData;
  onProjectSelect?: (projectId: string) => void;
  initialProjectId?: string;
  initialSelectedAnalysisId?: string | null;
  skipBootstrapFetches?: boolean; // when true, parent handles projects/integrations fetching
}

const MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024; // 10MB

function validateSelectedFile(file: File): { isValid: boolean; error?: string } {
  const name = file.name.toLowerCase();
  const isSupported = name.endsWith('.csv')
    || name.endsWith('.json')
    || name.endsWith('.pdf')
    || name.endsWith('.txt')
    || name.endsWith('.docx');
  if (!isSupported) {
    return { isValid: false, error: 'Please upload a CSV, JSON, PDF, TXT, or DOCX file.' };
  }
  if (file.size <= 0) {
    return { isValid: false, error: 'Selected file is empty.' };
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return { isValid: false, error: 'File is too large. Max size is 10MB.' };
  }
  return { isValid: true };
}

export function DashboardComponent({ data, onProjectSelect, initialProjectId, initialSelectedAnalysisId, skipBootstrapFetches = false }: DashboardProps) {
  const dispatch = useDispatch<AppDispatch>();
  const {
    analysisData,
    deepAnalysis,
    loading,
    error,
    isAnalyzing,
    analysisStatus,
    loadedComments,
    latestAnalysis,
    projectContext,
    analysisHistory,
    historyLoading,
    selectedAnalysisId,
    fetchingAnalysisById,
  } = useSelector((state: RootState) => state.analysis);
  
  const { projects, loading: projectsLoading } = useSelector((state: RootState) => state.projects);
  const { accounts: integrationAccounts, loading: integrationsLoading } = useSelector((state: RootState) => state.integrations);
  const { user } = useSelector((state: RootState) => state.auth);
  const {
    currentProjectUserStories,
    loading: userStoriesLoading,
  } = useSelector((state: RootState) => state.userStories);
  
  const [activeView, setActiveView] = useState<'dashboard' | 'user-stories'>('dashboard');
  const [topFile, setTopFile] = useState<File | null>(null);
 
  const [topError, setTopError] = useState<string | null>(null);
  const [selectedFeatures, setSelectedFeatures] = useState<string[]>([]);
  const [editedKeywords, setEditedKeywords] = useState<{ [key: string]: string[] }>({});
  const [currentProjectId, setCurrentProjectId] = useState<string>("");
  const [personalProjectId, setPersonalProjectId] = useState<string>('');
  const [isGeneratingUserStories, setIsGeneratingUserStories] = useState<boolean>(false);
  const [isSwitchingAnalysis, setIsSwitchingAnalysis] = useState<boolean>(false);

  // Declare all refs at the top to prevent recreation on every render
  const didInitRef = useRef(false);
  const hasConsolidatedFetchRef = useRef(false);
  const lastFetchedProjectRef = useRef<string | null>(null);
  const lastProcessedAnalysisIdRef = useRef<string | null>(null);
  const lastHistoryProjectRef = useRef<string | null>(null);
  const initialSelectionAppliedRef = useRef<string | null>(null);
  // Track pending setTimeout ids and the in-flight AbortController for the
  // analysis-switch fetch so we can cancel them on unmount or when the user
  // switches analyses faster than the network. Without this, react throws
  // "Can't perform state update on unmounted component" warnings and the
  // result of an abandoned fetch can overwrite the new selection's data.
  const pendingTimeoutsRef = useRef<number[]>([]);
  const analysisFetchAbortRef = useRef<AbortController | null>(null);
  // Helper: schedule a setTimeout and track its id so the unmount-cleanup
  // effect can clear it. Mirrors setTimeout's signature so call sites stay
  // small.
  const scheduleTimeout = (fn: () => void, ms: number): number => {
    const id = window.setTimeout(() => {
      pendingTimeoutsRef.current = pendingTimeoutsRef.current.filter(t => t !== id);
      fn();
    }, ms);
    pendingTimeoutsRef.current.push(id);
    return id;
  };
  // Unmount cleanup: clear any pending timeouts and abort any in-flight
  // analysis-by-id fetch. Runs once when the component unmounts.
  useEffect(() => {
    return () => {
      for (const id of pendingTimeoutsRef.current) window.clearTimeout(id);
      pendingTimeoutsRef.current = [];
      analysisFetchAbortRef.current?.abort();
      analysisFetchAbortRef.current = null;
    };
  }, []);

  // Hydration sweeper: reconcile in-flight task state against the live task
  // list on mount. Two reconciliations, both single-shot per mount:
  //
  // A) If Redux comes back with a stale `analyzing_*` placeholder (the user
  //    closed the tab before the task completed and the persistor restored
  //    it) AND the matching task is now terminal or missing → dispatch
  //    resolveAnalyzingTask so the loading skeletons release immediately.
  //
  // B) If Redux has NO placeholder (refresh wiped the non-persisted analysis
  //    slice — see store.ts whitelist: ['auth']) BUT the task list still has
  //    a non-terminal task for the current project → re-seed the optimistic
  //    history entry + placeholder + resumeInFlightTask so the "Analyzing..."
  //    tile reappears and the existing polling pipeline takes over. Without
  //    this, an in-progress upload silently vanishes from the UI on refresh
  //    and the user only sees completion after the next refresh.
  const hydrationSweptRef = useRef(false);
  useEffect(() => {
    if (hydrationSweptRef.current) return;
    // Wait until we know the current project; the re-seed in branch B is
    // project-scoped so seeding before currentProjectId resolves would attach
    // to the wrong project (or 'personal' fallback). Branch A doesn't need
    // the project but we run them together.
    if (!currentProjectId) return;
    hydrationSweptRef.current = true;

    const placeholderTaskId = isAnalyzingPlaceholder(selectedAnalysisId)
      ? extractTaskIdFromPlaceholder(selectedAnalysisId)
      : null;

    (async () => {
      try {
        const resp = await apiRequest('get', '/insights/tasks/', undefined, true);
        const list: any[] = resp?.data?.data?.tasks ?? [];

        // Branch A: existing-placeholder reconciliation.
        if (placeholderTaskId) {
          const match = list.find(t => t?.task_id === placeholderTaskId);
          if (!match || ['SUCCESS', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(match?.status)) {
            dispatch(resolveAnalyzingTask({
              taskId: placeholderTaskId,
              placeholderId: selectedAnalysisId as string,
              historyStatus: match?.status === 'CANCELLED'
                ? HISTORY_STATUS.CANCELLED
                : match?.analysis_id && match?.status !== 'FAILED'
                ? HISTORY_STATUS.COMPLETED
                : HISTORY_STATUS.FAILED,
              insightId: match?.analysis_id ? `insight_${match.analysis_id}` : null,
              nextTaskStatus: match?.analysis_id && match?.status !== 'FAILED' ? 'success' : 'failure',
            }));
          }
          // If still RUNNING/PENDING, branch B below will re-attach (the
          // placeholder is for the same task and we want the polling pipeline
          // running again).
        }

        // Branch B: cold-mount resume. Pick the newest non-terminal task for
        // the current project. Skip if branch A already has the same task
        // wired up via the placeholder.
        const NON_TERMINAL = ['RUNNING', 'PENDING', 'STARTED', 'PROCESSING'];
        const liveTaskForProject = list
          .filter(t => t?.project_id === currentProjectId && NON_TERMINAL.includes(String(t?.status || '').toUpperCase()))
          .sort((a, b) => String(b?.started_at || '').localeCompare(String(a?.started_at || '')))[0];

        if (liveTaskForProject && liveTaskForProject.task_id !== placeholderTaskId) {
          const tid = liveTaskForProject.task_id as string;
          const tempId = makeAnalyzingId(tid);
          dispatch(prependToHistory({
            id: tempId,
            analysis_date: liveTaskForProject.started_at || new Date().toISOString(),
            comments_count: Number(liveTaskForProject.comment_count ?? 0),
            positive_pct: 0,
            status: HISTORY_STATUS.ANALYZING,
            file_name: liveTaskForProject.file_name || undefined,
            task_id: tid,
          }));
          dispatch(setSelectedAnalysisId(tempId));
          // Re-attach to the running task; pending/fulfilled/rejected wired in
          // the slice's extraReducers will drive isAnalyzing + status forward.
          dispatch(resumeInFlightTask({ taskId: tid, projectId: currentProjectId }));
        }
      } catch {
        // If the task-list endpoint is unreachable, leave state alone — the
        // user will see the loader briefly and the next normal poll will
        // reconcile. Better than wrongly clearing on a transient network
        // hiccup.
      }
    })();
  }, [currentProjectId, selectedAnalysisId, dispatch]);
  useEffect(() => {
    const contextProjectId = projectContext?.project_id;
    if (!contextProjectId) return;

    if (projectContext?.is_draft) {
      setPersonalProjectId(contextProjectId);
    }

    if (!currentProjectId) {
      setCurrentProjectId(contextProjectId);
    }

    if (typeof window !== 'undefined') {
      localStorage.setItem('project_id', contextProjectId);
    }
  }, [projectContext]); // Removed currentProjectId from dependencies to prevent loop

  useEffect(() => {
    if (!initialSelectedAnalysisId) return;
    if (initialSelectionAppliedRef.current === initialSelectedAnalysisId) return;
    dispatch(setSelectedAnalysisId(initialSelectedAnalysisId));
    lastProcessedAnalysisIdRef.current = null;
    initialSelectionAppliedRef.current = initialSelectedAnalysisId;
  }, [dispatch, initialSelectedAnalysisId]);
  const [wordCloudView, setWordCloudView] = useState<'split' | 'advanced'>('split');
  const [resultsTab, setResultsTab] = useState<'insights' | 'workitems'>('insights');
  // const [dimensionFilters, setDimensionFilters] = useState<any[]>([]); // TODO: Re-enable when filters are fully implemented
  // const [filteredStats, setFilteredStats] = useState<any>(null);

  // Memoize the localStorage-derived projectId and the projects-array lookup
  // for selectedProjectName. Without memoization, both ran on every render —
  // the localStorage read is cheap (~1µs) but the projects.find() is a linear
  // scan that ran on every keystroke / hover / state change. Per the Phase 1
  // agent audit (CRITICAL #7), this was a measurable hot path.
  //
  // Same-tab writes to localStorage 'project_id' originate from this file's
  // own effect (line 236, triggered by projectContext change), so re-reading
  // when projectContext changes covers every meaningful update. Cross-tab
  // writes won't be observed (no 'storage' event subscription) — that matches
  // the original behavior since this read was synchronous.
  const projectId = useMemo(
    () => (typeof window !== 'undefined' ? localStorage.getItem('project_id') : null),
    [projectContext, currentProjectId]
  );
  const selectedProjectName = useMemo(
    () => projects?.find((p: any) => p.id === (currentProjectId || projectId))?.name,
    [projects, currentProjectId, projectId]
  );
  const slackAccount = useMemo(() => {
    return integrationAccounts.find((account: any) => account.provider === 'slack' && account.status === 'active');
  }, [integrationAccounts]);
  const slackDisplayName = useMemo(() => {
    if (!slackAccount) return null;
    return (
      slackAccount.metadata?.workspace ||
      slackAccount.metadata?.team ||
      slackAccount.metadata?.domain ||
      slackAccount.displayName ||
      null
    );
  }, [slackAccount]);
  const isProjectAnalyzing = isAnalyzing;
  const isTaskListLoading = useMemo(
    () => historyLoading && analysisHistory.length === 0,
    [historyLoading, analysisHistory.length]
  );
  const isTaskViewLoading = useMemo(
    () =>
      fetchingAnalysisById ||
      isSwitchingAnalysis ||
      // Only show loading if we're viewing the "analyzing" entry itself
      // Don't block viewing old analyses just because a new one is running in background
      isAnalyzingPlaceholder(selectedAnalysisId),
    [fetchingAnalysisById, isSwitchingAnalysis, selectedAnalysisId]
  );

  const workItemsPanelLoading = useMemo(
    () =>
      isTaskViewLoading ||
      userStoriesLoading ||
      isGeneratingUserStories,
    [isTaskViewLoading, userStoriesLoading, isGeneratingUserStories]
  );
  const selectedPlatform = useMemo((): 'azure' | 'jira' | null => {
    if (!projects || !projects.length) return null;
    const pid = currentProjectId || projectId || '';
    const proj = projects.find((p: any) => p.id === pid);
    const provider = proj?.externalLinks?.[0]?.provider;
    return provider === 'jira' ? 'jira' : provider === 'azure' ? 'azure' : null;
  }, [projects, currentProjectId, projectId]);

  const hasGeneratedWorkItems = useMemo(
    () =>
      Boolean(deepAnalysis?.work_items?.length) ||
      Boolean(currentProjectUserStories?.some((story: any) => story?.work_items?.length)),
    [deepAnalysis, currentProjectUserStories]
  );

  // Use analysis data directly (no cumulative view)
  const activeAnalysisData = analysisData;

  const hasAnalysisResults = useMemo(() => {
    if (!activeAnalysisData?.analysisData) return false;
    const counts = activeAnalysisData.analysisData.counts;
    const features = activeAnalysisData.analysisData.features;
    const positiveKeywords = activeAnalysisData.analysisData.positive_keywords;
    const negativeKeywords = activeAnalysisData.analysisData.negative_keywords;

    const totalComments = counts?.total ?? 0;
    const hasFeatureData = Array.isArray(features) && features.length > 0;
    const hasKeywordData =
      (Array.isArray(positiveKeywords) && positiveKeywords.length > 0) ||
      (Array.isArray(negativeKeywords) && negativeKeywords.length > 0);

    return totalComments > 0 || hasFeatureData || hasKeywordData;
  }, [activeAnalysisData?.analysisData]);

  const analysisProgressUi = useMemo(() => {
    // Only show progress bar when the currently selected task is the one being analyzed
    // i.e. the user is watching a live run, not viewing a historical entry
    const isViewingActiveRun = isAnalyzingPlaceholder(selectedAnalysisId);
    const isCurrentlyAnalyzing = isViewingActiveRun && (isAnalyzing || analysisStatus === 'pending' || analysisStatus === 'processing');
    const isGeneratingItems = isViewingActiveRun && isGeneratingUserStories;

    // Don't show progress bar for old completed analyses that are just being viewed
    if (!isCurrentlyAnalyzing && !isGeneratingItems) {
      return null;
    }

    switch (analysisStatus) {
      case 'pending':
        return { label: 'Queued', width: 'w-1/4', tone: 'bg-orange-400/80', text: 'text-orange-600 dark:text-orange-400' };
      case 'processing':
        return { label: 'Processing', width: 'w-2/3', tone: 'bg-orange-500/80', text: 'text-orange-600 dark:text-orange-400' };
      case 'success':
        if (isGeneratingItems) {
          return { label: 'Generating Work Items', width: 'w-3/4', tone: 'bg-orange-600/80', text: 'text-orange-600 dark:text-orange-400' };
        }
        if (isViewingActiveRun) {
          // Only mark the run "Completed" once the work-items pass has also
          // finished. The stepper's stage 4 checkmark uses hasGeneratedWorkItems
          // (per analysisProgressSteps below); without this guard, the right-
          // side badge can read "Completed" while stage 4 still shows as idle
          // — exactly the desync screenshot users have reported.
          if (hasGeneratedWorkItems) {
            return { label: 'Completed', width: 'w-full', tone: 'bg-saramsa-brand/80', text: 'text-saramsa-brand' };
          }
          return { label: 'Synthesizing work items', width: 'w-5/6', tone: 'bg-orange-500/80', text: 'text-orange-600 dark:text-orange-400' };
        }
        return null;
      case 'failure':
        if (isViewingActiveRun) {
          return { label: 'Failed', width: 'w-full', tone: 'bg-red-700/80', text: 'text-red-700 dark:text-red-400' };
        }
        return null;
      default:
        return null;
    }
  }, [analysisStatus, isGeneratingUserStories, isAnalyzing, selectedAnalysisId, hasGeneratedWorkItems]);

  const analysisProgressSteps = useMemo(() => {
    const base = [
      { label: 'Ingestion', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Processing', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Synthesis', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Work Items', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
    ];

    const isViewingActiveRun = isAnalyzingPlaceholder(selectedAnalysisId);
    if (!isViewingActiveRun && !isGeneratingUserStories) return base;

    if (analysisStatus === 'pending') {
      base[0].status = 'running';
      return base;
    }
    if (analysisStatus === 'processing') {
      base[0].status = 'success';
      base[1].status = 'running';
      return base;
    }
    if (analysisStatus === 'success') {
      base[0].status = 'success';
      base[1].status = 'success';
      base[2].status = 'success';
      base[3].status = isGeneratingUserStories
        ? 'running'
        : hasGeneratedWorkItems
        ? 'success'
        : 'idle';
      return base;
    }
    if (analysisStatus === 'failure') {
      base[0].status = 'success';
      base[1].status = 'error';
      return base;
    }

    return base;
  }, [analysisStatus, hasGeneratedWorkItems, isGeneratingUserStories, isAnalyzing, selectedAnalysisId]);


  // Handle regeneration of analysis
  const handleRegenerateAnalysis = async () => {
    if (!loadedComments || loadedComments.length === 0) {
      console.error('No comments available for regeneration');
      
        // Try to load comments from backend
        const regenerationProjectId = currentProjectId || personalProjectId || '';
        if (regenerationProjectId || !currentProjectId) {
          try {
            const queryParam = regenerationProjectId ? `project_id=${regenerationProjectId}` : 'is_personal=true';
            const response = await apiRequest('get', `/insights/comments/?${queryParam}`, undefined, true);
          if (response.data.success && response.data.data.comments && response.data.data.comments.length > 0) {
            dispatch(setLoadedComments(response.data.data.comments));
            if (!regenerationProjectId && response.data.data.project_id) {
              setPersonalProjectId(response.data.data.project_id);
            }
            // Continue with regeneration using the loaded comments
          } else {
            alert('No comments found for this analysis. Please upload a file with comments first.');
            return;
          }
        } catch (error: any) {
          console.error('❌ Error loading comments from backend:', error);
          console.error('Error details:', {
            status: error.response?.status,
            statusText: error.response?.statusText,
            data: error.response?.data,
            url: error.config?.url
          });
          alert('Failed to load comments from backend. Please try again or upload a new file.');
          return;
        }
      } else {
        alert('No project ID available. Please select a project first.');
        return;
      }
    }

    try {
      // Call the new backend endpoint for keyword updates and regeneration
      const response = await apiRequest('post', '/feedback/keywords/update/', {
        project_id: currentProjectId || personalProjectId || undefined,
        updated_keywords: editedKeywords,
        comments: loadedComments
      }, true);

      if (response.data.success) {
        // Update the analysis data with the new results
        dispatch(setAnalysisData(response.data));
        
        // Clear edited keywords after successful regeneration
        setEditedKeywords({});
        
      }
    } catch (error) {
      console.error('Failed to regenerate analysis:', error);
      alert('Failed to regenerate analysis. Please try again.');
    }
  };

  // Clear all stored data (comments and analysis)
  const handleClearData = () => {
    if (confirm('Are you sure you want to clear all stored data? This will remove comments and analysis results.')) {
      dispatch(setLoadedComments(null));
      dispatch(clearAnalysisData());
      setEditedKeywords({});
    }
  };

  // Transform features to include edited status
  const transformedFeatures = useMemo(() => {
    if (!activeAnalysisData?.analysisData?.features) return [];

    return activeAnalysisData.analysisData.features.map((feature: any) => ({
      name: feature.name || feature.feature,  // Backend uses "feature" field, fallback to "name"
      description: feature.description || '',
      sentiment: {
        positive: feature.positive || feature.sentiment?.positive || 0,
        negative: feature.negative || feature.sentiment?.negative || 0,
        neutral: feature.neutral || feature.sentiment?.neutral || 0,
      },
      keywords: feature.keywords || [],
      comment_count: feature.comment_count,
      isEdited: editedKeywords[feature.name || feature.feature] !== undefined,
      sample_comments: feature.sample_comments
    })) as LocalFeatureSentiment[];
  }, [activeAnalysisData?.analysisData?.features, editedKeywords]);
  const userStoryFromDeepAnalysis = useMemo(() => {
    if (!deepAnalysis?.work_items || deepAnalysis.work_items.length === 0) {
      return null;
    }

    const derivedId =
      deepAnalysis.id ||
      (deepAnalysis.projectId ? `consolidated_${deepAnalysis.projectId}` : currentProjectId ? `consolidated_${currentProjectId}` : undefined);

    if (!derivedId) {
      return null;
    }

    return {
      id: derivedId,
      type: deepAnalysis.type || 'user_story',
      userId: deepAnalysis.userId,
      projectId: deepAnalysis.projectId || currentProjectId,
      process_template: deepAnalysis.process_template || 'Agile',
      platform: deepAnalysis.platform,
      work_items: deepAnalysis.work_items,
      summary: deepAnalysis.summary,
      comments_count: deepAnalysis.comments_count || 0,
      generated_at: deepAnalysis.createdAt || deepAnalysis.generated_at || new Date().toISOString()
    };
  }, [deepAnalysis]);

  useEffect(() => {
    if (!userStoryFromDeepAnalysis) return;
    if (currentProjectUserStories && currentProjectUserStories.length > 0) return;

    dispatch(setCurrentProjectUserStories([userStoryFromDeepAnalysis]));
  }, [dispatch, userStoryFromDeepAnalysis, currentProjectUserStories]);
  
  // Process latestAnalysis from getConsolidatedDashboardData and set analysisData
  useEffect(() => {
    if (!latestAnalysis) {
      return;
    }

    const analysisId = latestAnalysis.analysis?.id;

    // CRITICAL: If user is viewing a historical analysis and a new analysis completes,
    // show notification but don't overwrite their current view
    if (selectedAnalysisId && !isAnalyzingPlaceholder(selectedAnalysisId)) {
      // Check if this is a newly completed analysis we haven't notified about
      // BUG FIX: Add project check to prevent showing toast when switching projects
      if (analysisId &&
          lastProcessedAnalysisIdRef.current !== analysisId &&
          latestAnalysis.exists &&
          latestAnalysis.analysis?.projectId === currentProjectId) {
        // Update the sidebar with the new completed analysis
        const a = latestAnalysis.analysis;
        const counts = a.analysisData?.counts ?? a.result?.counts ?? a.counts ?? {};
        const total = Number(counts.total ?? 0);
        const positive = Number(counts.positive ?? 0);

        dispatch(prependToHistory({
          id: analysisId,
          analysis_date: a.createdAt || a.analysis_date || new Date().toISOString(),
          comments_count: total,
          positive_pct: total > 0 ? Math.round((positive / total) * 100) : 0,
          status: 'completed',
          name: a.name,
        }));

        // Toast notification removed per user request — analysis completion is
        // already visible in sidebar history, no need for additional notification

        lastProcessedAnalysisIdRef.current = analysisId;
      }
      return; // Don't overwrite the current view
    }

    // Skip if we've already processed this analysis
    if (analysisId && lastProcessedAnalysisIdRef.current === analysisId) {
      return;
    }
    if (latestAnalysis.exists && latestAnalysis.analysis) {
      const a = latestAnalysis.analysis; // Extract the nested analysis data
      // BUG FIX: Verify this analysis belongs to the current project to prevent showing stale data on project switch
      if (a.projectId && a.projectId !== currentProjectId && a.projectId !== `project_${currentProjectId}`) {
        return;
      }
      // The backend now returns data in the new format (analysisData field)
      // Check if data is already in the correct frontend format
      if (a.analysisData) {
        // Data is already in the new format, use it directly
        dispatch(setAnalysisData(a));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = a.id;
        // Select this run in the sidebar and ensure it exists in history
        // Don't overwrite if hydration sweeper set an analyzing placeholder after refresh
        if (a.id && !isAnalyzingPlaceholder(selectedAnalysisId)) {
          dispatch(setSelectedAnalysisId(a.id));
          const counts = a.analysisData.counts ?? {};
          const total = Number(counts.total ?? 0);
          const positive = Number(counts.positive ?? 0);
          dispatch(prependToHistory({
            id: a.id,
            analysis_date: a.createdAt || a.analysis_date || new Date().toISOString(),
            comments_count: total,
            positive_pct: total > 0 ? Math.round((positive / total) * 100) : 0,
            status: 'completed',
            name: a.name,
          }));
        }
      } else if (a.result?.overall && a.result?.counts && a.result?.features !== undefined) {
        // Data is nested under result field - normalize it and merge metadata
        const normalized = normalizeAnalysis(a.result);
        // Merge metadata from the analysis object
        if (normalized) {
          normalized.id = a.id || normalized.id;
          normalized.projectId = a.projectId || normalized.projectId;
          normalized.userId = a.userId || normalized.userId;
          normalized.createdAt = a.createdAt || a.analysis_date || normalized.createdAt;
          normalized.analysisType = a.analysis_type || normalized.analysisType;
        }
        dispatch(setAnalysisData(normalized));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = normalized?.id || a.id;
      } else if (a.sentimentsummary && a.counts && a.featureasba !== undefined) {
        // Data is in the old format, normalize it
        const normalized = normalizeAnalysis(a);
        dispatch(setAnalysisData(normalized));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = normalized?.id || a.id;
      } else if (a.overall && a.counts && a.features !== undefined) {
        // Fallback: data is in the old format, normalize it
        const normalized = normalizeAnalysis(a);
        dispatch(setAnalysisData(normalized));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = normalized?.id || a.id;
      } else if (a.commentAnalysis) {
        // Fallback: use commentAnalysis if available
        const ca = Array.isArray(a.commentAnalysis)
          ? (typeof a.commentAnalysis[0] === 'string' ? JSON.parse(a.commentAnalysis[0]) : a.commentAnalysis[0])
          : a.commentAnalysis;
        const normalized = normalizeAnalysis(ca);
        dispatch(setAnalysisData(normalized));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = normalized?.id || a.id;
      } else {
        dispatch(setAnalysisData(null));
        dispatch(setDeepAnalysis(null));
        lastProcessedAnalysisIdRef.current = null;
      }
    } else {
      dispatch(setAnalysisData(null));
      dispatch(setDeepAnalysis(null));
      lastProcessedAnalysisIdRef.current = null;
    }
  }, [latestAnalysis, dispatch, selectedAnalysisId]);

  // Extract user stories from consolidated data and set in Redux store
  useEffect(() => {
    // CRITICAL: Don't process latestAnalysis work items if user has selected a specific historical analysis
    // This prevents overwriting the selected analysis's work items with the latest analysis
    if (selectedAnalysisId && !isAnalyzingPlaceholder(selectedAnalysisId)) {
      return;
    }

    // BUG FIX: Verify this analysis belongs to the current project to prevent showing stale data on project switch
    if (latestAnalysis?.analysis?.projectId &&
        latestAnalysis.analysis.projectId !== currentProjectId &&
        latestAnalysis.analysis.projectId !== `project_${currentProjectId}`) {
      return;
    }

    if (latestAnalysis?.analysis?.userStories?.work_items) {
      const workItems = latestAnalysis.analysis.userStories.work_items;
      
      // Convert work items to user stories format for compatibility
      const userStoriesData = [{
        id: `consolidated_${currentProjectId}`,
        type: 'user_story',
        userId: user?.id || user?.user_id || '',
        projectId: currentProjectId,
        process_template: latestAnalysis.analysis.userStories.process_template || 'Agile',
        platform: latestAnalysis.analysis.userStories.platform || selectedPlatform || 'azure',
        generated_at: latestAnalysis.analysis.userStories.generated_at,
        work_items: workItems,
        summary: latestAnalysis.analysis.userStories.summary || {},
        comments_count: latestAnalysis.analysis.userStories.comments_count || 0
      }];
      
      dispatch(setCurrentProjectUserStories(userStoriesData));
      
      // Also set deepAnalysis state for compatibility with existing logic
      const deepAnalysisData = {
        id: `consolidated_${currentProjectId}`,
        type: 'user_story',
        userId: user?.id || user?.user_id || '',
        projectId: currentProjectId,
        process_template: latestAnalysis.analysis.userStories.process_template || 'Agile',
        platform: latestAnalysis.analysis.userStories.platform || selectedPlatform || 'azure',
        generated_at: latestAnalysis.analysis.userStories.generated_at,
        work_items: workItems,
        work_items_by_feature: latestAnalysis.analysis.userStories.work_items_by_feature || {},
        summary: latestAnalysis.analysis.userStories.summary || {},
        comments_count: latestAnalysis.analysis.userStories.comments_count || 0
      };
      
      dispatch(setDeepAnalysis(deepAnalysisData));
    } else if (latestAnalysis && (!latestAnalysis.exists || !latestAnalysis.analysis)) {
      // Clear user stories when no analysis data exists for the project
      dispatch(clearCurrentProjectUserStories());
      dispatch(setDeepAnalysis(null));
    }
  }, [latestAnalysis, currentProjectId, user, dispatch, selectedAnalysisId]);

  // Fetch projects and integration accounts on mount (guard against double-invoke in dev)
  useEffect(() => {
    if (skipBootstrapFetches) return;
    if (didInitRef.current) return;
    didInitRef.current = true;
    dispatch(fetchProjects());
    dispatch(fetchIntegrationAccounts());
  }, [dispatch, skipBootstrapFetches]);

  // Fetch analysis history when project changes
  useEffect(() => {
    const pid = currentProjectId || projectId || '';
    if (!pid || lastHistoryProjectRef.current === pid) return;
    lastHistoryProjectRef.current = pid;
    dispatch(fetchAnalysisHistory(pid));
  }, [currentProjectId, projectId, dispatch]);

  // Load full analysis when a historical run is selected
  useEffect(() => {
    if (!selectedAnalysisId) {
      setIsSwitchingAnalysis(false);
      return;
    }
    // Skip fetch for temporary "analyzing" entries
    if (isAnalyzingPlaceholder(selectedAnalysisId)) {
      setIsSwitchingAnalysis(false);
      return;
    }
    // If the currently loaded analysis already matches, skip fetch
    if (analysisData && (analysisData as any).id === selectedAnalysisId) {
      setIsSwitchingAnalysis(false);
      return;
    }

    // BUG FIX: Set switching state and clear previous analysis data immediately to prevent flash of stale content
    setIsSwitchingAnalysis(true);

    // Clear all previous analysis data synchronously to prevent showing stale work items or analysis results
    dispatch(setAnalysisData(null));
    dispatch(clearCurrentProjectUserStories());
    dispatch(setDeepAnalysis(null));

    // Abort any previous in-flight fetch so a slow earlier selection can't
    // overwrite the new one. Critical when users click history entries fast.
    analysisFetchAbortRef.current?.abort();
    const controller = new AbortController();
    analysisFetchAbortRef.current = controller;

    (async () => {
      try {
        const result = await dispatch(fetchAnalysisById(selectedAnalysisId)).unwrap();
        // If the user switched again before this resolved, drop the result.
        if (controller.signal.aborted) return;
        if (result?.exists !== false && result?.analysis) {
          const a = result.analysis;
          if (a.analysisData) {
            dispatch(setAnalysisData(a));
            dispatch(setDeepAnalysis(a.userStories ? a.userStories : null));
          } else {
            dispatch(setAnalysisData(normalizeAnalysis(a.result ?? a)));
            dispatch(setDeepAnalysis(a.userStories ?? null));
          }
        } else if (result?.analysisData || result?.id) {
          // Direct analysis object returned
          dispatch(setAnalysisData(result.analysisData ? result : normalizeAnalysis(result)));
          dispatch(setDeepAnalysis(result.userStories ?? null));
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error('Failed to load analysis:', error);
        // Clear data on error
        dispatch(setAnalysisData(null));
        dispatch(setDeepAnalysis(null));
      } finally {
        if (!controller.signal.aborted) {
          setIsSwitchingAnalysis(false);
        }
      }
    })();
    // Cleanup: if this effect re-runs (user picks a new analysis) abort
    // the previous fetch's resolve path. The unmount-cleanup effect at the
    // top does the same for component teardown.
    return () => {
      controller.abort();
    };
  }, [selectedAnalysisId, dispatch]);

  // Handle page refresh - fetch consolidated dashboard data for the current project
  useEffect(() => {
    // Prevent duplicate fetches
    if (hasConsolidatedFetchRef.current) return;
    
    const currentProjectId = typeof window !== 'undefined' ? localStorage.getItem('project_id') : null;
    
    if (currentProjectId) {
      hasConsolidatedFetchRef.current = true;
      // Mark latest fetch as satisfied for this project to avoid a subsequent getLatestAnalysis call
      lastFetchedProjectRef.current = currentProjectId;
      // Fetch consolidated dashboard data (analysis + user stories + comments + submission status)
      dispatch(getConsolidatedDashboardData(currentProjectId));
    }
  }, [dispatch]);

  // Handle project selection
  const handleProjectSelect = (projectId: string) => {
    // If external handler is provided (from route-based component), use it
    if (onProjectSelect) {
      onProjectSelect(projectId);
      return;
    }

    // BUG FIX: Clear data synchronously BEFORE updating project ID to prevent flash of stale data
    // Reset all refs first to prevent stale processing
    lastProcessedAnalysisIdRef.current = null;
    lastHistoryProjectRef.current = null;
    lastFetchedProjectRef.current = null;

    // Dispatch all clear actions synchronously
    dispatch(clearAnalysisData());
    dispatch(setLoadedComments(null));
    dispatch(clearCurrentProjectUserStories());
    dispatch(setSelectedAnalysisId(null));
    dispatch(setDeepAnalysis(null));

    // Now update the project ID after data is cleared
    setCurrentProjectId(projectId);
    if (typeof window !== 'undefined') {
      localStorage.setItem('project_id', projectId);
    }

    // Fetch consolidated dashboard data for the selected project
    if (projectId) {
      // Mark latest fetch as satisfied for this project to avoid triggering getLatestAnalysis
      lastFetchedProjectRef.current = projectId;
      dispatch(getConsolidatedDashboardData(projectId));
    }
  };

  // Note: Work items are now generated dynamically and stored in deepAnalysis state
  // No need to load from backend as they're created on-demand

  // Handle deep analysis data when it's included in analysis response
  useEffect(() => {
    if (analysisData && analysisData.deepAnalysis && !deepAnalysis) {
      dispatch(setDeepAnalysis(analysisData.deepAnalysis));
    }
  }, [analysisData, deepAnalysis, dispatch]);

  // Avoids calling this endpoint for brand new projects without uploads
  useEffect(() => {
    const loadCommentsFromBackend = async () => {
      if (!loadedComments && analysisData) {
        const effectiveProjectId =
          currentProjectId ||
          projectId ||
          personalProjectId ||
          '';
        const queryParam = effectiveProjectId
          ? `project_id=${effectiveProjectId}`
          : 'is_personal=true';
        try {
          const response = await apiRequest('get', `/insights/comments/?${queryParam}`, undefined, true);
          if (response.data.success && response.data.data.comments) {
            dispatch(setLoadedComments(response.data.data.comments));
            if (!effectiveProjectId && response.data.data.project_id) {
              setPersonalProjectId(response.data.data.project_id);
            }
          } else {
          }
        } catch (error: any) {
          console.error('❌ Error loading comments from backend:', error);
          console.error('Error details:', {
            status: error?.response?.status || 'Unknown',
            statusText: error?.response?.statusText || 'Unknown',
            data: error?.response?.data || 'No data',
            url: error?.config?.url || 'No URL',
            message: error?.message || 'No message'
          });
        }
      }
    };
    loadCommentsFromBackend();
  }, [loadedComments, projectId, currentProjectId, personalProjectId, analysisData, dispatch]);



  // Set currentProjectId from initialProjectId prop or localStorage when component mounts
  useEffect(() => {
    if (initialProjectId && !currentProjectId) {
      setCurrentProjectId(initialProjectId);
    } else if (projectId && !currentProjectId && !initialProjectId) {
      setCurrentProjectId(projectId);
    }
  }, [projectId, currentProjectId, initialProjectId]);


  function parseDeepAnalysis(value: any): any {
    try {
      if (Array.isArray(value)) {
        const first = value[0];
        return typeof first === 'string' ? JSON.parse(first) : first;
      }
      if (typeof value === 'string') {
        return JSON.parse(value);
      }
      return value;
    } catch {
      return value;
    }
  }

  // When project changes, fetch latest analysis for it
  useEffect(() => {
    if (!currentProjectId) return;
    // Prevent fetching for the same project multiple times
    if (lastFetchedProjectRef.current === currentProjectId) return;
    lastFetchedProjectRef.current = currentProjectId;
    
    (async () => {
      try {
        const result = await dispatch(getLatestAnalysis(currentProjectId)).unwrap();
        if (result?.exists && result?.analysis) {
          const a = result.analysis; // Extract the nested analysis data
          
          // The backend now returns data in the new format (analysisData field)
          // Check if data is already in the correct frontend format
          if (a.analysisData) {
            // Data is already in the new format, use it directly
            dispatch(setAnalysisData(a));
            dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
          } else if (a.result?.overall && a.result?.counts && a.result?.features !== undefined) {
            // Data is nested under result field - normalize it and merge metadata
            const normalized = normalizeAnalysis(a.result);
            // Merge metadata from the analysis object
            if (normalized) {
              normalized.id = a.id || normalized.id;
              normalized.projectId = a.projectId || normalized.projectId;
              normalized.userId = a.userId || normalized.userId;
              normalized.createdAt = a.createdAt || a.analysis_date || normalized.createdAt;
              normalized.analysisType = a.analysis_type || normalized.analysisType;
            }
            dispatch(setAnalysisData(normalized));
            dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
          } else if (a.sentimentsummary && a.counts && a.featureasba !== undefined) {
            // Data is in the old format, normalize it
            dispatch(setAnalysisData(normalizeAnalysis(a)));
            dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
          } else if (a.overall && a.counts && a.features !== undefined) {
            // Fallback: data is in the old format, normalize it
            dispatch(setAnalysisData(normalizeAnalysis(a)));
            dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
          } else if (a.commentAnalysis) {
            // Fallback: use commentAnalysis if available
            const ca = Array.isArray(a.commentAnalysis)
              ? (typeof a.commentAnalysis[0] === 'string' ? JSON.parse(a.commentAnalysis[0]) : a.commentAnalysis[0])
              : a.commentAnalysis;
            dispatch(setAnalysisData(normalizeAnalysis(ca)));
            dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
          } else {
            dispatch(setAnalysisData(null));
            dispatch(setDeepAnalysis(null));
          }
        } else {
          dispatch(setAnalysisData(null));
          dispatch(setDeepAnalysis(null));
        }
      } catch (e) {
        console.error('Error fetching latest analysis:', e);
      }
    })();
  }, [currentProjectId, dispatch]);

  // TODO: Re-enable when filters are fully implemented
  // Fetch filtered analysis when dimension filters change
  // useEffect(() => {
  //   const fetchFilteredData = async () => {
  //     if (!selectedAnalysisId || dimensionFilters.length === 0) {
  //       setFilteredStats(null);
  //       return;
  //     }
  //
  //     try {
  //       const response = await apiRequest(
  //         'post',
  //         '/api/feedback/analysis/filtered/',
  //         {
  //           analysis_id: selectedAnalysisId,
  //           filters: dimensionFilters
  //         },
  //         true
  //       );
  //
  //       if (response.data?.data) {
  //         setFilteredStats(response.data.data);
  //       }
  //     } catch (error) {
  //       console.error('Failed to fetch filtered analysis:', error);
  //       setFilteredStats(null);
  //     }
  //   };
  //
  //   fetchFilteredData();
  // }, [dimensionFilters, selectedAnalysisId]);

  const handleCloudConnect = () => {
    if (typeof window !== 'undefined') {
      window.location.href = '/settings?tab=integrations';
    }
  };

  // Analyze loaded comments using the backend analyze endpoint
  async function handleTopAnalyze() {
    if (!topFile) {
      setTopError('Please select a file first');
      return;
    }
    const validation = validateSelectedFile(topFile);
    if (!validation.isValid) {
      setTopError(validation.error ?? null);
      return;
    }
    const tempId = makeAnalyzingId(String(Date.now()));
    const fileToSubmit = topFile;
    const fileName = fileToSubmit.name;
    const lowerName = fileName.toLowerCase();
    const effectiveProjectId = currentProjectId || personalProjectId || undefined;

    // PDF/DOCX/CSV/JSON/XLSX: server-side extraction with dimension support
    if (lowerName.endsWith('.pdf') || lowerName.endsWith('.docx') ||
        lowerName.endsWith('.csv') || lowerName.endsWith('.xlsx') ||
        lowerName.endsWith('.xls') || lowerName.endsWith('.json')) {
      try {
        setTopError(null);
        dispatch(clearError());
        setTopFile(null);
        dispatch(prependToHistory({
          id: tempId,
          analysis_date: new Date().toISOString(),
          comments_count: 0,
          positive_pct: 0,
          status: 'analyzing',
          file_name: fileName,
        }));
        dispatch(setSelectedAnalysisId(tempId));

        const result = await dispatch(ingestFile({
          file: fileToSubmit,
          projectId: effectiveProjectId,
        })).unwrap();

        await applyAnalysisResult(result, tempId);
      } catch (e: any) {
        dispatch(removeFromHistory(tempId));
        const data = e?.response?.data;
        const message =
          (typeof data?.message === 'string' && data.message) ||
          (typeof data?.error === 'string' && data.error) ||
          (typeof data?.detail === 'string' && data.detail) ||
          (Array.isArray(data?.errors) && data.errors[0]) ||
          e?.message ||
          'File ingestion failed. Please try again.';
        setTopError(message);
      }
      return;
    }

    try {
      setTopError(null);
      dispatch(clearError());

      // CRITICAL FIX: Clear any selected historical analysis before starting new one
      // This prevents the new analysis from replacing the old one you were viewing
      if (selectedAnalysisId && !isAnalyzingPlaceholder(selectedAnalysisId)) {
        // User was viewing a historical analysis, clear it before starting new one
        dispatch(clearAnalysisData());
        dispatch(setDeepAnalysis(null));
      }

      // Load file data first
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('Failed to read file'));
        reader.readAsText(fileToSubmit);
      });

      let comments: string[] = [];
      if (lowerName.endsWith('.json')) {
        try {
          const parsed = JSON.parse(text);
          if (Array.isArray(parsed)) {
            comments = parsed.map(String).filter(Boolean);
          } else if (Array.isArray(parsed.comments)) {
            comments = parsed.comments.map(String).filter(Boolean);
          }
        } catch {
          // ignore, will error below if empty
        }
      } else if (lowerName.endsWith('.txt')) {
        // Strip UTF-8 BOM if present.
        const stripped = text.startsWith('﻿') ? text.slice(1) : text;
        comments = stripped
          .split(/\r\n|\r|\n/)
          .map(line => line.trim())
          .filter(Boolean);
      } else if (lowerName.endsWith('.csv')) {
        // Parse CSV properly handling quoted fields (commas inside quotes)
        const parseCSVLine = (line: string): string[] => {
          const fields: string[] = [];
          let current = '';
          let inQuotes = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (inQuotes) {
              if (ch === '"' && line[i + 1] === '"') {
                current += '"';
                i++; // skip escaped quote
              } else if (ch === '"') {
                inQuotes = false;
              } else {
                current += ch;
              }
            } else {
              if (ch === '"') {
                inQuotes = true;
              } else if (ch === ',') {
                fields.push(current);
                current = '';
              } else {
                current += ch;
              }
            }
          }
          fields.push(current);
          return fields;
        };

        const lines = text.split(/\r?\n/).filter(Boolean);
        if (lines.length > 0) {
          const header = parseCSVLine(lines[0]).map(h => h.trim().toLowerCase());

          // Try multiple column name patterns for feedback text
          const possibleColumns = [
            'feedback_text', 'feedback', 'comment', 'text',
            'review', 'message', 'description', 'content'
          ];

          let commentIdx = -1;
          for (const colName of possibleColumns) {
            commentIdx = header.indexOf(colName);
            if (commentIdx >= 0) break;
          }

          // If no match, find the column with longest average text (likely the feedback column)
          if (commentIdx < 0 && lines.length > 2) {
            const sampleRows = lines.slice(1, Math.min(6, lines.length));
            const avgLengths = header.map((_, idx) => {
              const sum = sampleRows.reduce((acc, line) => {
                const cells = parseCSVLine(line);
                return acc + (cells[idx]?.length || 0);
              }, 0);
              return sum / sampleRows.length;
            });
            commentIdx = avgLengths.indexOf(Math.max(...avgLengths));
          }

          if (commentIdx >= 0) {
            comments = lines.slice(1)
              .map(line => (parseCSVLine(line)[commentIdx] || '').trim())
              .filter(Boolean);
          }
        }
      }
      
      if (!comments.length) {
        setTopError('No comments detected. Ensure JSON has a comments array, CSV has a comment column, or TXT has one comment per line.');
        return;
      }

      dispatch(setLoadedComments(comments));
      setTopFile(null);
      dispatch(prependToHistory({
        id: tempId,
        analysis_date: new Date().toISOString(),
        comments_count: comments.length,
        positive_pct: 0,
        status: 'analyzing',
        file_name: fileName,
      }));
      dispatch(setSelectedAnalysisId(tempId));

      const result = await dispatch(analyzeComments({
        comments,
        projectId: effectiveProjectId,
        fileName,
      })).unwrap();

      await applyAnalysisResult(result, tempId);
    } catch (e: any) {
      dispatch(removeFromHistory(tempId));
      const data = e?.response?.data;
      const message =
        (typeof data?.message === 'string' && data.message) ||
        (typeof data?.error === 'string' && data.error) ||
        (typeof data?.detail === 'string' && data.detail) ||
        (Array.isArray(data?.errors) && data.errors[0]) ||
        e?.message ||
        'Analysis failed. Please try again.';
      setTopError(message);
    }
  }

  // Replaces the optimistic "analyzing..." history entry (`tempId`) with
  // the resolved analysis state.
  async function applyAnalysisResult(result: any, tempId: string) {
    const payload = (result && (result.analysisData || result.sentimentsummary || result.featureasba))
      ? result
      : (result?.data || null);

    if (!payload) {
      dispatch(removeFromHistory(tempId));
      return;
    }

    const resolvedProjectId = payload?.context?.project_id || payload?.projectId;
    const isDraft = payload?.context?.is_draft;
    if (resolvedProjectId) {
      if (isDraft) {
        setPersonalProjectId(resolvedProjectId);
      }
      if (!currentProjectId) {
        setCurrentProjectId(resolvedProjectId);
      }
      if (typeof window !== 'undefined') {
        localStorage.setItem('project_id', resolvedProjectId);
      }
    }

    dispatch(setAnalysisData(payload));

    if ((result as any)?.taskId) {
      dispatch(setTaskIdForEntry({ tempId, taskId: (result as any).taskId }));
    }

    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('usage-updated'));
    }

    if (payload.id) {
      const counts = payload.analysisData?.counts ?? {};
      const total = Number(counts.total ?? 0);
      const positive = Number(counts.positive ?? 0);
      dispatch(replaceInHistory({
        oldId: tempId,
        entry: {
          id: payload.id,
          analysis_date: payload.createdAt || new Date().toISOString(),
          comments_count: total,
          positive_pct: total > 0 ? Math.round((positive / total) * 100) : 0,
          status: 'completed',
          name: payload.name,
        },
      }));

      // Always update if viewing an analyzing placeholder (handles race with other effects)
      if (isAnalyzingPlaceholder(selectedAnalysisId) || selectedAnalysisId === tempId) {
        dispatch(setSelectedAnalysisId(payload.id));
      }
      // Else case removed: toast notification no longer needed per user request
    } else {
      dispatch(removeFromHistory(tempId));
    }

    if (payload.deepAnalysis) {
      dispatch(setDeepAnalysis(payload.deepAnalysis));
    }

    generateWorkItemsFromAnalysis(payload).catch(e => {
      console.error('Background work item generation failed:', e);
    });
  }

  // Generate work items from analysis data
  async function generateWorkItemsFromAnalysis(analysisData: any) {
    try {
      setIsGeneratingUserStories(true);
      
      // Use platform derived from selected project (default to Azure for personal workspaces)
      const currentPlatform = selectedPlatform ?? 'azure';
      
      // Ensure we have comments available
      let commentsToUse = loadedComments;
      const effectiveProjectId = currentProjectId || personalProjectId || '';
      if (!commentsToUse || commentsToUse.length === 0) {
        try {
          const queryParam = effectiveProjectId
            ? `project_id=${effectiveProjectId}`
            : 'is_personal=true';
          const response = await apiRequest('get', `/insights/comments/?${queryParam}`, undefined, true);
          if (response.data.success && response.data.data.comments) {
            commentsToUse = response.data.data.comments;
            dispatch(setLoadedComments(commentsToUse));
            if (!effectiveProjectId && response.data.data.project_id) {
              setPersonalProjectId(response.data.data.project_id);
            }
          }
        } catch (error) {
          console.error('❌ Error loading comments:', error);
        }
      }
      
      if (!commentsToUse || commentsToUse.length === 0) {
        console.error('❌ No comments available for work item generation');
        setIsGeneratingUserStories(false);
        return;
      }
      if (currentPlatform === 'jira') {
        // For Jira, follow the same flow as Azure: general analysis -> work items generation
        
        if (commentsToUse && commentsToUse.length > 0) {
          // Step 1: Get Jira project metadata for better work item generation
          let jiraProjectMetadata = null;
          const selectedJiraProjectId = typeof window !== 'undefined' ? localStorage.getItem('jira_selected_project') : null;
          
          if (selectedJiraProjectId) {
            try {
              jiraProjectMetadata = null;
            } catch (e) {
              console.warn('⚠️ Failed to fetch Jira project metadata, proceeding without it:', e);
            }
          }
          
          // Step 2: Generate work items using the analysis data and Jira metadata
          const workItemsResult = await dispatch(generateUserStories({
            analysisData,
            comments: commentsToUse, // Use the loaded comments
            platform: 'jira',
            processTemplate: 'Agile', // Default for Jira
            projectId: effectiveProjectId || undefined,
            projectMetadata: jiraProjectMetadata
          })).unwrap();

          // Trigger usage badge refresh after work items generation
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new Event('usage-updated'));
          }

          // Fetch the persisted user stories from the backend after successful generation
          if (effectiveProjectId && user?.id) {
            const formattedProjectId = effectiveProjectId.startsWith('project_') ? effectiveProjectId.replace('project_', '') : effectiveProjectId;
            
            // Add a small delay to ensure the backend has saved the data
            scheduleTimeout(() => {
              dispatch(fetchUserStoriesByProject({
                projectId: formattedProjectId,
                userId: user.id || user.user_id
              }));
            }, 1000);
          }
          
          // Set the generated work items in the store
          if (workItemsResult.work_items) {
            
            // Structure the data properly for the UserStories component
            // The UserStoryList expects an array of user stories, so we need to wrap the response
            const structuredData = {
              ...workItemsResult,
              work_items: workItemsResult.work_items,
              work_items_by_feature: workItemsResult.work_items_by_feature,
              summary: workItemsResult.summary
            };
            
            dispatch(setDeepAnalysis(structuredData));
            
            // Also update the userStories state with the proper format
            // The UserStoryList component expects userStories to be an array
            const userStoryFormat = [{
              id: workItemsResult.id,
              type: workItemsResult.type || 'user_story',
              userId: workItemsResult.userId,
              projectId: workItemsResult.projectId,
              platform: workItemsResult.platform,
              work_items: workItemsResult.work_items,
              summary: workItemsResult.summary,
              generated_at: workItemsResult.generated_at,
              success: workItemsResult.success
            }];
            
            // Store in the userStories slice as well for proper display
            // This ensures the UserStoryList component gets the data in the expected format
            
            dispatch(setDeepAnalysis(structuredData));
          } else {
            console.warn('⚠️ No work items in result');
          }
        } else {
        }
      } else {
        // For Azure DevOps, use the existing logic
        const processTemplate = (typeof window !== 'undefined') ? 
          localStorage.getItem('azure_process_template') || 'Agile' : 'Agile';
        
        
        // Check if we have comments and analysis data available
        if (commentsToUse && commentsToUse.length > 0 && analysisData) {
          
          // Use existing analysis data instead of calling analyzeComments again
          
          // Generate work items from the existing analysis data
          const workItemsResult = await dispatch(generateUserStories({
            analysisData: analysisData,
            comments: commentsToUse,
            platform: (currentPlatform as 'azure' | 'jira') ?? 'azure',
            processTemplate,
            projectId: effectiveProjectId || undefined
          })).unwrap();

          // Trigger usage badge refresh after work items generation
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new Event('usage-updated'));
          }

          if (workItemsResult?.work_items && workItemsResult.work_items.length > 0) {
            // Structure the data properly for the UserStories component
            const structuredData = {
              ...workItemsResult,
              work_items: workItemsResult.work_items,
              work_items_by_feature: workItemsResult.work_items_by_feature,
              summary: workItemsResult.summary
            };
            
            dispatch(setDeepAnalysis(structuredData));
            
            // Fetch the persisted user stories from the backend after successful generation
            if (effectiveProjectId && user?.id) {
              const formattedProjectId = effectiveProjectId.startsWith('project_') ? effectiveProjectId.replace('project_', '') : effectiveProjectId;
              
              // Add a small delay to ensure the backend has saved the data
              scheduleTimeout(() => {
                dispatch(fetchUserStoriesByProject({
                  projectId: formattedProjectId,
                  userId: user.id || user.user_id
                }));
              }, 1000);
            }
          } else {
            console.warn('No work items generated from analysis');
          }
        } else {
          
          // Fallback to old method using analysis data
          const workItemsResult = await dispatch(generateUserStories({
            analysisData,
            comments: commentsToUse,
            platform: (currentPlatform as 'azure' | 'jira') ?? 'azure',
            processTemplate,
            projectId: effectiveProjectId || undefined
          })).unwrap();

          // Trigger usage badge refresh after work items generation
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new Event('usage-updated'));
          }

          // Set the generated work items in the store
          if (workItemsResult.work_items) {
            // Structure the data properly for the UserStories component
            const structuredData = {
              ...workItemsResult,
              work_items: workItemsResult.work_items,
              work_items_by_feature: workItemsResult.work_items_by_feature,
              summary: workItemsResult.summary
            };
            dispatch(setDeepAnalysis(structuredData));
            
            // Fetch the persisted user stories from the backend after fallback generation
            if (effectiveProjectId && user?.id) {
              const formattedProjectId = effectiveProjectId.startsWith('project_') ? effectiveProjectId.replace('project_', '') : effectiveProjectId;
              
              // Add a small delay to ensure the backend has saved the data
              scheduleTimeout(() => {
                dispatch(fetchUserStoriesByProject({
                  projectId: formattedProjectId,
                  userId: user.id || user.user_id
                }));
              }, 1000);
            }
          }
        }
      }
      
    } catch (e: any) {
      console.error('❌ Error generating work items:', e);
      
      // Show error to user for better debugging
      const errorMessage = typeof e === 'string' ? e : e?.message || 'Unknown error occurred';
      console.error('❌ Work item generation failed:', errorMessage);
      
      // You can uncomment this to show errors to users:
      // alert(`Work item generation failed: ${errorMessage}`);
    } finally {
      setIsGeneratingUserStories(false);
      
      // Fetch the persisted user stories from the backend after generation
      const effectiveProjectId = currentProjectId || personalProjectId;
      if (effectiveProjectId && user?.id) {
        const formattedProjectId = effectiveProjectId.startsWith('project_') ? effectiveProjectId.replace('project_', '') : effectiveProjectId;
        
        // Add a small delay to ensure the backend has saved the data
        scheduleTimeout(() => {
          dispatch(fetchUserStoriesByProject({
            projectId: formattedProjectId,
            userId: user.id || user.user_id
          }));
        }, 1000);
      }
    }
  }

  // Normalize backend keys to frontend shape
  function normalizeAnalysis(input: any): AnalysisData {
    if (!input) return input as AnalysisData;
    
    
    // If data is already in the new format (has analysisData field)
    if (input.analysisData) {
      const toNum = (v: any) => (typeof v === 'number' ? v : Number(v ?? 0));
      
      const normalized = {
        id: input.id || `analysis_${Date.now()}`,
        projectId: input.projectId || 'unknown',
        userId: input.userId || 'anonymous',
        createdAt: input.createdAt || new Date().toISOString(),
        analysisType: input.analysisType || 'commentSentiment',
        rawLlm: input.rawLlm || {},
        analysisData: {
          overall: {
            positive: toNum(input.analysisData.overall?.positive),
            negative: toNum(input.analysisData.overall?.negative),
            neutral: toNum(input.analysisData.overall?.neutral),
          },
          counts: {
            total: toNum(input.analysisData.counts?.total),
            positive: toNum(input.analysisData.counts?.positive),
            negative: toNum(input.analysisData.counts?.negative),
            neutral: toNum(input.analysisData.counts?.neutral),
          },
          features: (input.analysisData.features || []).map((f: any) => ({
            featureId: f.featureId || f.id || f.name,
            name: f.name || f.feature,
            description: f.description || '',
            sentiment: {
              positive: toNum(f.sentiment?.positive),
              negative: toNum(f.sentiment?.negative),
              neutral: toNum(f.sentiment?.neutral),
            },
            keywords: f.keywords || [],
            comment_count: toNum(f.comment_count),
          })),
          positive_keywords: input.analysisData.positive_keywords || [],
          negative_keywords: input.analysisData.negative_keywords || [],
        },
        deepAnalysis: input.deepAnalysis,
        // Cache-priming fields for the user-story-creation endpoint. Without
        // these, devops_service re-runs the GPT narration even though celery
        // already produced it. Tolerate either nesting (top-level vs inner).
        narration: input.narration ?? input.analysisData.narration ?? null,
        work_item_candidates:
          input.work_item_candidates ?? input.analysisData.work_item_candidates ?? null,
      } as AnalysisData;

      return normalized;
    }

    // If data is in the old format (has overall, counts, features at top level)
    if (input.overall && input.counts && input.features !== undefined) {
      const toNum = (v: any) => (typeof v === 'number' ? v : Number(v ?? 0));
      
      const normalized = {
        id: `analysis_${Date.now()}`,
        projectId: 'unknown',
        userId: 'anonymous',
        createdAt: new Date().toISOString(),
        analysisType: 'commentSentiment',
        rawLlm: input.raw_llm || {},
        analysisData: {
          overall: {
            positive: toNum(input.overall.positive),
            negative: toNum(input.overall.negative),
            neutral: toNum(input.overall.neutral),
          },
          counts: {
            total: toNum(input.counts.total),
            positive: toNum(input.counts.positive),
            negative: toNum(input.counts.negative),
            neutral: toNum(input.counts.neutral || 0),
          },
          features: (input.features || []).map((f: any) => ({
            featureId: f.featureId || f.id || f.name,
            name: f.name || f.feature,
            description: f.description || '',
            sentiment: {
              positive: toNum(f.sentiment?.positive),
              negative: toNum(f.sentiment?.negative),
              neutral: toNum(f.sentiment?.neutral),
            },
            keywords: f.keywords || [],
            comment_count: toNum(f.comment_count),
          })),
          positive_keywords: input.positive_keywords || [],
          negative_keywords: input.negative_keywords || [],
        },
        deepAnalysis: input.deepAnalysis,
        // Cache-priming fields for the user-story-creation endpoint (see other branch).
        narration: input.narration ?? null,
        work_item_candidates: input.work_item_candidates ?? null,
      } as AnalysisData;

      return normalized;
    }
    
    // Handle old format or commentAnalysis format
    const toNum = (v: any) => (typeof v === 'number' ? v : Number(v ?? 0));
    const sentiments = input.sentimentsummary || input.sentiment_summary || input.overall || {};
    const features = input.featureasba || input.feature_asba || input.features || [];
    const negatives = input.negativesummary || input.negative_summary || [];
    const emojis = input.emojianalysis || input.emoji_analysis || undefined;
    const posKeys = input.positivekeywords || input.positive_keywords || [];
    const negKeys = input.negativekeywords || input.negative_keywords || [];
    const counts = input.counts || input.count || { total: 0, positive: 0, negative: 0 };
    
    // Extract deepAnalysis from raw_llm.deep_chunks if available
    let deepAnalysis = null;
    if (input.raw_llm?.deep_chunks && input.raw_llm.deep_chunks.length > 0) {
      try {
        const deepChunk = input.raw_llm.deep_chunks[0];
        if (typeof deepChunk === 'string') {
          deepAnalysis = JSON.parse(deepChunk);
        } else {
          deepAnalysis = deepChunk;
        }
      } catch (e) {
        console.error('Error parsing deep analysis:', e);
      }
    }
    
    return {
      id: `analysis_${Date.now()}`,
      projectId: 'unknown',
      userId: 'anonymous',
      createdAt: new Date().toISOString(),
      analysisType: 'commentSentiment',
      rawLlm: input.raw_llm || {},
      analysisData: {
        overall: {
          positive: toNum(sentiments.positive),
          negative: toNum(sentiments.negative),
          neutral: toNum(sentiments.neutral),
        },
        counts: {
          total: toNum(counts.total),
          positive: toNum(counts.positive),
          negative: toNum(counts.negative),
          neutral: toNum(counts.neutral || 0),
        },
        features: (features || []).map((f: any) => ({
          featureId: f.featureId || f.id || f.name,
          name: f.feature || f.name,
          description: f.description || '',
          sentiment: {
            positive: toNum(f.sentiment?.positive ?? f.sentiment_positive),
            negative: toNum(f.sentiment?.negative ?? f.sentiment_negative),
            neutral: toNum(f.sentiment?.neutral ?? f.sentiment_neutral),
          },
          keywords: f.keywords || [],
          comment_count: toNum(f.comment_count),
        })),
        positive_keywords: posKeys,
        negative_keywords: negKeys,
      },
      deepAnalysis: deepAnalysis,
    } as AnalysisData;
  }

  // Prepare chart data
  const sentimentData = [
    { name: 'Positive', value: activeAnalysisData?.analysisData?.overall?.positive ?? 0 },
    { name: 'Negative', value: activeAnalysisData?.analysisData?.overall?.negative ?? 0 },
    { name: 'Neutral', value: activeAnalysisData?.analysisData?.overall?.neutral ?? 0 }
  ];

  const featureSentimentData = (activeAnalysisData?.analysisData?.features || []).map((feature: any) => ({
    name: feature.name || feature.feature,
    positive: feature.sentiment.positive,
    negative: feature.sentiment.negative,
    neutral: feature.sentiment.neutral,
    description: feature.description || '',
    keywords: feature.keywords || [],
    comment_count: feature.comment_count
  }));


  const metrics = [
    {
      title: "Total Comments",
      value: String(activeAnalysisData?.analysisData?.counts?.total ?? 0),
      color: "blue" as const,
      description: "Comments analyzed in current file"
    },
    {
      title: "Positive Comments",
      value: String(activeAnalysisData?.analysisData?.counts?.positive ?? 0),
      color: "green" as const,
      description: "Comments with positive sentiment"
    },
    {
      title: "Negative Comments",
      value: String(activeAnalysisData?.analysisData?.counts?.negative ?? 0),
      color: "red" as const,
      description: "Comments with negative sentiment"
    }
  ];

  const selectedEntry = analysisHistory.find(e => e.id === selectedAnalysisId);

  const handleRunSelect = (id: string) => {
    // Clear current data to prevent stale data showing
    if (selectedAnalysisId !== id) {
      setIsSwitchingAnalysis(true);
      // Reset processing ref to allow fresh load
      lastProcessedAnalysisIdRef.current = null;
    }
    dispatch(setSelectedAnalysisId(id));
  };

  const handleRunRename = async (id: string, name: string) => {
    try {
      await dispatch(renameAnalysisRun({ id, name })).unwrap();
    } catch (err: any) {
      console.error('Failed to rename analysis run:', err);
      alert(typeof err === 'string' ? err : 'Failed to rename analysis run.');
    }
  };

  const handleRunDelete = async (id: string) => {
    try {
      await dispatch(deleteAnalysisRun(id)).unwrap();
    } catch (err: any) {
      console.error('Failed to delete analysis:', err);
      alert(typeof err === 'string' ? err : 'Failed to delete analysis.');
    }
  };

  const handleCancelTask = async (tempId: string, taskId: string) => {
    try {
      await dispatch(cancelAnalysisTask({ taskId, tempId })).unwrap();
    } catch (err: any) {
      console.error('Failed to cancel task:', err);
    }
  };

  // Show loader while:
  // - projects are still loading (initial load only)
  // Note: We don't show full-screen loader for analysis loading anymore
  // Analysis loading is handled within the dashboard section
  if (projectsLoading && projects.length === 0) {
    return (
      <div className="h-full overflow-hidden bg-secondary/40 dark:bg-background">
        {/* Main Content */}
        <main className="p-6">
          <div className="max-w-7xl mx-auto space-y-6">
            {/* Navigation */}
            <div className="flex items-center justify-between">
              {/* Navigation Tabs - Inlined */}
              <div className="flex bg-secondary/60 rounded-xl p-1">
                <button
                  onClick={() => setActiveView('dashboard')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    activeView === 'dashboard' 
                      ? 'bg-background/90 text-foreground shadow-sm' 
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Dashboard
                </button>
                <button
                  onClick={() => setActiveView('user-stories')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    activeView === 'user-stories' 
                      ? 'bg-background/90 text-foreground shadow-sm' 
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  User Stories
                </button>
              </div>
            </div>

            {activeView === 'dashboard' ? (
              <>
                {/* Upload Panel */}
                <UploadPanel
                  dbProjectId={currentProjectId}
                  topFile={topFile}
                  topError={error || topError}
                  loadedComments={loadedComments}
                  topUploading={isAnalyzing}
                  integrationsLoading={integrationsLoading}
                  slackConnected={!!slackAccount}
                  slackDisplayName={slackDisplayName}
                  onFileSelect={setTopFile}
                  onAnalyze={handleTopAnalyze}
                  onCloudConnect={handleCloudConnect}
                  isAnalyzing={isAnalyzing}
                />
                {slackAccount && currentProjectId && (
                  <SlackChannelPanel
                    projectId={currentProjectId}
                    slackAccountId={slackAccount.id}
                    slackDisplayName={slackDisplayName}
                  />
                )}
              </>
            ) : activeView === 'user-stories' ? (
              /* User Stories View Loading */
              <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
                <div className="animate-pulse">
                  <div className="h-6 bg-muted rounded w-1/4 mb-4"></div>
                  <div className="space-y-3">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="h-16 bg-muted rounded"></div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              /* Jira Integration View Loading */
              <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
                <div className="animate-pulse">
                  <div className="h-6 bg-muted rounded w-1/4 mb-4"></div>
                  <div className="space-y-3">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="h-16 bg-muted rounded"></div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden bg-secondary/40 dark:bg-background">
      {/* Tabs + Panels in one seamless column */}
      <div className="w-full flex flex-col flex-1 min-h-0">
        {/* Two-Panel Layout */}
        <div className="flex gap-6 items-stretch flex-1 min-h-0">
          {/* Left Panel - Tasks */}
          <AnalysisRunList
            entries={analysisHistory}
            selectedId={selectedAnalysisId}
            isLoading={isTaskListLoading}
            onSelect={handleRunSelect}
            onRename={handleRunRename}
            onDelete={handleRunDelete}
            onCancel={handleCancelTask}
            projectName={selectedProjectName}
          />

          {/* Right Panel - Upload + Task Details */}
          <main className="flex-1 min-w-0 space-y-6 overflow-y-auto pr-2 scrollbar-thin">
            <div className="w-full">
              <UploadPanel
                dbProjectId={currentProjectId}
                topFile={topFile}
                topError={error || topError}
                loadedComments={loadedComments}
                topUploading={isAnalyzing}
                integrationsLoading={integrationsLoading}
                slackConnected={!!slackAccount}
                slackDisplayName={slackDisplayName}
                onFileSelect={setTopFile}
                onAnalyze={handleTopAnalyze}
                onCloudConnect={handleCloudConnect}
                isAnalyzing={isAnalyzing}
              />
              {slackAccount && currentProjectId && (
                <SlackChannelPanel
                  projectId={currentProjectId}
                  slackAccountId={slackAccount.id}
                  slackDisplayName={slackDisplayName}
                />
              )}
            </div>

            <>
              {/* Dismissible error banner above results */}
              {(error || topError) && (
                <div className="flex items-center justify-between gap-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl">
                  <p className="text-sm text-red-700 dark:text-red-300 flex-1">
                    {error || topError}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setTopError(null);
                      dispatch(clearError());
                    }}
                    className="shrink-0 p-2 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg transition-colors"
                    aria-label="Dismiss error"
                  >
                    <span className="text-lg leading-none">×</span>
                  </button>
                </div>
              )}

              <div id="analysis-results-section" className="space-y-6">
              {/* Analysis Results Section — only show loader when the selected run is the one being analyzed */}
              {isAnalyzingPlaceholder(selectedAnalysisId) && (
                <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin text-amber-500" />
                      <span className="text-sm font-medium text-foreground">
                        {selectedEntry?.file_name || 'Analyzing feedback...'}
                      </span>
                    </div>
                    <span className="text-xs text-amber-600 dark:text-amber-400">In Progress</span>
                  </div>
                  {selectedEntry && selectedEntry.comments_count > 0 && (
                    <p className="text-xs text-muted-foreground mb-3">{selectedEntry.comments_count} comments</p>
                  )}
                </div>
              )}
              {analysisProgressUi && (
                <div className="rounded-xl border border-border/60 bg-card/80 p-3 transition-all duration-300">
                  <div className="mb-2 flex items-center justify-between text-xs">
                    <span className="font-medium text-foreground">Analysis Progress</span>
                    <span className={`${analysisProgressUi.text} transition-colors duration-300`}>
                      {analysisProgressUi.label}
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-4 items-start gap-2">
                    {analysisProgressSteps.map((step, idx) => (
                      <div key={step.label} className="relative flex flex-col items-center text-center">
                        {idx < analysisProgressSteps.length - 1 && (
                          <div
                            className={`absolute left-[calc(50%+12px)] top-[10px] h-[2px] w-[calc(100%-24px)] transition-colors duration-300 ${
                              step.status === 'success'
                                ? 'bg-orange-500/60'
                                : 'bg-border/70'
                            }`}
                          />
                        )}
                        <div
                          className={`z-10 h-5 w-5 rounded-full border transition-all duration-300 ${
                            step.status === 'success'
                              ? 'border-orange-500/60 bg-orange-500/80'
                              : step.status === 'running'
                              ? 'border-orange-400/60 bg-orange-400/80'
                              : step.status === 'error'
                              ? 'border-red-700/60 bg-red-700/80'
                              : 'border-border/70 bg-background'
                          } flex items-center justify-center`}
                        >
                          {step.status === 'success' && <Check className="h-3 w-3 text-white" />}
                          {step.status === 'running' && <Loader2 className="h-3 w-3 animate-spin text-white" />}
                        </div>
                        <span
                          className={`mt-1 text-[10px] font-medium transition-colors duration-300 ${
                            step.status === 'success'
                              ? 'text-orange-600 dark:text-orange-400'
                              : step.status === 'running'
                              ? 'text-orange-500 dark:text-orange-400'
                              : step.status === 'error'
                              ? 'text-red-700 dark:text-red-400'
                              : 'text-muted-foreground'
                          }`}
                        >
                          {step.label}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex w-full min-w-0 justify-end py-1">
                <div
                  role="tablist"
                  aria-label="Analysis results"
                  className="flex w-fit shrink-0 rounded-xl bg-secondary/60 p-1"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={resultsTab === 'insights'}
                    id="tab-insights"
                    aria-controls="panel-insights"
                    onClick={() => setResultsTab('insights')}
                    className={`rounded-lg px-4 py-2 text-sm font-medium transition-all duration-200 ${
                      resultsTab === 'insights'
                        ? 'bg-background/90 text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Insights
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={resultsTab === 'workitems'}
                    id="tab-workitems"
                    aria-controls="panel-workitems"
                    onClick={() => setResultsTab('workitems')}
                    className={`rounded-lg px-4 py-2 text-sm font-medium transition-all duration-200 ${
                      resultsTab === 'workitems'
                        ? 'bg-background/90 text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Work items
                  </button>
                </div>
              </div>

              {resultsTab === 'insights' && (
                <InsightsPanel
                  isSwitchingAnalysis={isSwitchingAnalysis}
                  isTaskViewLoading={isTaskViewLoading}
                  analysisProgressUi={analysisProgressUi}
                  hasAnalysisResults={hasAnalysisResults}
                  isAnalyzing={isAnalyzing}
                  selectedAnalysisId={selectedAnalysisId}
                  metrics={metrics}
                  transformedFeatures={transformedFeatures}
                  selectedFeatures={selectedFeatures}
                  setSelectedFeatures={setSelectedFeatures}
                  handleRegenerateAnalysis={handleRegenerateAnalysis}
                  editedKeywords={editedKeywords}
                  loadedComments={loadedComments}
                  currentProjectId={currentProjectId}
                  latestAnalysis={latestAnalysis}
                  featureSentimentData={featureSentimentData}
                  sentimentData={sentimentData}
                  wordCloudView={wordCloudView}
                  activeAnalysisData={activeAnalysisData}
                />
              )}
              {resultsTab === 'workitems' && (
                <WorkItemsPanel
                  workItemsPanelLoading={workItemsPanelLoading}
                  isGeneratingUserStories={isGeneratingUserStories}
                  userStoriesLoading={userStoriesLoading}
                  isTaskViewLoading={isTaskViewLoading}
                  loading={loading}
                  selectedPlatform={selectedPlatform}
                  currentProjectId={currentProjectId}
                  personalProjectId={personalProjectId}
                  projectId={projectId}
                  user={user}
                  loadedComments={loadedComments}
                  deepAnalysis={deepAnalysis}
                  currentProjectUserStories={currentProjectUserStories}
                  analysisData={analysisData}
                  dispatch={dispatch}
                />
              )}
              </div>
            </>

          </main>
        </div>
      </div>
    </div>
  );
}



