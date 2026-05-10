"use client";

import { useEffect, useMemo, useState, CSSProperties } from "react";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  toggleActionSelection,
  clearSelectedActions,
  clearActionItems,
  addActionItem,
  updateActionItem,
  removeActionItem,
} from "@/store/features/workItems/workItemsSlice";
import { submitUserStories, setDeepAnalysis } from "@/store/features/analysis/analysisSlice";
import type { UserStory } from "@/store/features/userStories/userStoriesSlice";
import { deleteWorkItems, fetchUserStoriesByProject, setCurrentProjectUserStories } from "@/store/features/userStories/userStoriesSlice";
import { DeleteWorkItemsModal } from './DeleteWorkItemsModal';
import { DashboardIntegrationModal } from './dashboard/DashboardIntegrationModal';
import { WorkItemReviewModal } from './WorkItemReviewModal';
import { WorkItemQualityGateModal } from './WorkItemQualityGateModal';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import type {
  ActionItem,
  Feature,
} from "@/store/features/workItems/workItemsSlice";
import {
  Edit,
  Trash2,
  Send,
  ChevronDown,
  ChevronRight,
  Sparkles,
  CheckCircle,
} from 'lucide-react';
import { Badge } from "./badge";
import { Card, CardContent } from "./card";
import { Checkbox } from "./checkbox";
import { EditActionDrawer } from "./edit-action-drawer";
import apiRequest from "@/lib/apiRequest";
import { getRelatedInsightsForWorkItem } from "@/lib/insightTraceability";
import { DEFAULT_QUALITY_RULES, evaluateWorkItems, type QualityReport, type QualityRules } from "@/lib/workItemQuality";
import { sortWorkItemsByPriority } from "@/lib/workItemPrioritySort";

interface WorkItem {
  id: string;
  type: string;
  title: string;
  description: string;
  priority: string;
  tags?: string[];
  labels?: string[];
  acceptance_criteria?: string;
  acceptance?: string;
  business_value?: string;
  effort_estimate?: string;
  feature_area: string;
  created_at?: string;
}

interface UserStoryListProps {
  userStories?: UserStory[];
  platform?: 'azure' | 'jira';
  projectId?: string;
  projectKey?: string;
  onRegenerateAnalysis?: () => void;
  isAnalyzing?: boolean;
}

