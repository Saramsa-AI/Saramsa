import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit';
import { apiRequest } from '@/lib/apiRequest';

export interface IntegrationAccount {
  id: string;
  provider: 'azure' | 'jira' | 'slack';
  displayName: string;
  status: 'active' | 'revoked' | 'expired' | 'error';
  metadata: {
    organization?: string;
    domain?: string;
    email?: string;
    baseUrl?: string;
    teamId?: string;
    teamName?: string;
    workspace?: string;
    team?: string;
  };
  scopes: string[];
  savedAt: string;
  expiresAt?: string;
}

export interface ExternalProject {
  id: string;
  name: string;
  description?: string;
  url?: string;
  key?: string;
  templateName?: string;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  num_members: number;
}

export interface FeedbackSource {
  id: string;
  projectId: string;
  provider: string;
  accountId: string;
  config: {
    channels: { id: string; name: string }[];
    sync_frequency: string;
    last_synced_at: string | null;
    last_analysis_status: string | null;
  };
  status: string;
  createdAt: string;
  updatedAt: string;
}

export interface IntegrationsState {
  accounts: IntegrationAccount[];
  externalProjects: ExternalProject[];
  slackChannels: SlackChannel[];
  feedbackSources: FeedbackSource[];
  loading: boolean;
  error: string | null;
  testingConnection: { [accountId: string]: boolean };
  fetchingProjects: { [provider: string]: boolean };
  fetchingChannels: boolean;
  fetchingSources: boolean;
  creatingSource: boolean;
  syncingSource: { [sourceId: string]: boolean };
}

const initialState: IntegrationsState = {
  accounts: [],
  externalProjects: [],
  slackChannels: [],
  feedbackSources: [],
  loading: false,
  error: null,
  testingConnection: {},
  fetchingProjects: {},
  fetchingChannels: false,
  fetchingSources: false,
  creatingSource: false,
  syncingSource: {},
};

// Async thunks
export const fetchIntegrationAccounts = createAsyncThunk(
  'integrations/fetchAccounts',
  async () => {
    const response = await apiRequest('get', '/integrations/', undefined, true);
    return response.data.data.accounts; // StandardResponse format
  }
);

export const createIntegrationAccount = createAsyncThunk(
  'integrations/createAccount',
  async (data: {
    provider: 'azure' | 'jira';
    credentials: any;
    metadata: any;
  }) => {
    const response = await apiRequest('post', `/integrations/${data.provider}/`, {
      credentials: data.credentials,
      metadata: data.metadata,
    }, true);
    return response.data.data.account; // StandardResponse format
  }
);

export const deleteIntegrationAccount = createAsyncThunk(
  'integrations/deleteAccount',
  async (accountId: string) => {
    await apiRequest('delete', `/integrations/${accountId}/`, undefined, true);
    return accountId;
  }
);

export const testIntegrationConnection = createAsyncThunk(
  'integrations/testConnection',
  async (accountId: string) => {
    const response = await apiRequest('get', `/integrations/${accountId}/test/`, undefined, true);
    return { accountId, result: response.data.data }; // StandardResponse format
  }
);

export const fetchExternalProjects = createAsyncThunk(
  'integrations/fetchExternalProjects',
  async (data: { provider: 'azure' | 'jira'; accountId: string }) => {
    const response = await apiRequest('get', `/integrations/external/projects/?provider=${data.provider}&accountId=${data.accountId}`, undefined, true);
    // StandardResponse format: response.data.data
    // Add provider information to each project for the UI
    const projectsWithProvider = response.data.data.projects.map((project: any) => ({
      ...project,
      provider: data.provider
    }));
    return projectsWithProvider;
  }
);

// Fallback thunks for existing API endpoints (temporary compatibility)
export const createAzureIntegration = createAsyncThunk(
  'integrations/createAzureIntegration',
  async (data: { organization: string; pat_token: string }) => {
    // Use new integrations API
    const response = await apiRequest('post', '/integrations/azure/', {
      organization: data.organization,
      pat_token: data.pat_token,
      create_integration: true
    }, true);
    
    // StandardResponse format: response.data.data
    if (response.data.success) {
      return {
        id: response.data.data.account.id,
        provider: 'azure' as const,
        displayName: `${data.organization} (Azure DevOps)`,
        status: 'active' as const,
        metadata: {
          organization: data.organization,
          baseUrl: `https://dev.azure.com/${data.organization}`,
        },
        scopes: ['vso.project', 'vso.code'],
        savedAt: new Date().toISOString(),
      };
    } else {
      throw new Error(response.data.detail || response.data.title || 'Failed to connect to Azure DevOps');
    }
  }
);

