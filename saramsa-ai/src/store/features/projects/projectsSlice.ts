import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';

export interface ProjectExternalLink {
  provider: 'azure' | 'jira';
  integrationAccountId: string;
  externalId: string;
  url: string;
  status: 'ok' | 'degraded' | 'error';
  lastSyncedAt?: string;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  userId: string;
  createdAt: string;
  updatedAt: string;
  externalLinks: ProjectExternalLink[];
  status?: string;
  type?: string;
  metadata?: {
    totalComments?: number;
    lastAnalysisAt?: string;
    analysisStatus?: 'pending' | 'completed' | 'error';
  };
}

export interface ProjectsState {
  projects: Project[];
  currentProject: Project | null;
  loading: boolean;
  error: string | null;
  importing: boolean;
  importError: string | null;
}

const initialState: ProjectsState = {
  projects: [],
  currentProject: null,
  loading: false,
  error: null,
  importing: false,
  importError: null,
};

// Async thunks
export const fetchProjects = createAsyncThunk(
  'projects/fetchProjects',
  async () => {
    const response = await apiRequest('get', '/integrations/projects/', undefined, true);
    if (response.data.success) {
      return response.data.data.projects;
    } else {
      throw new Error(response.data.error || 'Failed to fetch projects');
    }
  }
);

export const createProject = createAsyncThunk(
  'projects/createProject',
  async (data: { 
    name: string; 
    description?: string;
    externalLinks?: Array<{
      provider: string;
      integrationAccountId: string;
      externalId: string;
      externalKey?: string;
      url?: string;
      status: string;
      lastSyncedAt: string | null;
      syncMetadata: any;
    }>;
  }, { rejectWithValue }) => {
    try {
      const payload: any = {
        project_name: data.name,
        description: data.description,
      };

      // If external links provided, use the first one to set platform and integration details
      if (data.externalLinks && data.externalLinks.length > 0) {
        const link = data.externalLinks[0];
        payload.platform = link.provider === 'azure' ? 'azure_devops' : 'jira';
        payload.external_project_id = link.externalId;
        payload.integration_account_id = link.integrationAccountId;
        payload.external_url = link.url;
        if (link.externalKey) {
          payload.jira_project_key = link.externalKey;
        }
      } else {
        payload.platform = 'standalone';
      }

      const response = await apiRequest('post', '/integrations/projects/create/', payload, true);
      
      if (!response.data.success) {
        return rejectWithValue(response.data.error || 'Failed to create project');
      }
      
      return response.data.data;
    } catch (error: any) {
      const errorMessage = error?.response?.data?.error || error?.message || 'Failed to create project';
      return rejectWithValue(errorMessage);
    }
  }
);

export const importProjectFromExternal = createAsyncThunk(
  'projects/importFromExternal',
  async (data: {
    provider: 'azure' | 'jira';
    integrationAccountId: string;
    externalProject: {
      id: string;
      name: string;
      description?: string;
      url?: string;
      key?: string; // For Jira
      templateName?: string; // For Azure DevOps
    };
  }) => {
    // First check if project already exists with this external link
    const checkResponse = await apiRequest('get', `/integrations/projects/check-external/?provider=${data.provider}&externalId=${data.externalProject.id}`, undefined, true);
    
    if (checkResponse.data.data.exists) {
      throw new Error(`Project "${data.externalProject.name}" is already imported`);
    }
    
    // Create new project with external link
    const createData: any = {
      project_name: data.externalProject.name,
      description: data.externalProject.description,
      platform: data.provider === 'azure' ? 'azure_devops' : 'jira',
      external_project_id: data.externalProject.id,
      integration_account_id: data.integrationAccountId,
    };
    
    // Add provider-specific fields
    if (data.provider === 'azure') {
      const organization = typeof window !== 'undefined' ? localStorage.getItem('azure_organization') : null;
      createData.organization = organization;
      createData.azure_project_name = data.externalProject.name;
      createData.azure_process_template = data.externalProject.templateName;
    } else if (data.provider === 'jira') {
      const jira_domain = typeof window !== 'undefined' ? localStorage.getItem('jira_domain') : null;
      const jira_email = typeof window !== 'undefined' ? localStorage.getItem('jira_email') : null;
      createData.jira_domain = jira_domain;
      createData.jira_email = jira_email;
      createData.jira_project_key = data.externalProject.key;
    }
    
    const response = await apiRequest('post', '/integrations/projects/create/', createData, true);
    return response.data.data;
  }
);

