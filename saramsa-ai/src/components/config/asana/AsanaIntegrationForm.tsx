'use client';

import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
import { fetchProjects, createProject } from '@/store/features/projects/projectsSlice';
import { fetchIntegrationAccounts } from '@/store/features/integrations/integrationsSlice';
import { apiRequest } from '@/lib/apiRequest';
import { AlertCircle, CheckCircle, ExternalLink, Eye, EyeOff, Loader2 } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

type AsanaWorkspace = {
  gid: string;
  name: string;
};

type AsanaProject = {
  id: string;
  name: string;
  description?: string;
  url?: string;
};

interface AsanaIntegrationFormProps {
  onContinue: (projectId: string) => void;
  onBack: () => void;
  targetProjectId?: string;
}

function handleProjectCardKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  onSelect: () => void
) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    onSelect();
  }
}

export function AsanaIntegrationForm({ onContinue, onBack, targetProjectId }: AsanaIntegrationFormProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const { projects: saramsaProjects } = useSelector((state: RootState) => state.projects);
  const normalizedTargetProjectId = targetProjectId?.startsWith('project_')
    ? targetProjectId.replace('project_', '')
    : targetProjectId;
  const currentSaramsaProject = normalizedTargetProjectId
    ? saramsaProjects?.find((project) => project.id === normalizedTargetProjectId)
    : null;

  const [patToken, setPatToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [workspaces, setWorkspaces] = useState<AsanaWorkspace[]>([]);
  const [selectedWorkspaceGid, setSelectedWorkspaceGid] = useState('');
  const [selectedWorkspaceName, setSelectedWorkspaceName] = useState('');
  const [availableProjects, setAvailableProjects] = useState<AsanaProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [isExistingIntegration, setIsExistingIntegration] = useState(false);

  const linkedProjects: Record<string, { id: string; name: string }> = {};
  saramsaProjects?.forEach((project) => {
    project.externalLinks?.forEach((link) => {
      if (link.provider === 'asana') {
        linkedProjects[link.externalId] = { id: project.id, name: project.name };
      }
    });
  });

  const asanaAccount = useMemo(
    () => accounts.find((acc) => acc.provider === 'asana' && acc.status === 'active'),
    [accounts]
  );

  useEffect(() => {
    dispatch(fetchProjects());
    dispatch(fetchIntegrationAccounts());
  }, [dispatch]);

  useEffect(() => {
    if (!asanaAccount) {
      return;
    }

    setIsExistingIntegration(true);
    const workspaceGid = asanaAccount.metadata.workspaceGid || '';
    const workspaceName = asanaAccount.metadata.workspaceName || '';
    setSelectedWorkspaceGid(workspaceGid);
    setSelectedWorkspaceName(workspaceName);
    setWorkspaces(workspaceGid ? [{ gid: workspaceGid, name: workspaceName || 'Selected workspace' }] : []);
    void fetchProjectsForExistingIntegration(asanaAccount.id);
  }, [asanaAccount]);

  const selectedProject = availableProjects.find((project) => project.id === selectedProjectId) || null;

  const ensureAsanaProjectSetup = async (saramsaProjectId: string, asanaProjectGid: string) => {
    const normalizedProjectId = saramsaProjectId.startsWith('project_')
      ? saramsaProjectId.replace('project_', '')
      : saramsaProjectId;

    await apiRequest(
      'post',
      `/integrations/asana/projects/${normalizedProjectId}/target/`,
      { asana_project_gid: asanaProjectGid },
      true
    );
  };

  const fetchProjectsForExistingIntegration = async (accountId: string) => {
    setLoadingProjects(true);
    setError('');
    try {
      const response = await apiRequest(
        'get',
        `/integrations/external/projects/?provider=asana&accountId=${accountId}`,
        undefined,
        true
      );
      if (!response.data.success) {
        throw new Error(response.data.detail || 'Failed to fetch Asana projects');
      }
      setAvailableProjects(response.data.data.projects || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to fetch Asana projects');
    } finally {
      setLoadingProjects(false);
    }
  };

  const handleLoadWorkspaces = async () => {
    if (!patToken.trim()) {
      setError('Please enter your Asana PAT');
      return;
    }

    setLoadingWorkspaces(true);
    setAvailableProjects([]);
    setSelectedProjectId('');
    setError('');

    try {
      const response = await apiRequest(
        'post',
        '/integrations/asana/workspaces/',
        { pat_token: patToken.trim() },
        true
      );
      if (!response.data.success) {
        throw new Error(response.data.detail || 'Failed to fetch Asana workspaces');
      }
      const fetchedWorkspaces = response.data.data.workspaces || [];
      setWorkspaces(fetchedWorkspaces);
      if (fetchedWorkspaces.length > 0) {
        setSelectedWorkspaceGid(fetchedWorkspaces[0].gid);
        setSelectedWorkspaceName(fetchedWorkspaces[0].name);
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to fetch Asana workspaces');
    } finally {
      setLoadingWorkspaces(false);
    }
  };

  const handleLoadProjects = async () => {
    if (!selectedWorkspaceGid) {
      setError('Please select a workspace');
      return;
    }
    if (!isExistingIntegration && !patToken.trim()) {
      setError('Please enter your Asana PAT');
      return;
    }

    setLoadingProjects(true);
    setSelectedProjectId('');
    setError('');

    try {
      const response = await apiRequest(
        'post',
        '/integrations/asana/projects/',
        {
          pat_token: patToken.trim(),
          workspace_gid: selectedWorkspaceGid,
        },
        true
      );
      if (!response.data.success) {
        throw new Error(response.data.detail || 'Failed to fetch Asana projects');
      }
      setAvailableProjects(response.data.data.projects || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to fetch Asana projects');
    } finally {
      setLoadingProjects(false);
    }
  };

  const handleWorkspaceChange = (workspaceGid: string) => {
    setSelectedWorkspaceGid(workspaceGid);
    const workspace = workspaces.find((item) => item.gid === workspaceGid);
    setSelectedWorkspaceName(workspace?.name || '');
    setAvailableProjects([]);
    setSelectedProjectId('');
    setError('');
  };

  const handleContinue = async () => {
    if (!selectedProject) {
      setError('Please select an Asana project');
      return;
    }

    setCreatingProject(true);
    setError('');
    setWarning('');

    try {
      let integrationAccountId = asanaAccount?.id || '';

      if (!integrationAccountId) {
        const integrationResponse = await apiRequest(
          'post',
          '/integrations/asana/',
          {
            pat_token: patToken.trim(),
            workspace_gid: selectedWorkspaceGid,
            workspace_name: selectedWorkspaceName,
          },
          true
        );
        if (!integrationResponse.data.success) {
          throw new Error(integrationResponse.data.detail || 'Failed to create Asana integration');
        }
        integrationAccountId = integrationResponse.data.data.account.id;
        await dispatch(fetchIntegrationAccounts());
      }

      if (currentSaramsaProject && normalizedTargetProjectId) {
        const asanaExternalLink = {
          provider: 'asana',
          integrationAccountId,
          externalId: selectedProject.id,
          url: selectedProject.url,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {
            workspaceGid: selectedWorkspaceGid,
            workspaceName: selectedWorkspaceName,
          },
        };

        const nextExternalLinks = [
          ...(currentSaramsaProject.externalLinks || []).filter((link) => link.provider !== 'asana'),
          asanaExternalLink,
        ];

        await apiRequest(
          'patch',
          `/integrations/projects/${normalizedTargetProjectId}/`,
          { externalLinks: nextExternalLinks },
          true
        );
        await dispatch(fetchProjects());
        await ensureAsanaProjectSetup(normalizedTargetProjectId, selectedProject.id);

        onContinue(normalizedTargetProjectId);
        return;
      }

      try {
        const checkResponse = await apiRequest(
          'get',
          `/integrations/external/projects/check/?provider=asana&externalId=${selectedProject.id}`,
          undefined,
          true
        );
        if (checkResponse.data.data.exists && checkResponse.data.data.project) {
          const existingProject = checkResponse.data.data.project;
          await ensureAsanaProjectSetup(existingProject.id, selectedProject.id);
          onContinue(existingProject.id);
          return;
        }
      } catch (checkError: any) {
        throw new Error(
          checkError?.response?.data?.detail ||
          checkError?.message ||
          'Failed to verify whether this Asana project is already linked.'
        );
      }

      const result = await dispatch(
        createProject({
          name: selectedProject.name,
          description: selectedProject.description || `Imported from Asana: ${selectedWorkspaceName}`,
          externalLinks: [
            {
              provider: 'asana',
              integrationAccountId,
              externalId: selectedProject.id,
              url: selectedProject.url,
              status: 'ok',
              lastSyncedAt: null,
              syncMetadata: {
                workspaceGid: selectedWorkspaceGid,
                workspaceName: selectedWorkspaceName,
              },
            },
          ],
        })
      ).unwrap();

      const setupWarning = result?.asanaSetupWarning || result?.asanaWebhookWarning;
      if (setupWarning) {
        setWarning(`Project created, but Asana automation setup is incomplete: ${setupWarning}`);
      }

      await ensureAsanaProjectSetup(result.id, selectedProject.id);

      onContinue(result.id);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to create Asana project');
    } finally {
      setCreatingProject(false);
    }
  };

  return (
    <div className="space-y-6 rounded-3xl border border-border/60 bg-card/80 p-6 shadow-sm">
      <div className="space-y-2">
        <h3 className="text-2xl font-semibold text-foreground">Connect Asana</h3>
        <p className="text-sm text-muted-foreground">
          Link one Asana workspace, choose a project, and import it into Saramsa for feedback-driven task sync.
        </p>
      </div>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
          <div className="flex items-center gap-2 text-sm text-red-700 dark:text-red-300">
            <AlertCircle className="h-4 w-4" />
            <span>{error}</span>
          </div>
        </div>
      )}

      {warning && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-900/20">
          <div className="flex items-center gap-2 text-sm text-amber-800 dark:text-amber-300">
            <AlertCircle className="h-4 w-4" />
            <span>{warning}</span>
          </div>
        </div>
      )}

      {isExistingIntegration ? (
        <div className="rounded-2xl border border-green-200 bg-green-50 p-4 dark:border-green-800 dark:bg-green-900/20">
          <div className="flex items-start gap-3">
            <CheckCircle className="mt-0.5 h-5 w-5 text-green-600 dark:text-green-400" />
            <div>
              <p className="text-sm font-medium text-foreground">Existing Asana workspace detected</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {selectedWorkspaceName || 'Configured workspace'} is already connected. Choose a project below to import or reopen.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <label className="mb-2 block text-sm font-medium text-muted-foreground">Asana Personal Access Token</label>
            <div className="relative">
              <Input
                type={showToken ? 'text' : 'password'}
                value={patToken}
                onChange={(event) => setPatToken(event.target.value)}
                placeholder="Enter your Asana PAT"
                className="pr-12"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setShowToken((current) => !current)}
                className="absolute right-2 top-1/2 h-8 w-8 -translate-y-1/2"
              >
                {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Your PAT is encrypted before storage. Saramsa uses it only to list workspaces, import projects, and sync tasks.
            </p>
          </div>

          <div className="flex justify-start">
            <Button type="button" variant="outline" onClick={handleLoadWorkspaces} disabled={loadingWorkspaces}>
              {loadingWorkspaces ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Loading workspaces...
                </>
              ) : (
                'Load Workspaces'
              )}
            </Button>
          </div>

          {workspaces.length > 0 && (
            <div>
              <label className="mb-2 block text-sm font-medium text-muted-foreground">Workspace</label>
              <select
                value={selectedWorkspaceGid}
                onChange={(event) => handleWorkspaceChange(event.target.value)}
                className="w-full rounded-xl border border-border/60 bg-background/80 px-3 py-3 text-foreground"
              >
                {workspaces.map((workspace) => (
                  <option key={workspace.gid} value={workspace.gid}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {selectedWorkspaceGid && (
            <div className="flex justify-start">
              <Button type="button" variant="outline" onClick={handleLoadProjects} disabled={loadingProjects}>
                {loadingProjects ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading projects...
                  </>
                ) : (
                  'Load Projects'
                )}
              </Button>
            </div>
          )}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-medium text-foreground">Asana Projects</h4>
            <p className="text-sm text-muted-foreground">
              Choose the project Saramsa should attach to this workspace.
            </p>
          </div>
          {selectedProject && linkedProjects[selectedProject.id] && (
            <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
              Already linked to {linkedProjects[selectedProject.id].name}
            </span>
          )}
        </div>

        {loadingProjects ? (
          <div className="flex items-center justify-center rounded-2xl border border-border/60 bg-secondary/30 py-10">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : availableProjects.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border/70 bg-secondary/20 p-6 text-sm text-muted-foreground">
            {isExistingIntegration ? 'No Asana projects found for the connected workspace yet.' : 'Load a workspace and fetch its projects to continue.'}
          </div>
        ) : (
          <div className="space-y-3">
            {availableProjects.map((project) => {
              const linkedProject = linkedProjects[project.id];
              const isSelected = selectedProjectId === project.id;
              const isLinked = Boolean(linkedProject);
              return (
                <div
                  key={project.id}
                  onClick={() => setSelectedProjectId(project.id)}
                  onKeyDown={(event) =>
                    handleProjectCardKeyDown(event, () => setSelectedProjectId(project.id))
                  }
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  className={`w-full rounded-2xl border p-4 text-left transition ${
                    isSelected
                      ? 'border-saramsa-brand/60 bg-saramsa-brand/10'
                      : 'border-border/60 bg-card/60 hover:border-saramsa-brand/30'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-medium text-foreground">{project.name}</p>
                        {isLinked && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
                            Linked
                          </span>
                        )}
                      </div>
                      {project.description && (
                        <p className="mt-1 text-sm text-muted-foreground">{project.description}</p>
                      )}
                      {isLinked && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          Already imported as {linkedProject?.name}
                        </p>
                      )}
                    </div>
                    {project.url ? (
                      <a
                        href={project.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(event) => event.stopPropagation()}
                        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                      >
                        <ExternalLink className="h-4 w-4" />
                        Open
                      </a>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-border/60 pt-4">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <Button
          type="button"
          variant="saramsa"
          onClick={handleContinue}
          disabled={!selectedProject || creatingProject}
        >
          {creatingProject ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Importing...
            </>
          ) : (
            'Continue'
          )}
        </Button>
      </div>
    </div>
  );
}
