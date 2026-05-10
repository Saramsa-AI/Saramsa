'use client';

import { useEffect, useState, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
import { encryptProjectId } from '@/lib/encryption';
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
} from '../../../store/features/analysis/analysisSlice';
import type { AnalysisHistoryEntry } from '../../../store/features/analysis/analysisSlice';
import { fetchProjects } from '../../../store/features/projects/projectsSlice';
import { fetchIntegrationAccounts } from '../../../store/features/integrations/integrationsSlice';
import { 
  clearCurrentProjectUserStories,
  setCurrentProjectUserStories,
  fetchUserStoriesByProject
} from '../../../store/features/userStories/userStoriesSlice';


import type { AnalysisData } from '@/types/analysis';
import { apiRequest } from '@/lib/apiRequest';
import { Check, Loader2, Sparkles, AlertCircle, CheckCircle } from 'lucide-react';
import { toast } from 'sonner';
import { UploadPanel } from './UploadPanel';
import { SlackChannelPanel } from './SlackChannelPanel';
import { MetricsCards } from './MetricsCards';
import { FeatureSentimentsTable } from '../../dashboard/analysisDashboard/FeatureSentimentsTable';
import { SentimentCharts } from '../../dashboard/analysisDashboard/SentimentCharts';
import { KeywordCloud } from './KeywordCloud';
import { AdvancedWordCloud } from './AdvancedWordCloud';
// import { NavigationTabs } from './NavigationTabs'; // Inlined below
import { UserStoryList } from '../userStoryList';

