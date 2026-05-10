import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';

export interface UserStory {
  id: string;
  type: string;
  userId: string;
  projectId: string;
  process_template: string;
  platform: string;
  generated_at: string;
  work_items: WorkItem[];
  summary: {
    totalitems: number;
    bytype: Record<string, number>;
    bypriority: Record<string, number>;
  };
  comments_count: number;
}

export interface WorkItem {
  type: string;
  title: string;
  description: string;
  priority: string;
  tags: string[];
  labels?: string[];
  acceptance_criteria: string;
  acceptancecriteria?: string; // Legacy field name
  acceptance?: string; // Legacy field name
  business_value: string;
  businessvalue?: string; // Legacy field name
  effort_estimate: string;
  effortestimate?: string; // Legacy field name
  feature_area: string;
  featurearea?: string; // Legacy field name
  id: string;
  created_at: string;
  project_id: string;
  process_template: string;
  platform: string;
  submitted?: boolean;
  submittedAt?: string;
  submittedTo?: string;
  external_id?: string;
  external_url?: string;
  status?: 'pending' | 'approved' | 'dismissed' | 'snoozed' | 'merged';
  push_status?: 'not_pushed' | 'pushed' | 'failed';
}

interface UserStoriesState {
  userStories: UserStory[];
  currentProjectUserStories: UserStory[];
  loading: boolean;
  error: string | null;
  lastFetchedProjectId: string | null;
  lastFetchedUserId: string | null;
}

const initialState: UserStoriesState = {
  userStories: [],
  currentProjectUserStories: [],
  loading: false,
  error: null,
  lastFetchedProjectId: null,
  lastFetchedUserId: null,
};

// Async thunk for getting user stories by user and project
export const fetchUserStoriesByProject = createAsyncThunk<
  { userStories: UserStory[]; projectId: string; userId: string },
  { projectId: string; userId?: string },
  { rejectValue: string }
>('userStories/fetchUserStoriesByProject', async (params, { rejectWithValue }) => {
  try {
    const queryParams: any = { project_id: params.projectId };
    if (params.userId) {
      queryParams.user_id = params.userId;
    }

    const response = await apiRequest('get', '/insights/user-stories/', queryParams, true);

    return {
      userStories: response.data.data.user_stories || [],
      projectId: params.projectId,
      userId: params.userId || response.data.data.user_id
    };
  } catch (err: any) {
    let errorMessage = 'Failed to load user stories.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'User stories not found.';
    } else if (err.response?.status >= 500) {
    } else if (err.message) {
      errorMessage = err.response?.data?.error || err.message;
    }
    return rejectWithValue(errorMessage);
  }
});
// Async thunk for updating a user story
export const updateUserStory = createAsyncThunk<
  { userStory: UserStory; userStoryId: string },
  { userStoryId: string; updatedData: Partial<UserStory> },
  { rejectValue: string }
