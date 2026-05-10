'use client';

import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from "@/store/store";
import { fetchProjects, createProject } from "@/store/features/projects/projectsSlice";
import { JiraFormPanel } from './JiraFormPanel';
import { apiRequest } from '@/lib/apiRequest';

interface JiraIntegrationFormProps {
  onContinue: () => void;
  onBack: () => void;
}

export function JiraIntegrationForm({ onContinue, onBack }: JiraIntegrationFormProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const { projects: saramsaProjects } = useSelector((state: RootState) => state.projects);

  const [config, setConfig] = useState({
    email: '',
    apiToken: '',
    domain: '',
    projectKey: ''
  });
  const [isLoading, setIsLoading] = useState(false);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [validationStatus, setValidationStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [isExistingIntegration, setIsExistingIntegration] = useState(false);

  const linkedProjects: { [key: string]: { id: string; name: string } } = {};
  saramsaProjects?.forEach(project => {
    project.externalLinks?.forEach(link => {
      if (link.provider === 'jira') {
        linkedProjects[link.externalId] = { id: project.id, name: project.name };
      }
    });
  });

  useEffect(() => {
    dispatch(fetchProjects());
  }, [dispatch]);

  useEffect(() => {
    const jiraAccount = accounts.find(acc => acc.provider === 'jira');
    if (jiraAccount) {
      setIsExistingIntegration(true);
      setConfig(prev => ({
        ...prev,
        domain: jiraAccount.metadata.domain || '',
        email: jiraAccount.metadata.email || ''
      }));
      fetchProjectsForExistingIntegration(jiraAccount);
    }
  }, [accounts]);

  const fetchProjectsForExistingIntegration = async (jiraAccount: any) => {
    setIsLoading(true);
    setErrorMessage('');
    setValidationStatus('loading');

    try {
      const projectsResponse = await apiRequest('get', `/integrations/external/projects/?provider=jira&accountId=${jiraAccount.id}`, undefined, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setValidationStatus('success');
        setErrorMessage('');
      } else {
        setErrorMessage(projectsResponse.data.error || 'Failed to fetch projects');
        setValidationStatus('error');
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setErrorMessage(error.response.data.error);
      } else {
        setErrorMessage('Failed to connect to Jira. Please check your credentials.');
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
    if (!config.email || !config.apiToken || !config.domain) {
      setErrorMessage('Please fill in all required fields');
      setValidationStatus('error');
      return;
    }

    setIsLoading(true);
    setErrorMessage('');
    setValidationStatus('loading');

    try {
      const projectsResponse = await apiRequest('post', '/integrations/external/projects/', {
        provider: 'jira',
        domain: config.domain,
        email: config.email,
        api_token: config.apiToken,
      }, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setValidationStatus('success');
        setErrorMessage('');
      } else {
        setErrorMessage(projectsResponse.data.error || 'Failed to fetch projects');
        setValidationStatus('error');
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setErrorMessage(error.response.data.error);
      } else {
        setErrorMessage('Failed to connect to Jira. Please check your credentials.');
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
      setConfig(prev => ({ ...prev, projectKey: selectedProjectData.key }));
    }
  };

  const handleContinue = async () => {
    if (!selectedProject) {
      setErrorMessage('Please select a project to continue');
      return;
    }

    const selectedProjectData = projects.find(p => p.id === selectedProject);
    if (selectedProjectData) {
      localStorage.setItem('jira_email', config.email);
      localStorage.setItem('jira_domain', config.domain);
      localStorage.setItem('jira_project_key', selectedProjectData.key);
      localStorage.setItem('jira_project_id', selectedProject);
      localStorage.setItem('jira_project_name', selectedProjectData.name);
    }

    setIsCreatingProject(true);
    setErrorMessage("");

    try {
      let integrationAccountId;

      if (isExistingIntegration) {
        const jiraAccount = accounts.find(acc => acc.provider === 'jira');
        if (!jiraAccount) throw new Error('Jira integration not found');
        integrationAccountId = jiraAccount.id;
      } else {
        const integrationResponse = await apiRequest('post', '/integrations/jira/', {
          domain: config.domain,
          email: config.email,
          api_token: config.apiToken
        }, true);

        if (!integrationResponse.data.success) {
          throw new Error(integrationResponse.data.error || 'Failed to create Jira integration');
        }
        integrationAccountId = integrationResponse.data.data.account.id;
      }

      try {
        const checkResponse = await apiRequest(
          'get',
          `/integrations/external/projects/check/?provider=jira&externalId=${selectedProject}`,
          undefined,
          true
        );

        if (checkResponse.data.data.exists && checkResponse.data.data.project) {
          const existingSaramsaProject = checkResponse.data.data.project;
          localStorage.setItem('project_id', existingSaramsaProject.id);
          localStorage.setItem('selected_project_name', existingSaramsaProject.name || selectedProjectData?.name || '');
          onContinue();
          return;
        }
      } catch {
        // Best-effort check; proceed with creation
      }

      const projectData = {
        name: selectedProjectData?.name || 'Jira Project',
        description: `Imported from Jira: ${config.domain}`,
        externalLinks: [{
          provider: 'jira',
          integrationAccountId: integrationAccountId,
          externalId: selectedProject,
          externalKey: selectedProjectData?.key,
          url: `https://${config.domain}/browse/${selectedProjectData?.key}`,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {}
        }]
      };

      const result = await dispatch(createProject(projectData)).unwrap();
      localStorage.setItem('project_id', result.id);
      localStorage.setItem('selected_project_name', selectedProjectData?.name || '');
      onContinue();
    } catch (e: any) {
      if (e.response?.status === 409) {
        try {
          const checkResponse = await apiRequest(
            'get',
            `/integrations/external/projects/check/?provider=jira&externalId=${selectedProject}`,
            undefined,
            true
          );
          if (checkResponse.data.data.exists && checkResponse.data.data.project) {
            const existingProject = checkResponse.data.data.project;
            localStorage.setItem('project_id', existingProject.id);
            localStorage.setItem('selected_project_name', existingProject.name || selectedProjectData?.name || '');
            onContinue();
            return;
          }
        } catch {
          // keep default error flow
        }
      }
      setErrorMessage(e instanceof Error ? e.message : 'Failed to create project');
      setValidationStatus('error');
    } finally {
      setIsCreatingProject(false);
    }
  };

  return (
    <JiraFormPanel
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