export const UserStoryList = ({ 
  userStories, 
  platform = 'azure',
  projectId,
  projectKey,
  onRegenerateAnalysis,
  isAnalyzing 
}: UserStoryListProps) => {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const { selectedActions, actionItems, features, loading, error } = useAppSelector((state) => state.workItems);
  const { loading: analysisLoading, projectContext, analysisData } = useAppSelector((state) => state.analysis);
  const { user, isAuthenticated } = useAppSelector((state) => state.auth);
  const { currentProjectUserStories } = useAppSelector((state) => state.userStories);
  const { projects } = useAppSelector((state) => state.projects);
  const [currentProjectRole, setCurrentProjectRole] = useState<string | null>(null);
  
  const isPushing = analysisLoading;
  
  // Check if project is draft - check both projectContext and actual project from projects list
  const currentProject = projectId ? projects.find((p: any) => p.id === projectId) : null;
  const hasIntegrations = currentProject?.externalLinks && currentProject.externalLinks.length > 0;
  // Check if the specific platform integration exists
  const hasPlatformIntegration = currentProject?.externalLinks?.some(
    (link: any) => link.provider === platform
  ) || false;
  const isDraftFromContext = projectContext?.is_draft === true || projectContext?.config_state === 'unconfigured';
  const isDraftProject = isDraftFromContext || (!hasIntegrations && projectId) || (!hasPlatformIntegration && projectId);
  const canManageAdminActions =
    !!projectId && (
      user?.role === 'admin' ||
      (!!currentProject?.userId && !!user?.id && String(currentProject.userId) === String(user.id)) ||
      currentProjectRole === 'owner' ||
      currentProjectRole === 'admin'
    );
  const currentProjectUserStoryIds = currentProjectUserStories.map(story => story.id);
  const currentProjectUserStoryWorkItems = currentProjectUserStories.flatMap(story => story.work_items || []);
  const currentProjectUserStoryWorkItemIds = currentProjectUserStoryWorkItems.map(item => item.id);
  const currentProjectUserStoryWorkItemIdsSet = new Set(currentProjectUserStoryWorkItemIds);

  const [editingAction, setEditingAction] = useState<ActionItem | null>(null);
  const [isEditDrawerOpen, setIsEditDrawerOpen] = useState(false);
  const [collapsedFeatures, setCollapsedFeatures] = useState<Set<string>>(
    new Set()
  );
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [showIntegrationModal, setShowIntegrationModal] = useState(false);
  const [expandedDescriptionIds, setExpandedDescriptionIds] = useState<Record<string, boolean>>({});
  const [showReviewModal, setShowReviewModal] = useState(false);
  const [showQualityModal, setShowQualityModal] = useState(false);
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [qualityRules, setQualityRules] = useState<QualityRules>(DEFAULT_QUALITY_RULES);
  const [qualityLoading, setQualityLoading] = useState(false);
  /** Items staged for the review/push flow (header multi-select or per-row push). */
  const [pushQueueItems, setPushQueueItems] = useState<ActionItem[]>([]);

  const insightsList = useMemo(() => {
    const data = analysisData as Record<string, unknown> | null;
    const nested = data?.analysisData as Record<string, unknown> | undefined;
    const rawInsights =
      data?.insights ||
      nested?.insights ||
      nested?.pipeline_insights ||
      nested?.pipelineInsights ||
      [];
    return Array.isArray(rawInsights)
      ? rawInsights.map((insight: unknown) => String(insight)).filter(Boolean)
      : [];
  }, [analysisData]);

  useEffect(() => {
    let active = true;

    const loadCurrentRole = async () => {
      if (!projectId || !isAuthenticated) {
        if (active) setCurrentProjectRole(null);
        return;
      }

      try {
        const response = await apiRequest('get', `/integrations/projects/${projectId}/roles/`, undefined, true);
        const role = response?.data?.data?.current_user_role;
        if (active) {
          setCurrentProjectRole(typeof role === 'string' ? role : null);
        }
      } catch {
        if (active) setCurrentProjectRole(null);
      }
    };

    void loadCurrentRole();
    return () => {
      active = false;
    };
  }, [projectId, isAuthenticated]);

  const relatedInsightsByActionId = useMemo(() => {
    if (!insightsList.length || actionItems.length === 0) return new Map<string, string[]>();
    const map = new Map<string, string[]>();
    actionItems.forEach((action) => {
      const matches = getRelatedInsightsForWorkItem(insightsList, {
        id: action.id,
        title: action.title,
        description: action.description,
        tags: action.tags,
        featureArea: action.featureArea,
      });
      map.set(action.id, matches.map((match) => match.item));
    });
    return map;
  }, [actionItems, insightsList]);

  const toggleDescription = (id: string) => {
    setExpandedDescriptionIds((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  const truncateStyle: CSSProperties = useMemo(
    () => ({
      display: "-webkit-box",
      WebkitLineClamp: 1,
      WebkitBoxOrient: "vertical",
      overflow: "hidden",
    }),
    []
  );

  const renderDescription = (text: string | undefined | null, id: string) => {
    if (!text) return null;
    const isExpanded = expandedDescriptionIds[id] === true;
    const shouldShowToggle = text.length > 180;

    return (
      <div className="space-y-1">
        <p
          className="text-sm text-muted-foreground dark:text-muted-foreground leading-relaxed"
          style={!isExpanded ? truncateStyle : undefined}
        >
          {text}
        </p>
        {shouldShowToggle && (
          <Button
            type="button"
            onClick={() => toggleDescription(id)}
            variant="link"
            size="sm"
            className="text-xs font-medium text-saramsa-brand hover:text-saramsa-brand-hover dark:text-saramsa-brand dark:hover:text-saramsa-brand-hover focus:outline-none"
          >
            {isExpanded ? "See less" : "See more"}
          </Button>
        )}
      </div>
    );
  };

  const renderRelatedInsights = (actionId: string) => {
    const related = relatedInsightsByActionId.get(actionId) || [];
    if (related.length === 0) return null;

    return (
      <div className="flex flex-wrap gap-1">
        {related.slice(0, 2).map((insight, idx) => (
          <Badge key={`${actionId}-insight-${idx}`} variant="outline" className="text-xs">
            Insight: {insight}
          </Badge>
        ))}
      </div>
    );
  };

  const recalculateSummary = (existingSummary: UserStory['summary'] | undefined, updatedWorkItems: UserStory['work_items'] = []) => {
    const summary = existingSummary
      ? { ...existingSummary }
      : {
          totalitems: 0,
          bytype: {} as Record<string, number>,
          bypriority: {} as Record<string, number>,
        };

    summary.totalitems = updatedWorkItems.length;
    summary.bytype = {};
    summary.bypriority = {};

    updatedWorkItems.forEach((item) => {
      const typeKey = (item.type || 'Unknown').toLowerCase();
      const priorityKey = (item.priority || 'Unknown').toLowerCase();

      summary.bytype[typeKey] = (summary.bytype[typeKey] || 0) + 1;
      summary.bypriority[priorityKey] = (summary.bypriority[priorityKey] || 0) + 1;
    });

    return summary;
  };


  // Set user stories or deep analysis data when component mounts or data changes
  useEffect(() => {
    
    // Use userStories work items (prioritize prop over Redux state)
    const workItemsToProcess = userStories && userStories.length > 0 
      ? userStories[0]?.work_items // Use the first (most recent) user story from props
      : currentProjectUserStories && currentProjectUserStories.length > 0
      ? currentProjectUserStories[0]?.work_items // Fallback to Redux state
      : [];
    
    
    if (workItemsToProcess && workItemsToProcess.length > 0) {
      
      // Clear existing action items first to prevent duplicates
      dispatch(clearActionItems());
      dispatch(clearSelectedActions());
      
      // Convert work items to action items and add them
      workItemsToProcess.forEach((item, index) => {
        const actionItem: ActionItem = {
          id: item.id,
          title: item.title,
          description: item.description,
          priority: item.priority as "low" | "medium" | "high" | "critical",
          type: item.type as "feature" | "bug" | "change",
          status: "todo",
          createdAt: 'created_at' in item ? item.created_at : new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          tags: item.tags || item.labels || [],
          acceptance: item.acceptance_criteria || item.acceptancecriteria || item.acceptance || '',
          featureArea: item.feature_area || item.featurearea || '',
          review_status: item.status || 'pending',
          push_status: item.push_status || (item.submitted ? 'pushed' : 'not_pushed'),
          submitted: item.submitted || item.push_status === 'pushed',
        };
        dispatch(addActionItem(actionItem));
      });
      
    } else {
    }
  }, [userStories, currentProjectUserStories, dispatch, platform]);

  const handleActionSelect = (actionId: string) => {
    // Check if this work item has already been submitted or is in a non-selectable state
    const workItem = currentProjectUserStoryWorkItems.find(item => item.id === actionId);
    const reviewStatus = workItem?.status || 'pending';
    const isPushed = workItem?.submitted || workItem?.push_status === 'pushed';

    if (isPushed) {
      alert('This work item has already been pushed and cannot be selected again.');
      return;
    }
    if (reviewStatus === 'dismissed') {
      alert('This work item has been dismissed. Restore it from the Review Queue first.');
      return;
    }
    if (reviewStatus === 'snoozed') {
      alert('This work item is snoozed. Unsnooze it from the Review Queue first.');
      return;
    }
    dispatch(toggleActionSelection(actionId));
  };

  const handleEditAction = (action: ActionItem) => {
    setEditingAction(action);
    setIsEditDrawerOpen(true);
  };

  const handleSaveAction = async (updatedAction: ActionItem) => {
    try {
      // First update the local state immediately for better UX
      dispatch(updateActionItem(updatedAction));

      // Then make API call to persist changes to backend
      const response = await apiRequest(
        "put",
        `/insights/work-items/${updatedAction.id}/update/`,
        {
          title: updatedAction.title,
          description: updatedAction.description,
          acceptance: updatedAction.acceptance,
          priority: updatedAction.priority,
          type: updatedAction.type,
          tags: updatedAction.tags,
          status: updatedAction.status,
          project_id: projectId,
        },
        true
      );

      if (response.status !== 200) {
        throw new Error("Failed to update work item on server");
      }

      const result = response.data;

      setIsEditDrawerOpen(false);
      setEditingAction(null);
    } catch (error) {
      console.error("Error updating user story:", error);
      // Revert the local state change if server update failed
      if (editingAction) {
        dispatch(updateActionItem(editingAction));
      }
      alert("Failed to update user story. Please try again.");
    }
  };

  const handlePushActionItems = async () => {
    const itemsToSubmit = pushQueueItems.length > 0 ? pushQueueItems : getSelectedItems();
    if (itemsToSubmit.length === 0) {
      alert("Please select user stories to push");
      return;
    }

    if (!projectId) {
      alert("Project ID is required for submission");
      return;
    }

    // Check if project is a draft (no integrations configured)
    // Also check if projectId starts with "proj_draft" which indicates a draft project
    const isDraftProjectId = projectId && (projectId.startsWith('proj_draft') || projectId.includes('draft'));
    
    
    // Block push if: draft project, draft project ID, no integrations at all, or no platform-specific integration
    // Also block if currentProject is null (project not in list yet, likely a draft)
    const shouldBlockPush = 
      isDraftProject || 
      isDraftProjectId || 
      (projectId && !currentProject) ||  // Project not found in list - likely draft
      (currentProject && !hasIntegrations) || 
      (currentProject && !hasPlatformIntegration);
    
    if (shouldBlockPush) {
      setShowIntegrationModal(true);
      return;
    }

    // The backend expects the project ID without the project_ prefix
    // It will normalize the prefix internally when resolving the project ID
    const formattedProjectId = projectId.startsWith('project_') ? projectId.replace('project_', '') : projectId;

    if (!isAuthenticated || !user) {
      alert("User authentication is required. Please login again.");
      return;
    }

    if (!user.user_id && !user.id) {
      alert("User ID not found. Please logout and login again.");
      return;
    }

    // Use the id field (which contains the proper user_xxxxx format) instead of user_id
    const userId = user.id || user.user_id || '';
    
    // Check if user_id is in the correct format
    if (userId && !userId.startsWith('user_')) {
      console.warn('⚠️ User ID is not in expected format. Forcing session refresh...');
      alert("User session needs to be refreshed. Please logout and login again to get the correct user ID.");
      return;
    }
    
    if (!userId) {
      alert("User ID could not be determined. Please logout and login again.");
      return;
    }


    // Transform action items to user story format
    const userStoriesToSubmit = itemsToSubmit.map((item: ActionItem) => ({
      id: item.id,
      title: item.title,
      description: item.description,
      type: item.type === 'feature' ? 'Feature' : item.type === 'bug' ? 'Bug' : 'Task',
      priority: item.priority,
      acceptance_criteria: item.acceptance || '',
      business_value: '', // Could be added to ActionItem interface if needed
      tags: item.tags || [],
      created: false // Always false for new submissions
    }));

    try {
      
      const result = await dispatch(
        submitUserStories({
          userId: userId,
          projectId: formattedProjectId,
          userStories: userStoriesToSubmit,
          platform: platform,
          processTemplate: 'Agile',
          time: new Date().toISOString()
        })
      ).unwrap();

      // Check if the submission was actually successful
      if (result.success) {
        // Clear selection after successful submission
        dispatch(clearSelectedActions());
        setPushQueueItems([]);
        
        // Update Redux state with the updated user stories from the response
        if (result.updated_user_stories && Array.isArray(result.updated_user_stories)) {
          dispatch(setCurrentProjectUserStories(result.updated_user_stories));
          
          // Also update deepAnalysis to sync the top section
          if (userStories && userStories.length > 0) {
            const updatedDeepAnalysis = {
              ...userStories[0],
              work_items: result.updated_user_stories[0]?.work_items || userStories[0].work_items
            };
            dispatch(setDeepAnalysis(updatedDeepAnalysis));
          }
        } else {
          // Fallback: Refresh user stories to get updated submission status
          if (projectId && user) {
            dispatch(fetchUserStoriesByProject({ 
              projectId: projectId.startsWith('project_') ? projectId.replace('project_', '') : projectId,
              userId: user.id || user.user_id
            }));
          }
        }
        
        const summary = result.summary || {};
        const created = summary.created || 0;
        const skipped = summary.skipped || 0;
        const failed = summary.failed || 0;
      const submissionResults = (result.results || []).filter((r: any) => r.success);
      const submissionTimestamp = result.submitted_at || new Date().toISOString();

      if (submissionResults.length > 0) {
        const submittedIdSet = new Set(submissionResults.map((r: any) => r.story_id));

        const baseUserStory =
          currentProjectUserStories.find((story) => story.work_items?.some((item) => submittedIdSet.has(item.id))) ||
          userStories?.find((story) => story.work_items?.some((item) => submittedIdSet.has(item.id)));

        if (baseUserStory) {
          const updatedWorkItems =
            baseUserStory.work_items?.map((item) => {
              if (submittedIdSet.has(item.id)) {
                const match = submissionResults.find((res: any) => res.story_id === item.id);
                return {
                  ...item,
                  submitted: true,
                  submittedAt: submissionTimestamp,
                  submittedTo: platform,
                  external_id: match?.work_item_id ?? match?.issue_key ?? item.external_id,
                  external_url: match?.url ?? item.external_url,
                };
              }
              return item;
            }) || [];

          const updatedSummary = recalculateSummary(baseUserStory.summary, updatedWorkItems);
          const updatedStory: UserStory = {
            ...baseUserStory,
            work_items: updatedWorkItems,
            summary: updatedSummary,
            generated_at: baseUserStory.generated_at,
          };

          const nextStories = currentProjectUserStories.length
            ? currentProjectUserStories.map((story) => (story.id === updatedStory.id ? updatedStory : story))
            : [updatedStory];

          dispatch(setCurrentProjectUserStories(nextStories));
          dispatch(setDeepAnalysis({
            ...(userStories?.[0] || updatedStory),
            work_items: updatedWorkItems,
            summary: updatedSummary,
          }));

          // The canonical render path reads UserStory.work_items[].external_id /
          // .external_url (updated above). The per-ActionItem mirror only
          // tracks the submitted/submittedAt flags now — external id/url is
          // intentionally not duplicated here to avoid drift.
          submissionResults.forEach((res: any) => {
            const targetAction = actionItems.find((action) => action.id === res.story_id);
            if (targetAction) {
              dispatch(
                updateActionItem({
                  ...targetAction,
                  status: 'done',
                  submitted: true,
                  submittedAt: submissionTimestamp,
                })
              );
            }
          });
        }
      }
        
        if (created > 0) {
          alert(`Successfully submitted ${created} user stories to ${platform === 'azure' ? 'Azure DevOps' : 'Jira'}${skipped > 0 ? ` (${skipped} already existed)` : ''}`);
        } else if (skipped > 0 && failed === 0) {
          alert(`All ${skipped} user stories were already created in ${platform === 'azure' ? 'Azure DevOps' : 'Jira'}`);
        }
      } else {
        // Handle case where submission failed
        const summary = result.summary || {};
        const failed = summary.failed || 0;
        const results = result.results || [];
        
        // Get error messages from failed results
        const errorMessages = results
          .filter((r: any) => !r.success)
          .map((r: any) => r.error)
          .filter(Boolean);
        
        const errorMessage = errorMessages.length > 0 
          ? errorMessages[0] 
          : 'Unknown error occurred';
        
        alert(`Failed to submit user stories: ${errorMessage}`);
      }
    } catch (error) {
      console.error('Failed to submit user stories:', error);
      alert(`Failed to submit user stories: ${error}`);
    }
  };

  const fetchQualityRules = async () => {
    if (!projectId) return DEFAULT_QUALITY_RULES;
    try {
      const response = await apiRequest('get', `/work-items/quality-rules/?project_id=${projectId}`, undefined, true);
      const rules = response?.data?.data?.rules;
      if (rules) {
        setQualityRules(rules);
        return rules as QualityRules;
      }
    } catch (error) {
      // Fall back to default rules if API fails
    }
    setQualityRules(DEFAULT_QUALITY_RULES);
    return DEFAULT_QUALITY_RULES;
  };

  const runPushPreflightForItems = async (items: ActionItem[]) => {
    setQualityLoading(true);
    const rules = await fetchQualityRules();
    const report = evaluateWorkItems(items, rules);
    report.allow_push_with_warnings = rules.allow_push_with_warnings;

    setQualityReport(report);
    setQualityLoading(false);

    if (report.items_with_issues > 0) {
      setShowQualityModal(true);
      if (!rules.allow_push_with_warnings) {
        return;
      }
    }

    setShowReviewModal(true);
  };

  const handleReviewClick = async () => {
    if (selectedActions.length === 0) {
      alert("Please select user stories to push");
      return;
    }

    const selectedItems = getSelectedItems();
    if (selectedItems.length === 0) {
      alert("No selected items found");
      return;
    }

    setPushQueueItems(selectedItems);
    await runPushPreflightForItems(selectedItems);
  };

  const handleRowPushClick = async (item: ActionItem) => {
    const workItem = currentProjectUserStoryWorkItems.find((wi) => wi.id === item.id);
    const reviewStatus = workItem?.status || item.review_status || "pending";
    const isPushed = workItem?.submitted || workItem?.push_status === "pushed";

    if (isPushed) {
      alert("This work item has already been pushed.");
      return;
    }
    if (reviewStatus === "dismissed") {
      alert("This work item has been dismissed. Restore it from the Review Queue first.");
      return;
    }
    if (reviewStatus === "snoozed") {
      alert("This work item is snoozed. Unsnooze it from the Review Queue first.");
      return;
    }

    setPushQueueItems([item]);
    await runPushPreflightForItems([item]);
  };

  const getSelectedItems = () => {
    const selectedFromFeatures = features
      .flatMap((feature: Feature) => feature.actions)
      .filter((action: ActionItem) => selectedActions.includes(action.id));

    const selectedFromActionItems = actionItems
      .filter((action: ActionItem) => selectedActions.includes(action.id));

    return [...selectedFromFeatures, ...selectedFromActionItems];
  };

  const handleDeleteSelected = () => {
    if (selectedActions.length === 0) {
      alert("Please select work items to delete");
      return;
    }
    
    setShowDeleteModal(true);
  };

  const handleConfirmDelete = async () => {
    const toDelete = [...selectedActions];
    
    try {
      setDeleteLoading(true);
      
      // We need to identify which user story these work items belong to
      // Use the Redux state to find the matching user story
      const matchingUserStories = currentProjectUserStories.filter(story => 
        story.work_items?.some(item => toDelete.includes(item.id))
      );
      
      if (matchingUserStories.length === 0) {
        alert('Could not find user story containing these work items');
        return;
      }
      
      // Use the first matching user story
      const userStoryId = matchingUserStories[0].id;
      
      // Use the proper Redux action for deleting work items
      await dispatch(deleteWorkItems({ 
        workItemIds: toDelete,
        userStoryId: userStoryId,
        projectId: projectId
      })).unwrap();

      // Remove deleted items from action items list to reflect immediately
      toDelete.forEach((id) => dispatch(removeActionItem(id)));

      const baseStory =
        currentProjectUserStories.find((story) => story.id === userStoryId) ||
        userStories?.find((story) => story.id === userStoryId);

      const updatedWorkItems =
        (baseStory?.work_items || []).filter((item) => !toDelete.includes(item.id));
      const updatedSummary = recalculateSummary(baseStory?.summary, updatedWorkItems);

      if (baseStory) {
        const updatedStory: UserStory = {
          ...baseStory,
          work_items: updatedWorkItems,
          summary: updatedSummary,
        };

        const nextStories = currentProjectUserStories.length
          ? currentProjectUserStories.map((story) => (story.id === userStoryId ? updatedStory : story))
          : [updatedStory];

        dispatch(setCurrentProjectUserStories(nextStories));
        dispatch(
          setDeepAnalysis({
            ...(userStories?.[0] || updatedStory),
            work_items: updatedWorkItems,
            summary: updatedSummary,
          })
        );
      }
      
      // Clear selection after successful deletion
      dispatch(clearSelectedActions());
      
      // Close modal
      setShowDeleteModal(false);
      
    } catch (err) {
      console.error('Failed to delete work items:', err);
      alert('Failed to delete work items. Please try again.');
    } finally {
      setDeleteLoading(false);
    }
  };

  const toggleFeatureCollapse = (featureName: string) => {
    const newCollapsed = new Set(collapsedFeatures);
    if (newCollapsed.has(featureName)) {
      newCollapsed.delete(featureName);
    } else {
      newCollapsed.add(featureName);
    }
    setCollapsedFeatures(newCollapsed);
  };

  const getPriorityColor = (priority: string) => {
    switch (priority.toLowerCase()) {
      case "critical":
        return "bg-red-500 text-white";
      case "high":
        return "bg-orange-500 text-white";
      case "medium":
        return "bg-yellow-500 text-black";
      case "low":
        return "bg-green-500 text-white";
      default:
        return "bg-secondary text-secondary-foreground border border-border";
    }
  };

  const getPlatformDisplayName = () => {
    return platform === 'azure' ? 'Azure DevOps' : 'Jira';
  };

  // Get submitted work items for the "Pushed to" section
  const getSubmittedWorkItems = () => {
    return currentProjectUserStoryWorkItems.filter(
      item => item.submitted === true || item.push_status === 'pushed'
    );
  };

  
  // Check if there are any user stories available (from props, Redux state, features, or action items)
  const hasUserStories = (userStories && userStories.length > 0) || 
                        (currentProjectUserStories && currentProjectUserStories.length > 0);
  const hasWorkItems = features.length > 0 || actionItems.length > 0;
  
  // Also check if we have work_items directly in the user stories data
  const hasWorkItemsInData = userStories?.some(story => story.work_items && story.work_items.length > 0) ||
                            currentProjectUserStories?.some(story => story.work_items && story.work_items.length > 0);
  
  
  // Show content if we have user stories OR work items OR work items in data
  const shouldShowContent = hasUserStories || hasWorkItems || hasWorkItemsInData;
  
  if (!shouldShowContent) {
    return (
      <div className="text-center py-8">
        <div className="w-16 h-16 mx-auto mb-4 bg-slate-100 dark:bg-slate-800 rounded-full flex items-center justify-center">
          <Sparkles className="w-8 h-8 text-slate-400" />
        </div>
        <h3 className="text-lg font-medium text-slate-900 dark:text-foreground mb-2">
          No User Stories Generated
        </h3>
        <p className="text-slate-500 dark:text-slate-400 mb-4">
          User stories will be automatically generated after you analyze
          feedback data.
        </p>
        <p className="text-sm text-slate-400 dark:text-slate-500">
          Go to the Dashboard tab, upload feedback data, and click "Analyze" to
          generate user stories.
        </p>
      </div>
    );
  }

  const draftItems = sortWorkItemsByPriority(
    actionItems.filter((actionItem) => {
      const workItem = currentProjectUserStoryWorkItems.find((item) => item.id === actionItem.id);
      const isPushed = workItem?.submitted || workItem?.push_status === 'pushed';
      const reviewStatus = workItem?.status || actionItem.review_status || 'pending';
      return !isPushed && reviewStatus !== 'dismissed' && reviewStatus !== 'snoozed';
    })
  );
  const dismissedItems = sortWorkItemsByPriority(
    actionItems.filter((actionItem) => {
      const workItem = currentProjectUserStoryWorkItems.find((item) => item.id === actionItem.id);
      return (workItem?.status || actionItem.review_status) === 'dismissed';
    })
  );
  const snoozedItems = sortWorkItemsByPriority(
    actionItems.filter((actionItem) => {
      const workItem = currentProjectUserStoryWorkItems.find((item) => item.id === actionItem.id);
      return (workItem?.status || actionItem.review_status) === 'snoozed';
    })
  );
  const submittedItems = sortWorkItemsByPriority(getSubmittedWorkItems());

  const renderAccordionItem = (item: ActionItem, isSubmitted: boolean, submittedData?: any) => {
    const isExpanded = expandedDescriptionIds[item.id] === true;
    const isSelected = selectedActions.includes(item.id);

    return (
      <div
        key={item.id}
        className={`rounded-xl border transition-all duration-200 ${
          isSubmitted
            ? 'border-emerald-500/20 bg-emerald-500/5'
            : isSelected
            ? 'border-saramsa-brand/30 bg-saramsa-brand/5'
            : 'border-border/60 bg-card/40'
        }`}
      >
        {/* Collapsed Row */}
        <div
          className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none"
          onClick={() => toggleDescription(item.id)}
        >
          {/* Checkbox */}
          {!isSubmitted ? (
            <Checkbox
              checked={isSelected}
              onCheckedChange={(e) => {
                handleActionSelect(item.id);
              }}
              onClick={(e) => e.stopPropagation()}
              className="shrink-0"
            />
          ) : (
            <CheckCircle className="w-4 h-4 text-emerald-500 shrink-0" />
          )}

          {/* Expand Arrow */}
          <ChevronRight
            className={`w-4 h-4 text-muted-foreground shrink-0 transition-transform duration-200 ${
              isExpanded ? 'rotate-90' : ''
            }`}
          />

          {/* Title */}
          <span className="text-sm font-medium text-foreground truncate flex-1 min-w-0">
            {item.title}
          </span>

          {/* Badges */}
          <Badge className={`text-[10px] px-1.5 py-0 shrink-0 ${getPriorityColor(item.priority || 'medium')}`}>
            {item.priority}
          </Badge>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            {item.type}
          </Badge>
          {isSubmitted && (
            <Badge className="text-[10px] px-1.5 py-0 bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300 shrink-0">
              Pushed
            </Badge>
          )}

          {/* Push + Edit (row-level push avoids scrolling to header) */}
          {!isSubmitted && canManageAdminActions && (
            <div
              className="flex items-center gap-0.5 shrink-0"
              onClick={(e) => e.stopPropagation()}
            >
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  void handleRowPushClick(item);
                }}
                disabled={isPushing || qualityLoading}
                className="h-6 w-6 p-0 text-muted-foreground hover:text-saramsa-brand shrink-0"
                title="Push this work item"
              >
                <Send className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  handleEditAction(item);
                }}
                className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground shrink-0"
                title="Edit"
              >
                <Edit className="w-3.5 h-3.5" />
              </Button>
            </div>
          )}
        </div>

        {/* Expanded Content */}
        {isExpanded && (
          <div className="px-4 pb-4 pt-1 border-t border-border/40 space-y-3">
            {/* Description + Acceptance in two columns */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-xs font-semibold text-muted-foreground mb-1">Description</p>
                <p className="text-sm text-foreground/80 leading-relaxed">
                  {item.description || 'No description.'}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-muted-foreground mb-1">Acceptance Criteria</p>
                <p className="text-sm text-foreground/80 leading-relaxed">
                  {item.acceptance || 'Not defined.'}
                </p>
              </div>
            </div>

            {/* Tags */}
            {item.tags && item.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {item.tags.map((tag, i) => (
                  <span key={i} className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-secondary/70 text-foreground">
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Related Insights */}
            {renderRelatedInsights(item.id)}

            {/* Submitted info */}
            {isSubmitted && submittedData && (
              <div className="flex items-center gap-4 text-xs text-muted-foreground pt-1">
                {submittedData.submittedAt && (
                  <span>Submitted {new Date(submittedData.submittedAt).toLocaleDateString()}</span>
                )}
                {submittedData.external_id && (
                  <span>ID: {submittedData.external_id}</span>
                )}
                {submittedData.external_url && (
                  <Button
                    variant="link"
                    size="sm"
                    onClick={() => window.open(submittedData.external_url, '_blank')}
                    className="text-xs text-emerald-600 dark:text-emerald-400 h-auto p-0"
                  >
                    <Send className="w-3 h-3 mr-1" />
                    View in {getPlatformDisplayName()}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Error Message */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-3">
          <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
        </div>
      )}

      {/* Header + Action Buttons */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold text-foreground">
            Work Items
          </h3>
          {(draftItems.length > 0 || submittedItems.length > 0 || dismissedItems.length > 0 || snoozedItems.length > 0) && (
            <span className="text-xs text-muted-foreground">
              {draftItems.length} draft
              {submittedItems.length > 0 ? `, ${submittedItems.length} pushed` : ''}
              {dismissedItems.length > 0 ? `, ${dismissedItems.length} dismissed` : ''}
              {snoozedItems.length > 0 ? `, ${snoozedItems.length} snoozed` : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectedActions.length > 0 && (
            <span className="text-xs text-muted-foreground mr-1">{selectedActions.length} selected</span>
          )}
          {canManageAdminActions && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={handleDeleteSelected}
                disabled={selectedActions.length === 0}
                className="text-red-600 border-red-200 hover:bg-red-50 dark:text-red-400 dark:border-red-800 dark:hover:bg-red-900/20 h-8 text-xs"
              >
                <Trash2 className="w-3.5 h-3.5 mr-1" />
                Delete
              </Button>
              <Button
                size="sm"
                onClick={handleReviewClick}
                disabled={selectedActions.length === 0 || isPushing || qualityLoading}
                className="bg-gradient-to-r from-saramsa-gradient-from to-saramsa-gradient-to hover:from-saramsa-brand-hover hover:to-saramsa-gradient-to text-white h-8 text-xs px-3"
              >
                {isPushing || qualityLoading ? (
                  <div className="flex items-center gap-1.5">
                    <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    <span>{qualityLoading ? 'Checking...' : 'Pushing...'}</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5">
                    <Send className="w-3.5 h-3.5" />
                    <span>{isDraftProject ? 'Configure & Push' : 'Review & Push'}</span>
                  </div>
                )}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Draft Work Items */}
      {draftItems.length > 0 && (
        <div className="space-y-1.5">
          {draftItems.map((item) => renderAccordionItem(item, false))}
        </div>
      )}

      {/* Feature-grouped items */}
      {features.length > 0 && features.map((feature: Feature) => (
        <div key={feature.id} className="space-y-1.5">
          <div
            className="flex items-center gap-2 cursor-pointer select-none py-1"
            onClick={() => toggleFeatureCollapse(feature.name)}
          >
            <ChevronRight
              className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${
                !collapsedFeatures.has(feature.name) ? 'rotate-90' : ''
              }`}
            />
            <Badge className={`text-xs ${feature.color || 'bg-saramsa-brand/10 text-saramsa-brand dark:bg-saramsa-brand/20 dark:text-saramsa-brand'}`}>
              {feature.name}
            </Badge>
            <span className="text-xs text-muted-foreground">{feature.actions.length} items</span>
          </div>
          {!collapsedFeatures.has(feature.name) && (
            <div className="space-y-1.5 ml-4">
              {sortWorkItemsByPriority(feature.actions).map((action: ActionItem) =>
                renderAccordionItem(action, false)
              )}
            </div>
          )}
        </div>
      ))}

      {/* Pushed Items */}
      {submittedItems.length > 0 && (
        <div className="space-y-1.5 pt-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Pushed to {getPlatformDisplayName()}
          </p>
          {submittedItems.map((workItem: any) => {
            // Build a pseudo ActionItem for the renderer
            const pseudoAction: ActionItem = {
              id: workItem.id,
              title: workItem.title,
              description: workItem.description,
              type: workItem.type || 'feature',
              priority: workItem.priority || 'medium',
              acceptance: workItem.acceptance_criteria || workItem.acceptance || '',
              tags: workItem.tags || workItem.labels || [],
              featureArea: workItem.feature_area || '',
              status: 'done',
              submitted: true,
              createdAt: workItem.created_at || '',
              updatedAt: workItem.submittedAt || '',
            };
            return renderAccordionItem(pseudoAction, true, workItem);
          })}
        </div>
      )}

      {/* Snoozed Items */}
      {snoozedItems.length > 0 && (
        <div className="space-y-1.5 pt-2 opacity-60">
          <p className="text-xs font-semibold text-amber-500 uppercase tracking-wider">
            Snoozed ({snoozedItems.length})
          </p>
          {snoozedItems.map((item) => renderAccordionItem(item, true))}
        </div>
      )}

      {/* Dismissed Items */}
      {dismissedItems.length > 0 && (
        <div className="space-y-1.5 pt-2 opacity-40">
          <p className="text-xs font-semibold text-red-400 uppercase tracking-wider">
            Dismissed ({dismissedItems.length})
          </p>
          {dismissedItems.map((item) => renderAccordionItem(item, true))}
        </div>
      )}

      {/* Edit Drawer */}
      <EditActionDrawer
        action={editingAction}
        isOpen={isEditDrawerOpen}
        onClose={() => {
          setIsEditDrawerOpen(false);
          setEditingAction(null);
        }}
        onSave={handleSaveAction}
      />

      {/* Delete Confirmation Modal */}
      {showDeleteModal && (
        <DeleteWorkItemsModal
          workItemCount={selectedActions.length}
          onConfirm={handleConfirmDelete}
          onCancel={() => setShowDeleteModal(false)}
          loading={deleteLoading}
        />
      )}

      {/* Integration Setup Modal */}
      <DashboardIntegrationModal
        isOpen={showIntegrationModal}
        onClose={() => setShowIntegrationModal(false)}
        projectId={projectId || ''}
      />

      <WorkItemReviewModal
        isOpen={showReviewModal}
        onClose={() => {
          setShowReviewModal(false);
          setPushQueueItems([]);
        }}
        onConfirm={() => {
          setShowReviewModal(false);
          handlePushActionItems();
        }}
        items={pushQueueItems}
        platformLabel={getPlatformDisplayName()}
        isSubmitting={isPushing}
      />

      <WorkItemQualityGateModal
        isOpen={showQualityModal}
        onClose={() => {
          setShowQualityModal(false);
          setPushQueueItems([]);
        }}
        onProceed={() => {
          setShowQualityModal(false);
          setShowReviewModal(true);
        }}
        report={qualityReport}
        allowProceed={!!qualityReport?.allow_push_with_warnings}
      />
    </div>
  );
};

