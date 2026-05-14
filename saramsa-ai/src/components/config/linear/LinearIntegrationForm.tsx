'use client';

import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from "@/store/store";
import { fetchProjects, createProject } from "@/store/features/projects/projectsSlice";
import { LinearFormPanel } from './LinearFormPanel';
import { apiRequest } from '@/lib/apiRequest';

interface LinearIntegrationFormProps {
  onContinue: (projectId: string) => void;
  onBack: () => void;
  targetProjectId?: string;
}

export function LinearIntegrationForm({ onContinue, onBack, targetProjectId }: LinearIntegrationFormProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const { projects: saramsaProjects } = useSelector((state: RootState) => state.projects);

  const [config, setConfig] = useState({
    apiKey: '',
    teamId: '',
    teamKey: '',
  });
  const [isLoading, setIsLoading] = useState(false);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [validationStatus, setValidationStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [isExistingIntegration, setIsExistingIntegration] = useState(false);

  const normalizedTargetProjectId = targetProjectId?.startsWith('project_')
    ? targetProjectId.replace('project_', '')
    : targetProjectId;
  const currentSaramsaProject = normalizedTargetProjectId
    ? saramsaProjects?.find(project => project.id === normalizedTargetProjectId)
    : null;

  const linkedProjects: { [key: string]: { id: string; name: string } } = {};
  saramsaProjects?.forEach(project => {
    project.externalLinks?.forEach(link => {
      if (link.provider === 'linear') {
        linkedProjects[link.externalId] = { id: project.id, name: project.name };
      }
    });
  });

  useEffect(() => {
    dispatch(fetchProjects());
  }, [dispatch]);

  useEffect(() => {
    const linearAccount = accounts.find(acc => acc.provider === 'linear');
    if (linearAccount) {
      setIsExistingIntegration(true);
      fetchProjectsForExistingIntegration(linearAccount);
    }
  }, [accounts]);

  const fetchProjectsForExistingIntegration = async (linearAccount: any) => {
    setIsLoading(true);
    setErrorMessage('');
    setValidationStatus('loading');

    try {
      const projectsResponse = await apiRequest('get', `/integrations/external/projects/?provider=linear&accountId=${linearAccount.id}`, undefined, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setValidationStatus('success');
        setErrorMessage('');
      } else {
        setErrorMessage(projectsResponse.data.error || 'Failed to fetch teams');
        setValidationStatus('error');
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setErrorMessage(error.response.data.error);
      } else {
        setErrorMessage('Failed to connect to Linear. Please check your API key.');
      }
      setValidationStatus('error');
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfigChange = (field: string, value: string) => {
    setConfig(prev => ({ ...prev, [field]: value }));
    setValidationStatus('idle');
    setErrorMessage('');
  };

  const handleValidateConfig = async () => {
    if (!config.apiKey) {
      setErrorMessage('Please enter your Linear API key');
      setValidationStatus('error');
      return;
    }

    setIsLoading(true);
    setErrorMessage('');
    setValidationStatus('loading');

    try {
      const projectsResponse = await apiRequest('post', '/integrations/linear/projects/', {
        api_key: config.apiKey,
      }, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setValidationStatus('success');
        setErrorMessage('');
      } else {
        setErrorMessage(projectsResponse.data.error || 'Failed to fetch teams');
        setValidationStatus('error');
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setErrorMessage(error.response.data.error);
      } else {
        setErrorMessage('Failed to connect to Linear. Please check your API key.');
      }
      setValidationStatus('error');
    } finally {
      setIsLoading(false);
    }
  };

  const handleProjectSelect = (projectId: string) => {
    setSelectedProject(projectId);
    const selectedProjectData = projects.find(p => p.id === projectId);
    if (selectedProjectData) {
      setConfig(prev => ({ ...prev, teamId: projectId, teamKey: selectedProjectData.key }));
    }
  };

  const handleContinue = async () => {
    if (!selectedProject) {
      setErrorMessage('Please select a team to continue');
      return;
    }

    const selectedProjectData = projects.find(p => p.id === selectedProject);

    setIsCreatingProject(true);
    setErrorMessage("");

    try {
      let integrationAccountId;

      if (isExistingIntegration) {
        const linearAccount = accounts.find(acc => acc.provider === 'linear');
        if (!linearAccount) throw new Error('Linear integration not found');
        integrationAccountId = linearAccount.id;
      } else {
        const integrationResponse = await apiRequest('post', '/integrations/linear/', {
          api_key: config.apiKey,
        }, true);

        if (!integrationResponse.data.success) {
          throw new Error(integrationResponse.data.error || 'Failed to create Linear integration');
        }
        integrationAccountId = integrationResponse.data.data.account.id;
      }

      // Backend builds the proper https://linear.app/<urlKey>/team/<key>
      // URL using the workspace urlKey it persisted. If for some reason
      // it's missing, fall back to the workspace landing — never the
      // short https://linear.app/team/<key> form, which 404s.
      const teamUrl = selectedProjectData?.url || 'https://linear.app/';

      if (currentSaramsaProject && normalizedTargetProjectId) {
        const linearExternalLink = {
          provider: 'linear' as const,
          integrationAccountId: integrationAccountId,
          externalId: selectedProject,
          externalKey: selectedProjectData?.key,
          url: teamUrl,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {}
        };

        const nextExternalLinks = [
          ...(currentSaramsaProject.externalLinks || []).filter(link => link.provider !== 'linear'),
          linearExternalLink,
        ];

        await apiRequest('patch', `/integrations/projects/${normalizedTargetProjectId}/`, {
          externalLinks: nextExternalLinks,
        }, true);
        await dispatch(fetchProjects());

        onContinue(normalizedTargetProjectId);
        return;
      }

      try {
        const checkResponse = await apiRequest(
          'get',
          `/integrations/external/projects/check/?provider=linear&externalId=${selectedProject}`,
          undefined,
          true
        );

        if (checkResponse.data.data.exists && checkResponse.data.data.project) {
          const existingSaramsaProject = checkResponse.data.data.project;
          onContinue(existingSaramsaProject.id);
          return;
        }
      } catch (checkError: any) {
        const message =
          checkError?.response?.data?.error ||
          checkError?.message ||
          'Failed to verify whether this Linear team is already linked.';
        setErrorMessage(message);
        setValidationStatus('error');
        throw new Error(message);
      }

      const projectData = {
        name: selectedProjectData?.name || 'Linear Team',
        description: `Imported from Linear team ${selectedProjectData?.key || selectedProject}`,
        externalLinks: [{
          provider: 'linear' as const,
          integrationAccountId: integrationAccountId,
          externalId: selectedProject,
          externalKey: selectedProjectData?.key,
          url: teamUrl,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {}
        }]
      };

      const result = await dispatch(createProject(projectData)).unwrap();
      onContinue(result.id);
    } catch (e: any) {
      if (e.response?.status === 409) {
        try {
          const checkResponse = await apiRequest(
            'get',
            `/integrations/external/projects/check/?provider=linear&externalId=${selectedProject}`,
            undefined,
            true
          );
          if (checkResponse.data.data.exists && checkResponse.data.data.project) {
            onContinue(checkResponse.data.data.project.id);
            return;
          }
        } catch (lookupError: any) {
          setErrorMessage(
            lookupError?.response?.data?.error ||
              lookupError?.message ||
              'Failed to resolve the existing Linear team after a conflict.'
          );
          setValidationStatus('error');
          return;
        }
      }
      setErrorMessage(e instanceof Error ? e.message : 'Failed to create project');
      setValidationStatus('error');
    } finally {
      setIsCreatingProject(false);
    }
  };

  return (
    <LinearFormPanel
      config={config}
      onConfigChange={handleConfigChange}
      onValidateConfiguration={handleValidateConfig}
      isLoading={isLoading}
      isCreatingProject={isCreatingProject}
      error={errorMessage}
      projects={projects}
      selectedProject={selectedProject}
      onProjectSelect={handleProjectSelect}
      onContinue={handleContinue}
      onBack={onBack}
      validationStatus={validationStatus}
      isExistingIntegration={isExistingIntegration}
      linkedProjects={linkedProjects}
    />
  );
}