export const updateProject = createAsyncThunk(
  'projects/updateProject',
  async (data: { id: string; name: string; description?: string }) => {
    const response = await apiRequest('patch', `/integrations/projects/${data.id}/`, {
      project_name: data.name,
      description: data.description,
    }, true);
    
    if (response.data.success) {
      return response.data.data;
    } else {
      throw new Error(response.data.error || 'Failed to update project');
    }
  }
);

export const deleteProject = createAsyncThunk(
  'projects/deleteProject',
  async (projectId: string) => {
    await apiRequest('delete', `/integrations/projects/${projectId}/`, undefined, true);
    return projectId;
  }
);

export const syncProjectWithExternal = createAsyncThunk(
  'projects/syncWithExternal',
  async (data: { projectId: string; provider: 'azure' | 'jira' }) => {
    const response = await apiRequest('post', `/integrations/projects/${data.projectId}/sync`, {
      provider: data.provider,
    }, true);
    return response.data;
  }
);

// Fallback thunk for existing project creation (temporary compatibility)
export const createProjectLegacy = createAsyncThunk(
  'projects/createProjectLegacy',
  async (data: {
    name: string;
    platform: 'azure_devops' | 'jira' | 'standalone';
    externalProjectId?: string;
    metadata?: any;
  }) => {
    // Use existing project creation endpoint
    const response = await apiRequest('post', '/integrations/projects/create/', {
      project_name: data.name,
      platform: data.platform,
      external_project_id: data.externalProjectId,
      ...data.metadata,
    }, true);
    
    const project = response.data.data;
    
    // Transform to our Project interface
    return {
      id: project.id,
      name: project.name || data.name,
      description: project.description,
      userId: project.userId || 'current_user',
      createdAt: project.createdAt || new Date().toISOString(),
      updatedAt: project.updatedAt || new Date().toISOString(),
      externalLinks: data.externalProjectId ? [{
        provider: data.platform === 'azure_devops' ? 'azure' as const : 'jira' as const,
        integrationAccountId: 'legacy',
        externalId: data.externalProjectId,
        url: project.external_url || '',
        status: 'ok' as const,
        lastSyncedAt: new Date().toISOString(),
      }] : [],
      metadata: {
        totalComments: 0,
        analysisStatus: 'pending' as const,
      },
    } as Project;
  }
);