>('userStories/updateUserStory', async (params, { rejectWithValue }) => {
  try {
    const response = await apiRequest('put', `/insights/user-stories/${params.userStoryId}/`, params.updatedData, true);
    return {
      userStory: response.data.data.user_story,
      userStoryId: params.userStoryId
    };
  } catch (err: any) {
    let errorMessage = 'Failed to update user story.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'User story not found.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for deleting a user story
export const deleteUserStory = createAsyncThunk<
  { userStoryId: string },
  { userStoryId: string },
  { rejectValue: string }
>('userStories/deleteUserStory', async (params, { rejectWithValue }) => {
  try {
    // Use the individual endpoint for single user story deletion
    await apiRequest('delete', `/insights/user-stories/${params.userStoryId}/delete/`, undefined, true);
    return {
      userStoryId: params.userStoryId
    };
  } catch (err: any) {
    let errorMessage = 'Failed to delete user story.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'User story not found.';
    } else if (err.response?.status === 405) {
      errorMessage = 'Delete operation not allowed. Please check your permissions.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.response?.data?.error || err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for bulk deleting user stories
export const deleteUserStories = createAsyncThunk<
  { deletedIds: string[]; failedDeletions?: Array<{id: string; error: string}> },
  { userStoryIds: string[] },
  { rejectValue: string }
>('userStories/deleteUserStories', async (params, { rejectWithValue }) => {
  try {
    // For single user story, use the individual endpoint for better performance
    if (params.userStoryIds.length === 1) {
      const userStoryId = params.userStoryIds[0];
      await apiRequest('delete', `/insights/user-stories/${userStoryId}/delete/`, undefined, true);
      return {
        deletedIds: [userStoryId],
        failedDeletions: []
      };
    }
    
    // For multiple user stories, use the bulk endpoint
    const response = await apiRequest('delete', '/insights/user-stories/delete-items/', {
      ids: params.userStoryIds,
      type: 'user_stories'
    }, true);
    
    const deletedIds = params.userStoryIds.filter((id) => 
      !response.data.data.failed || !response.data.data.failed.some((f: any) => f.id === id)
    );
    
    return {
      deletedIds,
      failedDeletions: response.data.data.failed || []
    };
  } catch (err: any) {
    let errorMessage = 'Failed to delete user stories.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'User stories not found.';
    } else if (err.response?.status === 405) {
      errorMessage = 'Delete operation not allowed. Please check your permissions.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.response?.data?.error || err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

// Async thunk for bulk deleting work items
export const deleteWorkItems = createAsyncThunk<
  { deletedCount: number; userStoryId: string },
  { workItemIds: string[]; userStoryId: string; projectId?: string },
  { rejectValue: string }
>('userStories/deleteWorkItems', async (params, { rejectWithValue }) => {
    try {
      // Use PUT method for updating user story (removing work items)
      const response = await apiRequest('put', '/insights/user-stories/remove-work-items/', {
        ids: params.workItemIds,
        user_story_id: params.userStoryId,
        project_id: params.projectId
      }, true);
    
    return {
      deletedCount: response.data.data.deleted || 0,
      userStoryId: params.userStoryId
    };
  } catch (err: any) {
    let errorMessage = 'Failed to delete work items.';
    if (err.response?.status === 401) {
      errorMessage = 'Authentication required. Please login again.';
    } else if (err.response?.status === 404) {
      errorMessage = 'Work items not found.';
    } else if (err.response?.status >= 500) {
      errorMessage = 'Server error. Please try again later.';
    } else if (err.message) {
      errorMessage = err.response?.data?.error || err.message;
    }
    return rejectWithValue(errorMessage);
  }
});

const userStoriesSlice = createSlice({
  name: 'userStories',
  initialState,
  reducers: {
    clearUserStories: (state) => {
      state.userStories = [];
      state.currentProjectUserStories = [];
      state.error = null;
      state.lastFetchedProjectId = null;
      state.lastFetchedUserId = null;
    },
    clearCurrentProjectUserStories: (state) => {
      state.currentProjectUserStories = [];
      state.lastFetchedProjectId = null;
    },
    clearError: (state) => {
      state.error = null;
    },
    setUserStories: (state, action: PayloadAction<UserStory[]>) => {
      state.userStories = action.payload;
    },
    setCurrentProjectUserStories: (state, action: PayloadAction<UserStory[]>) => {
      state.currentProjectUserStories = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      // Fetch user stories by project
      .addCase(fetchUserStoriesByProject.pending, (state) => {
        state.loading = true;
        state.error = null;
        state.currentProjectUserStories = [];
      })
      .addCase(fetchUserStoriesByProject.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        state.currentProjectUserStories = action.payload.userStories;
        state.lastFetchedProjectId = action.payload.projectId;
        state.lastFetchedUserId = action.payload.userId;

        // Update the main userStories array with the latest data
        const updatedUserStories = [...state.userStories];
        action.payload.userStories.forEach((newStory: UserStory) => {
          const existingIndex = updatedUserStories.findIndex(
            (story: UserStory) => story.id === newStory.id
          );
          if (existingIndex >= 0) {
            updatedUserStories[existingIndex] = newStory;
          } else {
            updatedUserStories.push(newStory);
          }
        });
        state.userStories = updatedUserStories;
      })
      .addCase(fetchUserStoriesByProject.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to load user stories.';
      })

      // Update user story
      .addCase(updateUserStory.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(updateUserStory.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;

        // Update the user story in both arrays
        const updatedStory = action.payload.userStory;
        const storyId = action.payload.userStoryId;

        // Update in userStories array
        const userStoriesIndex = state.userStories.findIndex((story: UserStory) => story.id === storyId);
        if (userStoriesIndex >= 0) {
          state.userStories[userStoriesIndex] = updatedStory;
        }

        // Update in currentProjectUserStories array if it exists there
        const projectStoriesIndex = state.currentProjectUserStories.findIndex((story: UserStory) => story.id === storyId);
        if (projectStoriesIndex >= 0) {
          state.currentProjectUserStories[projectStoriesIndex] = updatedStory;
        }
      })
      .addCase(updateUserStory.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to update user story.';
      })

      // Delete user story
      .addCase(deleteUserStory.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(deleteUserStory.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;

        const deletedId = action.payload.userStoryId;

        // Remove from userStories array
        state.userStories = state.userStories.filter((story: UserStory) => story.id !== deletedId);

        // Remove from currentProjectUserStories array
        state.currentProjectUserStories = state.currentProjectUserStories.filter((story: UserStory) => story.id !== deletedId);
      })
      .addCase(deleteUserStory.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to delete user story.';
      })

      // Bulk delete user stories
      .addCase(deleteUserStories.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(deleteUserStories.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;

        const deletedIds = action.payload.deletedIds;

        // Remove from userStories array
        state.userStories = state.userStories.filter((story: UserStory) => !deletedIds.includes(story.id));

        // Remove from currentProjectUserStories array
        state.currentProjectUserStories = state.currentProjectUserStories.filter((story: UserStory) => !deletedIds.includes(story.id));

        // Handle partial failures
        if (action.payload.failedDeletions && action.payload.failedDeletions.length > 0) {
          const failedIds = action.payload.failedDeletions.map(f => f.id);
          state.error = `Some user stories could not be deleted: ${failedIds.join(', ')}`;
        }
      })
      .addCase(deleteUserStories.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to delete user stories.';
      })

      // Bulk delete work items
      .addCase(deleteWorkItems.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(deleteWorkItems.fulfilled, (state, action) => {
        state.loading = false;
        state.error = null;
        
        // Update the current project user stories by removing the deleted work items
        const { deletedCount, userStoryId } = action.payload;
        
        state.currentProjectUserStories = state.currentProjectUserStories.map(userStory => {
          if (userStory.id === userStoryId) {
            // Remove the deleted work items and update summary
            const updatedWorkItems = userStory.work_items?.filter(item => 
              !action.meta.arg.workItemIds.includes(item.id)
            ) || [];
            
            // Update summary counts
            const updatedSummary = {
              ...userStory.summary,
              totalitems: updatedWorkItems.length,
              bytype: {} as Record<string, number>,
              bypriority: {} as Record<string, number>
            };
            
            // Recalculate type and priority counts
            updatedWorkItems.forEach(item => {
              const type = item.type || 'Unknown';
              const priority = item.priority || 'Unknown';
              
              updatedSummary.bytype[type] = (updatedSummary.bytype[type] || 0) + 1;
              updatedSummary.bypriority[priority] = (updatedSummary.bypriority[priority] || 0) + 1;
            });
            
            return {
              ...userStory,
              work_items: updatedWorkItems,
              summary: updatedSummary,
              updated_at: new Date().toISOString()
            };
          }
          return userStory;
        });
      })
      .addCase(deleteWorkItems.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload || 'Failed to delete work items.';
      });
  },
});

export const {
  clearUserStories,
  clearCurrentProjectUserStories,
  clearError,
  setUserStories,
  setCurrentProjectUserStories,
} = userStoriesSlice.actions;

export default userStoriesSlice.reducer;