import { AnalysisRunList } from './AnalysisRunList';

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

  const projectId = typeof window !== 'undefined' ? localStorage.getItem('project_id') : null;
  const selectedProjectName = projects?.find((p: any) => p.id === (currentProjectId || projectId))?.name;
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
      !!selectedAnalysisId?.startsWith('analyzing_'),
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
    // Only show progress bar when actively analyzing or generating work items
    const isCurrentlyAnalyzing = isAnalyzing || analysisStatus === 'pending' || analysisStatus === 'processing';
    const isGeneratingItems = isGeneratingUserStories;

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
        if (isGeneratingUserStories) {
          return { label: 'Generating Work Items', width: 'w-3/4', tone: 'bg-orange-600/80', text: 'text-orange-600 dark:text-orange-400' };
        }
        // Only show completion status briefly after analysis finishes, not for historical views
        if (isCurrentlyAnalyzing) {
          return { label: 'Completed', width: 'w-full', tone: 'bg-saramsa-brand/80', text: 'text-saramsa-brand' };
        }
        return null;
      case 'failure':
        return { label: 'Failed', width: 'w-full', tone: 'bg-red-700/80', text: 'text-red-700 dark:text-red-400' };
      default:
        return null;
    }
  }, [analysisStatus, isGeneratingUserStories, isAnalyzing]);

  const analysisProgressSteps = useMemo(() => {
    const base = [
      { label: 'Ingestion', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Processing', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Synthesis', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
      { label: 'Work Items', status: 'idle' as 'idle' | 'running' | 'success' | 'error' },
    ];

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
  }, [analysisStatus, hasGeneratedWorkItems, isGeneratingUserStories, isAnalyzing]);


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
  
  // Log when analysis data changes
  useEffect(() => {
  }, [analysisData, deepAnalysis, loading, error, isAnalyzing, loadedComments]);

  // Process latestAnalysis from getConsolidatedDashboardData and set analysisData
  useEffect(() => {
    if (!latestAnalysis) {
      return;
    }

    const analysisId = latestAnalysis.analysis?.id;

    // CRITICAL: If user is viewing a historical analysis and a new analysis completes,
    // show notification but don't overwrite their current view
    if (selectedAnalysisId && !selectedAnalysisId.startsWith('analyzing_')) {
      // Check if this is a newly completed analysis we haven't notified about
      if (analysisId && lastProcessedAnalysisIdRef.current !== analysisId && latestAnalysis.exists) {
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

        // Show notification with brand color
        toast('Analysis Complete!', {
          description: `New analysis with ${total} comments is ready to view.`,
          icon: <CheckCircle className="w-5 h-5 text-saramsa-brand" />,
          action: {
            label: 'View Now',
            onClick: () => {
              dispatch(setSelectedAnalysisId(analysisId));
            }
          },
          duration: 10000, // Show for 10 seconds
        });

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
      // The backend now returns data in the new format (analysisData field)
      // Check if data is already in the correct frontend format
      if (a.analysisData) {
        // Data is already in the new format, use it directly
        dispatch(setAnalysisData(a));
        dispatch(setDeepAnalysis(a.userStories ? parseDeepAnalysis(a.userStories) : null));
        lastProcessedAnalysisIdRef.current = a.id;
        // Select this run in the sidebar and ensure it exists in history
        if (a.id) {
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
    if (selectedAnalysisId && !selectedAnalysisId.startsWith('analyzing_')) {
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
    if (selectedAnalysisId.startsWith('analyzing_')) {
      setIsSwitchingAnalysis(false);
      return;
    }
    // If the currently loaded analysis already matches, skip fetch
    if (analysisData && (analysisData as any).id === selectedAnalysisId) {
      setIsSwitchingAnalysis(false);
      return;
    }

    // Set switching state to show loading UI
    setIsSwitchingAnalysis(true);

    // Clear previous analysis data to prevent showing stale work items
    dispatch(clearCurrentProjectUserStories());
    dispatch(setDeepAnalysis(null));

    (async () => {
      try {
        const result = await dispatch(fetchAnalysisById(selectedAnalysisId)).unwrap();
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
        console.error('Failed to load analysis:', error);
        // Clear data on error
        dispatch(setAnalysisData(null));
        dispatch(setDeepAnalysis(null));
      } finally {
        setIsSwitchingAnalysis(false);
      }
    })();
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
    
    // Otherwise, use the original logic for backward compatibility
    setCurrentProjectId(projectId);
    if (typeof window !== 'undefined') {
      localStorage.setItem('project_id', projectId);
    }
    
    // Clear current analysis data when switching projects
    dispatch(clearAnalysisData());
    dispatch(setLoadedComments(null));
    dispatch(clearCurrentProjectUserStories());
    dispatch(setSelectedAnalysisId(null));

    // Reset the processed analysis ID ref when switching projects
    lastProcessedAnalysisIdRef.current = null;
    lastHistoryProjectRef.current = null;
    
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

  // Debug: Monitor deepAnalysis state changes
  useEffect(() => {
  }, [deepAnalysis]);


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
    const tempId = `analyzing_${Date.now()}`;
    const fileToSubmit = topFile;
    const fileName = fileToSubmit.name;
    const lowerName = fileName.toLowerCase();
    const effectiveProjectId = currentProjectId || personalProjectId || undefined;

    // PDF/DOCX: server-side extraction keeps the JS bundle light.
    if (lowerName.endsWith('.pdf') || lowerName.endsWith('.docx')) {
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
      if (selectedAnalysisId && !selectedAnalysisId.startsWith('analyzing_')) {
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
          const commentIdx = header.indexOf('comment');
          if (commentIdx >= 0) {
            comments = lines.slice(1)
              .map(line => (parseCSVLine(line)[commentIdx] || '').trim())
              .filter(Boolean);
          } else {
            comments = lines.slice(1).map(line => (parseCSVLine(line)[0] || '').trim()).filter(Boolean);
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

      if (selectedAnalysisId === tempId) {
        dispatch(setSelectedAnalysisId(payload.id));
      } else {
        toast('Analysis Complete!', {
          description: `Analysis with ${total} comments completed successfully.`,
          icon: <CheckCircle className="w-5 h-5 text-saramsa-brand" />,
          action: {
            label: 'View Results',
            onClick: () => {
              dispatch(setSelectedAnalysisId(payload.id));
            }
          },
          duration: 10000,
        });
      }
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
            setTimeout(() => {
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
              setTimeout(() => {
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
              setTimeout(() => {
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
        setTimeout(() => {
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
              <div
                id="panel-insights"
                role="tabpanel"
                aria-labelledby="tab-insights"
                className={`space-y-6 transition-opacity duration-300 ${isSwitchingAnalysis ? 'opacity-50' : 'opacity-100'}`}
              >
              {isTaskViewLoading ? (
                <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-semibold text-foreground">
                        {isSwitchingAnalysis ? 'Loading analysis...' : 'Preparing fresh analysis'}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">
                        {isSwitchingAnalysis
                          ? 'Fetching selected analysis data and rebuilding visualizations.'
                          : 'Fetching latest run data and rebuilding charts.'}
                      </p>
                    </div>
                    <span className="inline-flex items-center rounded-full border border-orange-400/30 bg-orange-500/10 px-3 py-1 text-xs font-medium text-orange-600 dark:text-orange-400">
                      {analysisProgressUi?.label || (isSwitchingAnalysis ? 'Loading' : 'Processing')}
                    </span>
                  </div>
                  <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
                    <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
                    <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
                    <div className="h-20 rounded-xl border border-border/60 bg-secondary/40 animate-pulse" />
                  </div>
                  <div className="mt-4 space-y-3">
                    <div className="h-4 w-40 rounded bg-secondary/50 animate-pulse" />
                    <div className="h-36 rounded-xl border border-border/60 bg-secondary/30 animate-pulse" />
                  </div>
                </div>
              ) : !hasAnalysisResults && !isAnalyzing && selectedAnalysisId ? (
                /* Empty state when no analysis data is available for the selected analysis */
                <div className="bg-card/80 rounded-2xl border border-border/60 p-8 text-center">
                  <AlertCircle className="w-12 h-12 text-muted-foreground/50 mx-auto mb-4" />
                  <h3 className="text-lg font-semibold text-foreground mb-2">
                    No Analysis Data Available
                  </h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    The selected analysis could not be loaded or contains no data.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Try selecting a different analysis or upload new feedback data.
                  </p>
                </div>
              ) : (
                <>
                  {/* Metrics Cards */}
                  {hasAnalysisResults && <MetricsCards metrics={metrics} />}

                  {/* Feature Sentiments Table */}
                  {hasAnalysisResults && (
                    <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
                        <FeatureSentimentsTable
                          features={transformedFeatures}
                          selectedFeatures={selectedFeatures}
                          onFeatureToggle={(featureName) => {
                            setSelectedFeatures(prev =>
                              prev.includes(featureName)
                                ? prev.filter(name => name !== featureName)
                                : [...prev, featureName]
                            );
                          }}
                          onRegenerateAnalysis={handleRegenerateAnalysis}
                          hasEditedFeaturesProp={Object.keys(editedKeywords).length > 0}
                          hasComments={!!loadedComments && loadedComments.length > 0}
                        />
                    </div>
                  )}
                </>
              )}

              {hasAnalysisResults && !isTaskViewLoading && (
              <SentimentCharts
                featureSentimentData={featureSentimentData}
                sentimentData={sentimentData}
                selectedFeatures={selectedFeatures}
              />
              )}

              {hasAnalysisResults && !isTaskViewLoading && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-foreground">
                    Word Cloud Analysis
                  </h3>
                </div>

                {wordCloudView === 'split' ? (
                  <KeywordCloud
                    positiveKeywords={
                      activeAnalysisData?.analysisData?.positive_keywords?.map((word: any) =>
                        typeof word === 'string' ? word : word.keyword || word.text || String(word)
                      ) || []
                    }
                    negativeKeywords={
                      activeAnalysisData?.analysisData?.negative_keywords?.map((word: any) =>
                        typeof word === 'string' ? word : word.keyword || word.text || String(word)
                      ) || []
                    }
                  />
                ) : (
                  <AdvancedWordCloud
                    positiveKeywords={
                      activeAnalysisData?.analysisData?.positive_keywords?.map((word: any) =>
                        typeof word === 'string' ? word : word.keyword || word.text || String(word)
                      ) || []
                    }
                    negativeKeywords={
                      activeAnalysisData?.analysisData?.negative_keywords?.map((word: any) =>
                        typeof word === 'string' ? word : word.keyword || word.text || String(word)
                      ) || []
                    }
                  />
                )}
              </div>
              )}

              {hasAnalysisResults && !isTaskViewLoading && (
              <div className="text-xs text-muted-foreground/70 text-right">
                Analysis from {(() => {
                  const analysisDate = activeAnalysisData?.createdAt;
                  const deepAnalysisDate = activeAnalysisData?.deepAnalysis?.generated_at;
                  const timestamp = deepAnalysisDate || analysisDate;
                  if (timestamp) {
                    return new Date(timestamp).toLocaleDateString('en-US', {
                      year: 'numeric', month: 'long', day: 'numeric',
                      hour: '2-digit', minute: '2-digit'
                    });
                  }
                  return new Date().toLocaleDateString();
                })()}
              </div>
              )}
              </div>
              )}

              {resultsTab === 'workitems' && (
              <div
                id="panel-workitems"
                role="tabpanel"
                aria-labelledby="tab-workitems"
                className={`space-y-6 transition-opacity duration-300 ${workItemsPanelLoading ? 'opacity-50' : 'opacity-100'}`}
              >
            {workItemsPanelLoading ? (
            <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <Loader2 className="h-5 w-5 shrink-0 animate-spin text-saramsa-brand" aria-hidden />
                  <div>
                    <p className="text-sm font-semibold text-foreground">Loading work items</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {isGeneratingUserStories
                        ? 'Generating stories from your analysis…'
                        : userStoriesLoading
                          ? 'Fetching work items for this project…'
                          : 'Refreshing analysis and backlog…'}
                    </p>
                  </div>
                </div>
              </div>
              <div className="mt-5 space-y-3">
                {[1, 2, 3, 4].map((i) => (
                  <div
                    key={i}
                    className="h-14 rounded-xl border border-border/60 bg-secondary/40 animate-pulse"
                  />
                ))}
              </div>
            </div>
            ) : (
            <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
              {selectedPlatform === 'jira' ? (
                /* Jira User Stories View */
                (() => {
                  return loadedComments && loadedComments.length > 0;
                })() ? (
                  (() => {
                    // Check if we have work items in deepAnalysis OR in currentProjectUserStories
                    const hasDeepAnalysisWorkItems = deepAnalysis?.work_items && deepAnalysis.work_items.length > 0;
                    const hasCurrentUserStories = currentProjectUserStories && currentProjectUserStories.length > 0;
                    const hasAnyUserStories = hasDeepAnalysisWorkItems || hasCurrentUserStories;
                    return hasAnyUserStories ? (
                      (() => {
                        // Prepare user stories data for display
                        let userStoriesToDisplay = currentProjectUserStories;
                        
                        if (hasDeepAnalysisWorkItems) {
                          // Store Jira user stories in Redux state like Azure does
                          const jiraUserStory = {
                            id: deepAnalysis.id,
                            type: deepAnalysis.type || 'user_story',
                            userId: deepAnalysis.userId,
                            projectId: deepAnalysis.projectId,
                            process_template: deepAnalysis.process_template || 'Agile',
                            platform: deepAnalysis.platform,
                            work_items: deepAnalysis.work_items,
                            summary: deepAnalysis.summary,
                            generated_at: deepAnalysis.generated_at,
                            comments_count: deepAnalysis.comments_count || 0
                          };
                          
                          // Check if we need to store this in Redux (similar to Azure logic)
                          if (!hasCurrentUserStories) {
                            dispatch(setCurrentProjectUserStories([jiraUserStory]));
                          }
                          
                          userStoriesToDisplay = [jiraUserStory];
                        }
                        
                        
                        return (
                          <UserStoryList 
                            userStories={userStoriesToDisplay}
                            platform="jira"
                            projectId={currentProjectId}
                            onRegenerateAnalysis={async () => {
                              if (loadedComments && loadedComments.length > 0) {
                                try {
                                  // Use existing analysis data instead of calling analyzeComments again
                                  const analysisResult = analysisData;
                                  
                                  if (!analysisResult) {
                                    console.error('No analysis data available for Jira work item generation');
                                    return;
                                  }
                                  
                                  // Step 1: Get Jira project metadata
                                  let jiraProjectMetadata = null;
                                  const selectedJiraProjectId = typeof window !== 'undefined' ? localStorage.getItem('jira_selected_project') : null;
                                  
                                  if (selectedJiraProjectId) {
                                    try {
                                      jiraProjectMetadata = null;
                                    } catch (e) {
                                      console.warn('Failed to fetch Jira project metadata:', e);
                                    }
                                  }
                                  
                                  // Step 3: Generate work items
                                  const workItemsResult = await dispatch(generateUserStories({
                                    analysisData: analysisResult,
                                    comments: loadedComments, // Add the original comments
                                    platform: selectedPlatform === 'jira' ? 'jira' : 'azure',
                                    processTemplate: 'Agile',
                                    projectId: projectId || undefined,
                                    projectMetadata: jiraProjectMetadata
                                  })).unwrap();

                                  // Trigger usage badge refresh after work items generation
                                  if (typeof window !== 'undefined') {
                                    window.dispatchEvent(new Event('usage-updated'));
                                  }

                                  // Structure the data properly for the UserStories component
                                  const structuredData = {
                                    ...workItemsResult,
                                    work_items: workItemsResult.work_items,
                                    work_items_by_feature: workItemsResult.work_items_by_feature,
                                    summary: workItemsResult.summary
                                  };
                                  dispatch(setDeepAnalysis(structuredData));
                                } catch (error) {
                                  console.error('Failed to regenerate Jira analysis:', error);
                                }
                              }
                            }}
                            isAnalyzing={loading}
                          />
                        );
                      })()
                    ) : (
                      <div className="text-center py-8">
                        <div className="w-16 h-16 mx-auto mb-4 bg-secondary/60 rounded-full flex items-center justify-center">
                          <Sparkles className="w-8 h-8 text-muted-foreground" />
                        </div>
                        <h3 className="text-lg font-medium text-foreground mb-2">
                          No User Stories Generated
                        </h3>
                        <p className="text-muted-foreground mb-4">
                          User stories will be automatically generated after you analyze feedback data.
                        </p>
                        <p className="text-sm text-muted-foreground/70">
                          Go to the Dashboard tab, upload feedback data, and click "Analyze" to generate user stories.
                        </p>
                      </div>
                    );
                  })()
                ) : currentProjectUserStories && currentProjectUserStories.length > 0 ? (
                  /* Show user stories even without loaded comments if they exist in Redux */
                  <UserStoryList 
                    userStories={currentProjectUserStories}
                    platform="jira"
                    projectId={currentProjectId}
                    isAnalyzing={loading}
                  />
                ) : (
                  <div className="text-center py-8">
                    <div className="w-16 h-16 mx-auto mb-4 bg-secondary/60 rounded-full flex items-center justify-center">
                      <Sparkles className="w-8 h-8 text-muted-foreground" />
                    </div>
                    <h3 className="text-lg font-medium text-foreground mb-2">
                      No User Stories Found
                    </h3>
                    <p className="text-muted-foreground mb-4">
                      {loadedComments && loadedComments.length > 0 
                        ? "User stories should have been generated. Try refreshing or check the console for errors."
                        : "No comments available. Please upload feedback data to use Jira integration."
                      }
                    </p>
                    {process.env.NODE_ENV === 'development' && (
                      <button
                        onClick={() => {
                          const effectiveProjectId = currentProjectId || personalProjectId;
                          if (effectiveProjectId && user?.id) {
                            const formattedProjectId = effectiveProjectId.startsWith('project_') ? effectiveProjectId.replace('project_', '') : effectiveProjectId;
                            const userId = user.id || user.user_id;
                            dispatch(fetchUserStoriesByProject({
                              projectId: formattedProjectId,
                              userId
                            }));
                          }
                        }}
                        className="mt-4 px-4 py-2 bg-saramsa-brand text-white rounded-lg hover:bg-saramsa-brand-hover transition-colors text-sm"
                      >
                        Refresh user stories
                      </button>
                    )}
                  </div>
                )
              ) : (
                /* Azure User Stories View */
                (() => {
                  // Check if we have work items in the response
                  const hasWorkItems = deepAnalysis?.work_items && deepAnalysis.work_items.length > 0;
                  const hasValidDeepAnalysis = deepAnalysis && (deepAnalysis.work_items || deepAnalysis.work_items_by_feature);
                  const hasUserStories = currentProjectUserStories && currentProjectUserStories.length > 0;
                  
                  // Simplified condition - show if we have ANY work items from either source
                  const shouldShowUserStories = (hasValidDeepAnalysis && hasWorkItems) || hasUserStories;
                  
                  return shouldShowUserStories ? (
                    <UserStoryList 
                      key={`user-stories-${deepAnalysis?.id || currentProjectUserStories?.[0]?.id || 'default'}`} 
                      userStories={hasWorkItems ? [{
                        id: deepAnalysis.id,
                        type: deepAnalysis.type || 'user_story',
                        userId: deepAnalysis.userId,
                        projectId: deepAnalysis.projectId,
                        process_template: deepAnalysis.process_template || 'Agile',
                        platform: deepAnalysis.platform,
                        work_items: deepAnalysis.work_items,
                        summary: deepAnalysis.summary,
                        generated_at: deepAnalysis.generated_at,
                        comments_count: deepAnalysis.comments_count || 0
                      }] : currentProjectUserStories}
                      platform="azure"
                      projectId={currentProjectId}
                    />
                  ) : (
                    <div className="text-center py-8">
                      <p className="text-muted-foreground">
                        No deep analysis data available. Please upload feedback data to generate user stories.
                      </p>
                    </div>
                  );
                })()
              )}
            </div>
            )}

            {!isTaskViewLoading && currentProjectId && (
              <div className="bg-card/80 rounded-2xl border border-border/60 p-6">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <h3 className="text-lg font-semibold text-foreground">Review Queue</h3>
                    <p className="text-sm text-muted-foreground">
                      Review generated work items before pushing them.
                    </p>
                  </div>
                  <a
                    href={`/projects/${encryptProjectId(currentProjectId)}/review/`}
                    className="inline-flex items-center rounded-lg bg-saramsa-brand px-4 py-2 text-sm font-medium text-white transition hover:bg-saramsa-brand-hover"
                  >
                    Open Review Queue
                  </a>
                </div>
              </div>
            )}
              </div>
              )}
              </div>
            </>

          </main>
        </div>
      </div>
    </div>
  );
}