const projectsSlice = createSlice({
  name: 'projects',
  initialState,
  reducers: {
    setCurrentProject: (state, action: PayloadAction<Project | null>) => {
      state.currentProject = action.payload;
    },
    clearError: (state) => {
      state.error = null;
      state.importError = null;
    },
    updateProjectMetadata: (state, action: PayloadAction<{ projectId: string; metadata: any }>) => {
      const project = state.projects.find(p => p.id === action.payload.projectId);
      if (project) {
        project.metadata = { ...project.metadata, ...action.payload.metadata };
      }
      if (state.currentProject?.id === action.payload.projectId) {
        state.currentProject.metadata = { ...state.currentProject.metadata, ...action.payload.metadata };
      }
    },
  },
  extraReducers: (builder) => {
    builder
      // Fetch projects
      .addCase(fetchProjects.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(fetchProjects.fulfilled, (state, action) => {
        state.loading = false;
        state.projects = action.payload;
      })
      .addCase(fetchProjects.rejected, (state, action) => {
        state.loading = false;
        state.error = (action.payload as string) || action.error?.message || 'Failed to fetch projects';
      })
      
      // Create project
      .addCase(createProject.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(createProject.fulfilled, (state, action) => {
        state.loading = false;
        // Safety check: ensure payload exists and has an id
        if (action.payload && action.payload.id) {
          // Check if project already exists before adding
          const existingIndex = state.projects.findIndex(p => p.id === action.payload.id);
          if (existingIndex === -1) {
            state.projects.push(action.payload);
          } else {
            // Update existing project
            state.projects[existingIndex] = action.payload;
          }
        } else {
          console.error('createProject fulfilled but payload is invalid:', action.payload);
          state.error = 'Project created but data is invalid';
        }
      })
      .addCase(createProject.rejected, (state, action) => {
        state.loading = false;
        state.error = (action.payload as string) || action.error?.message || 'Failed to create project';
      })
      
      // Import project from external
      .addCase(importProjectFromExternal.pending, (state) => {
        state.importing = true;
        state.importError = null;
      })
      .addCase(importProjectFromExternal.fulfilled, (state, action) => {
        state.importing = false;
        // Safety check: ensure payload exists and has an id
        if (action.payload && action.payload.id) {
          // Check if project already exists before adding
          const existingIndex = state.projects.findIndex(p => p.id === action.payload.id);
          if (existingIndex === -1) {
            state.projects.push(action.payload);
          } else {
            // Update existing project
            state.projects[existingIndex] = action.payload;
          }
        } else {
          console.error('importProjectFromExternal fulfilled but payload is invalid:', action.payload);
          state.importError = 'Project imported but data is invalid';
        }
      })
      .addCase(importProjectFromExternal.rejected, (state, action) => {
        state.importing = false;
        state.importError = action.error.message || 'Failed to import project';
      })
      
      // Update project
      .addCase(updateProject.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(updateProject.fulfilled, (state, action) => {
        state.loading = false;
        // Safety check: ensure payload exists and has an id
        if (action.payload && action.payload.id) {
          const index = state.projects.findIndex(p => p.id === action.payload.id);
          if (index !== -1) {
            state.projects[index] = action.payload;
          }
          if (state.currentProject?.id === action.payload.id) {
            state.currentProject = action.payload;
          }
        } else {
          console.error('updateProject fulfilled but payload is invalid:', action.payload);
          state.error = 'Project updated but data is invalid';
        }
      })
      .addCase(updateProject.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to update project';
      })
      
      // Delete project
      .addCase(deleteProject.fulfilled, (state, action) => {
        state.projects = state.projects.filter(p => p.id !== action.payload);
        if (state.currentProject?.id === action.payload) {
          state.currentProject = null;
        }
      })
      
      // Sync with external
      .addCase(syncProjectWithExternal.fulfilled, (state, action) => {
        const project = state.projects.find(p => p.id === action.meta.arg.projectId);
        if (project) {
          const link = project.externalLinks.find((l: ProjectExternalLink) => l.provider === action.meta.arg.provider);
          if (link) {
            link.status = 'ok';
            link.lastSyncedAt = new Date().toISOString();
          }
        }
      })
      
      // Legacy project creation
      .addCase(createProjectLegacy.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(createProjectLegacy.fulfilled, (state, action) => {
        state.loading = false;
        // Safety check: ensure payload exists and has an id
        if (action.payload && action.payload.id) {
          state.projects.push(action.payload);
          state.currentProject = action.payload;
        } else {
          console.error('createProjectLegacy fulfilled but payload is invalid:', action.payload);
          state.error = 'Project created but data is invalid';
        }
      })
      .addCase(createProjectLegacy.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to create project';
      });
  },
});

export const { setCurrentProject, clearError, updateProjectMetadata } = projectsSlice.actions;
export default projectsSlice.reducer;
