'use client';

import { useEffect } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import type { AppDispatch } from '@/store/store';
import { encryptProjectId } from '@/lib/encryption';
import {
  generateUserStories,
  setDeepAnalysis,
} from '../../../store/features/analysis/analysisSlice';
import {
  setCurrentProjectUserStories,
  fetchUserStoriesByProject,
} from '../../../store/features/userStories/userStoriesSlice';
import { UserStoryList } from '../userStoryList';

export interface WorkItemsPanelProps {
  // Loading flags
  workItemsPanelLoading: boolean;
  isGeneratingUserStories: boolean;
  userStoriesLoading: boolean;
  isTaskViewLoading: boolean;
  loading: boolean;

  // Project / platform context
  selectedPlatform: 'jira' | 'azure' | string | null | undefined;
  currentProjectId: string;
  personalProjectId: string | null;
  projectId: string | null;
  user: any;

  // Analysis data
  loadedComments: string[] | null;
  deepAnalysis: any;
  currentProjectUserStories: any[] | null;
  analysisData: any;

  // Redux
  dispatch: AppDispatch;
}

/**
 * Renders the "Work items" tab body of the Dashboard.
 *
 * Required state is passed as explicit props. Redux dispatch and the
 * specific thunks/actions used by the inline regenerate handlers are passed
 * through so the imperative side effects fire from this component.
 */
export function WorkItemsPanel(props: WorkItemsPanelProps) {
  const {
    workItemsPanelLoading,
    isGeneratingUserStories,
    userStoriesLoading,
    isTaskViewLoading,
    loading,
    selectedPlatform,
    currentProjectId,
    personalProjectId,
    projectId,
    user,
    loadedComments,
    deepAnalysis,
    currentProjectUserStories,
    analysisData,
    dispatch,
  } = props;

  // Side-effect: prime Redux's currentProjectUserStories from a fresh
  // deepAnalysis on the Jira branch. Kept in a useEffect (rather than during
  // render) so it fires after render commit; Redux holds the jira_user_story
  // so navigating away preserves it. The displayed list is computed locally
  // from deepAnalysis regardless of Redux state.
  useEffect(() => {
    if (selectedPlatform !== 'jira') return;
    const hasDeepAnalysisWorkItems = deepAnalysis?.work_items && deepAnalysis.work_items.length > 0;
    const hasCurrentUserStories = currentProjectUserStories && currentProjectUserStories.length > 0;
    if (!hasDeepAnalysisWorkItems || hasCurrentUserStories) return;
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
      comments_count: deepAnalysis.comments_count || 0,
    };
    dispatch(setCurrentProjectUserStories([jiraUserStory]));
  }, [selectedPlatform, deepAnalysis, currentProjectUserStories, dispatch]);

  return (
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
                      // Build the local list for display. The Redux priming
                      // happens in the useEffect at the top of this component.
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
                        comments_count: deepAnalysis.comments_count || 0,
                      };
                      userStoriesToDisplay = [jiraUserStory];
                    }

                    return (
                      <UserStoryList
                        userStories={userStoriesToDisplay ?? undefined}
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
                                projectMetadata: jiraProjectMetadata,
                              })).unwrap() as any;

                              // Trigger usage badge refresh after work items generation
                              if (typeof window !== 'undefined') {
                                window.dispatchEvent(new Event('usage-updated'));
                              }

                              // Structure the data properly for the UserStories component
                              if (workItemsResult && typeof workItemsResult === 'object' && workItemsResult !== null) {
                                const structuredData = {
                                  ...workItemsResult,
                                  work_items: workItemsResult.work_items,
                                  work_items_by_feature: workItemsResult.work_items_by_feature,
                                  summary: workItemsResult.summary,
                                };
                                dispatch(setDeepAnalysis(structuredData));
                              }
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
                    : "No comments available. Please upload feedback data to use Jira integration."}
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
                          userId,
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
                (() => {
                  const userStoriesToPass = hasWorkItems ? [{
                    id: deepAnalysis.id,
                    type: deepAnalysis.type || 'user_story',
                    userId: deepAnalysis.userId,
                    projectId: deepAnalysis.projectId,
                    process_template: deepAnalysis.process_template || 'Agile',
                    platform: deepAnalysis.platform,
                    work_items: deepAnalysis.work_items,
                    summary: deepAnalysis.summary,
                    generated_at: deepAnalysis.generated_at,
                    comments_count: deepAnalysis.comments_count || 0,
                  }] : (currentProjectUserStories ?? undefined);
                  console.log('[WorkItemsPanel] Passing to UserStoryList:', userStoriesToPass?.[0]?.work_items?.length, 'work items');
                  console.log('[WorkItemsPanel] Work item IDs:', userStoriesToPass?.[0]?.work_items?.map((wi: any) => wi.id));
                  return (
                    <UserStoryList
                      key={`user-stories-${deepAnalysis?.id || currentProjectUserStories?.[0]?.id || 'default'}`}
                      userStories={userStoriesToPass}
                      platform="azure"
                      projectId={currentProjectId}
                    />
                  );
                })()
              ) : (
                <div className="text-center py-8">
                  <p className="text-muted-foreground">
                    No deep analysis data available. Please upload feedback data to generate user stories.
                  </p>
                </div>
              )
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
  );
}