export const createJiraIntegration = createAsyncThunk(
  'integrations/createJiraIntegration',
  async (data: { domain: string; email: string; api_token: string }) => {
    // Use new integrations API
    const response = await apiRequest('post', '/integrations/jira/', {
      domain: data.domain,
      email: data.email,
      api_token: data.api_token
    }, true);
    
    // StandardResponse format: response.data.data
    if (response.data.success) {
      return {
        id: response.data.data.account.id,
        provider: 'jira' as const,
        displayName: `${data.domain} (Jira)`,
        status: 'active' as const,
        metadata: {
          domain: data.domain,
          email: data.email,
          baseUrl: `https://${data.domain}.atlassian.net`,
        },
        scopes: ['read:project', 'write:issue'],
        savedAt: new Date().toISOString(),
      };
    } else {
      throw new Error(response.data.detail || response.data.title || 'Failed to connect to Jira');
    }
  }
);

export const fetchAzureProjects = createAsyncThunk(
  'integrations/fetchAzureProjects',
  async (credentials: { organization: string; pat_token: string }) => {
    const response = await apiRequest('post', '/integrations/azure/projects/', credentials, true);
    
    // StandardResponse format: response.data.data
    if (response.data.success) {
      return response.data.data.projects;
    } else {
      throw new Error(response.data.detail || response.data.title || 'Failed to fetch Azure projects');
    }
  }
);

// Slack channel & feedback source thunks
export const fetchSlackChannels = createAsyncThunk(
  'integrations/fetchSlackChannels',
  async (data: { accountId: string }) => {
    const response = await apiRequest('get', `/integrations/slack/channels/?account_id=${data.accountId}`, undefined, true);
    return response.data.data.channels as SlackChannel[];
  }
);

export const fetchFeedbackSources = createAsyncThunk(
  'integrations/fetchFeedbackSources',
  async (data: { projectId: string }) => {
    const response = await apiRequest('get', `/integrations/sources/?project_id=${data.projectId}`, undefined, true);
    return response.data.data.sources as FeedbackSource[];
  }
);

export const createFeedbackSource = createAsyncThunk(
  'integrations/createFeedbackSource',
  async (data: { projectId: string; accountId: string; channels: { id: string; name: string }[]; syncFrequency?: string }) => {
    const response = await apiRequest('post', '/integrations/sources/create/', {
      project_id: data.projectId,
      provider: 'slack',
      account_id: data.accountId,
      channels: data.channels,
      sync_frequency: data.syncFrequency || 'hourly',
    }, true);
    return response.data.data.source as FeedbackSource;
  }
);

export const syncFeedbackSource = createAsyncThunk(
  'integrations/syncFeedbackSource',
  async (data: { sourceId: string }) => {
    const response = await apiRequest('post', `/integrations/sources/${data.sourceId}/sync/`, undefined, true);
    return {
      sourceId: data.sourceId,
      taskId: response.data?.data?.task_id as string | undefined,
    };
  }
);

export const fetchJiraProjects = createAsyncThunk(
  'integrations/fetchJiraProjects',
  async (credentials: { domain: string; email: string; api_token: string }) => {
    const response = await apiRequest('post', '/integrations/jira/projects/', credentials, true);
    
    // StandardResponse format: response.data.data
    if (response.data.success) {
      return response.data.data.projects;
    } else {
      throw new Error(response.data.detail || response.data.title || 'Failed to fetch Jira projects');
    }
  }
);

const integrationsSlice = createSlice({
  name: 'integrations',
  initialState,
  reducers: {
    clearError: (state) => {
      state.error = null;
    },
    setTestingConnection: (state, action: PayloadAction<{ accountId: string; testing: boolean }>) => {
      state.testingConnection[action.payload.accountId] = action.payload.testing;
    },
    clearExternalProjects: (state) => {
      state.externalProjects = [];
    },
    clearSlackChannels: (state) => {
      state.slackChannels = [];
    },
    clearFeedbackSources: (state) => {
      state.feedbackSources = [];
    },
  },
  extraReducers: (builder) => {
    builder
      // Fetch accounts
      .addCase(fetchIntegrationAccounts.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(fetchIntegrationAccounts.fulfilled, (state, action) => {
        state.loading = false;
        state.accounts = action.payload;
      })
      .addCase(fetchIntegrationAccounts.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to fetch integration accounts';
      })
      
      // Create account
      .addCase(createIntegrationAccount.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(createIntegrationAccount.fulfilled, (state, action) => {
        state.loading = false;
        state.accounts.push(action.payload);
      })
      .addCase(createIntegrationAccount.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to create integration account';
      })
      // Delete account
      .addCase(deleteIntegrationAccount.fulfilled, (state, action) => {
        state.accounts = state.accounts.filter(acc => acc.id !== action.payload);
      })
      
      // Test connection
      .addCase(testIntegrationConnection.pending, (state, action) => {
        state.testingConnection[action.meta.arg] = true;
      })
      .addCase(testIntegrationConnection.fulfilled, (state, action) => {
        state.testingConnection[action.payload.accountId] = false;
        const account = state.accounts.find(acc => acc.id === action.payload.accountId);
        if (account) {
          account.status = action.payload.result.success ? 'active' : 'error';
        }
      })
      .addCase(testIntegrationConnection.rejected, (state, action) => {
        const accountId = action.meta.arg;
        state.testingConnection[accountId] = false;
        const account = state.accounts.find(acc => acc.id === accountId);
        if (account) {
          account.status = 'error';
        }
      })
      
      // Fetch external projects
      .addCase(fetchExternalProjects.pending, (state, action) => {
        const provider = action.meta.arg.provider;
        state.fetchingProjects[provider] = true;
        state.error = null;
      })
      .addCase(fetchExternalProjects.fulfilled, (state, action) => {
        const provider = action.meta.arg.provider;
        state.fetchingProjects[provider] = false;
        state.externalProjects = action.payload;
      })
      .addCase(fetchExternalProjects.rejected, (state, action) => {
        const provider = action.meta.arg.provider;
        state.fetchingProjects[provider] = false;
        state.error = action.error.message || 'Failed to fetch external projects';
      })
      
      // Azure integration (fallback)
      .addCase(createAzureIntegration.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(createAzureIntegration.fulfilled, (state, action) => {
        state.loading = false;
        // Remove existing Azure account if any
        state.accounts = state.accounts.filter(acc => acc.provider !== 'azure');
        state.accounts.push(action.payload);
      })
      .addCase(createAzureIntegration.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to create Azure integration';
      })
      
      // Jira integration (fallback)
      .addCase(createJiraIntegration.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(createJiraIntegration.fulfilled, (state, action) => {
        state.loading = false;
        // Remove existing Jira account if any
        state.accounts = state.accounts.filter(acc => acc.provider !== 'jira');
        state.accounts.push(action.payload);
      })
      .addCase(createJiraIntegration.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to create Jira integration';
      })
      
      // Fetch Azure projects
      .addCase(fetchAzureProjects.pending, (state) => {
        state.fetchingProjects['azure'] = true;
        state.error = null;
      })
      .addCase(fetchAzureProjects.fulfilled, (state, action) => {
        state.fetchingProjects['azure'] = false;
        state.externalProjects = action.payload;
      })
      .addCase(fetchAzureProjects.rejected, (state, action) => {
        state.fetchingProjects['azure'] = false;
        state.error = action.error.message || 'Failed to fetch Azure projects';
      })
      
      // Fetch Jira projects
      .addCase(fetchJiraProjects.pending, (state) => {
        state.fetchingProjects['jira'] = true;
        state.error = null;
      })
      .addCase(fetchJiraProjects.fulfilled, (state, action) => {
        state.fetchingProjects['jira'] = false;
        state.externalProjects = action.payload;
      })
      .addCase(fetchJiraProjects.rejected, (state, action) => {
        state.fetchingProjects['jira'] = false;
        state.error = action.error.message || 'Failed to fetch Jira projects';
      })

      // Slack channels
      .addCase(fetchSlackChannels.pending, (state) => {
        state.fetchingChannels = true;
      })
      .addCase(fetchSlackChannels.fulfilled, (state, action) => {
        state.fetchingChannels = false;
        state.slackChannels = action.payload;
      })
      .addCase(fetchSlackChannels.rejected, (state, action) => {
        state.fetchingChannels = false;
        state.error = action.error.message || 'Failed to fetch Slack channels';
      })

      // Feedback sources
      .addCase(fetchFeedbackSources.pending, (state) => {
        state.fetchingSources = true;
      })
      .addCase(fetchFeedbackSources.fulfilled, (state, action) => {
        state.fetchingSources = false;
        state.feedbackSources = action.payload;
      })
      .addCase(fetchFeedbackSources.rejected, (state, action) => {
        state.fetchingSources = false;
        state.error = action.error.message || 'Failed to fetch feedback sources';
      })

      // Create feedback source
      .addCase(createFeedbackSource.pending, (state) => {
        state.creatingSource = true;
      })
      .addCase(createFeedbackSource.fulfilled, (state, action) => {
        state.creatingSource = false;
        state.feedbackSources.push(action.payload);
      })
      .addCase(createFeedbackSource.rejected, (state, action) => {
        state.creatingSource = false;
        state.error = action.error.message || 'Failed to create feedback source';
      })

      // Sync feedback source
      .addCase(syncFeedbackSource.pending, (state, action) => {
        state.syncingSource[action.meta.arg.sourceId] = true;
      })
      .addCase(syncFeedbackSource.fulfilled, (state, action) => {
        state.syncingSource[action.payload.sourceId] = false;
      })
      .addCase(syncFeedbackSource.rejected, (state, action) => {
        state.syncingSource[action.meta.arg.sourceId] = false;
        state.error = action.error.message || 'Failed to sync feedback source';
      });
  },
});

export const { clearError, setTestingConnection, clearExternalProjects, clearSlackChannels, clearFeedbackSources } = integrationsSlice.actions;
export default integrationsSlice.reducer;
