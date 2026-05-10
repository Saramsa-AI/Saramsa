'use client';

import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from "@/store/store";
import { fetchProjects, createProject } from "@/store/features/projects/projectsSlice";
import { DevOpsFormPanel } from './DevOpsFormPanel';
import { apiRequest } from '@/lib/apiRequest';

type AzureDevOpsProject = {
  id: string;
  name: string;
  description?: string;
  url?: string;
  templateName?: string;
};

interface AzureDevOpsIntegrationFormProps {
  onContinue: () => void;
  onBack: () => void;
}

export function AzureDevOpsIntegrationForm({ onContinue, onBack }: AzureDevOpsIntegrationFormProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const { projects: saramsaProjects } = useSelector((state: RootState) => state.projects);

  const [orgName, setOrgName] = useState("");
  const [pat, setPat] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [error, setError] = useState<string>("");
  const [projects, setProjects] = useState<AzureDevOpsProject[]>([]);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [isExistingIntegration, setIsExistingIntegration] = useState(false);

  const linkedProjects: { [key: string]: { id: string; name: string } } = {};
  saramsaProjects?.forEach(project => {
    project.externalLinks?.forEach(link => {
      if (link.provider === 'azure') {
        linkedProjects[link.externalId] = { id: project.id, name: project.name };
      }
    });
  });

  useEffect(() => {
    dispatch(fetchProjects());
  }, [dispatch]);

  useEffect(() => {
    const azureAccount = accounts.find(acc => acc.provider === 'azure');
    if (azureAccount) {
      setIsExistingIntegration(true);
      setOrgName(azureAccount.metadata.organization || '');
      fetchProjectsForExistingIntegration(azureAccount);
    }
  }, [accounts]);

  const fetchProjectsForExistingIntegration = async (azureAccount: any) => {
    setIsLoading(true);
    setError("");

    try {
      const url = `/integrations/external/projects/?provider=azure&accountId=${azureAccount.id}`;
      const projectsResponse = await apiRequest('get', url, undefined, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setError('');
      } else {
        const errorMsg = projectsResponse.data.error || projectsResponse.data.detail || 'Failed to fetch projects';
        setError(errorMsg);
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setError(error.response.data.error);
      } else if (error.response?.data?.detail) {
        setError(error.response.data.detail);
      } else {
        setError('Failed to connect to Azure DevOps. Please check your credentials.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleProjectSelect = (projectId: string) => {
    setSelectedProject(projectId);
  };

  const handleContinue = async () => {
    if (!selectedProject) {
      setError("Please select a project to continue");
      return;
    }

    const selectedProjectData = projects.find(p => p.id === selectedProject);
    if (selectedProjectData) {
      localStorage.setItem('azure_organization', orgName);
      localStorage.setItem('azure_selected_project', selectedProject);
      localStorage.setItem('azure_project_name', selectedProjectData.name);
      if (selectedProjectData.templateName) {
        localStorage.setItem('azure_process_template', selectedProjectData.templateName);
      }
    }

    setIsCreatingProject(true);
    setError("");

    try {
      await handlePersistProject();
      onContinue();
    } catch {
      // Error already set by handlePersistProject
    } finally {
      setIsCreatingProject(false);
    }
  };

  const handleFetchProjects = async () => {
    if (!orgName.trim() || !pat.trim()) {
      setError("Please provide both organization name and personal access token");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const projectsResponse = await apiRequest('post', '/integrations/external/projects/', {
        provider: 'azure',
        organization: orgName,
        pat_token: pat,
      }, true);

      if (projectsResponse.data.success) {
        setProjects(projectsResponse.data.data.projects || []);
        setError('');
      } else {
        setError(projectsResponse.data.error || 'Failed to fetch projects');
      }
    } catch (error: any) {
      if (error.response?.data?.error) {
        setError(error.response.data.error);
      } else {
        setError('Failed to connect to Azure DevOps. Please check your credentials.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handlePersistProject = async () => {
    if (!selectedProject) return;
    const selectedProjectData = projects.find(p => p.id === selectedProject);
    try {
      let integrationAccountId;

      if (isExistingIntegration) {
        const azureAccount = accounts.find(acc => acc.provider === 'azure');
        if (!azureAccount) throw new Error('Azure integration not found');
        integrationAccountId = azureAccount.id;
      } else {
        const integrationResponse = await apiRequest('post', '/integrations/azure/', {
          organization: orgName,
          pat_token: pat,
          create_integration: true
        }, true);

        if (!integrationResponse.data.success) {
          throw new Error(integrationResponse.data.error || 'Failed to create Azure integration');
        }
        integrationAccountId = integrationResponse.data.data.account.id;
      }

      const projectData = {
        name: selectedProjectData?.name || 'Azure DevOps Project',
        description: `Imported from Azure DevOps: ${orgName}`,
        externalLinks: [{
          provider: 'azure',
          integrationAccountId: integrationAccountId,
          externalId: selectedProject,
          url: `https://dev.azure.com/${orgName}/${selectedProjectData?.name}`,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {}
        }]
      };

      const result = await dispatch(createProject(projectData)).unwrap();
      localStorage.setItem('project_id', result.id);
      localStorage.setItem('selected_project_name', selectedProjectData?.name || '');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create project');
      throw e;
    }
  };

  return (
    <DevOpsFormPanel
      orgName={orgName}
      onOrgNameChange={setOrgName}
      pat={pat}
      onPatChange={setPat}
      onFetchProjects={handleFetchProjects}
      isLoading={isLoading}
      isCreatingProject={isCreatingProject}
      error={error}
      projects={projects}
      selectedProject={selectedProject}
      onProjectSelect={handleProjectSelect}
      onContinue={handleContinue}
      onBack={onBack}
      isExistingIntegration={isExistingIntegration}
      linkedProjects={linkedProjects}
    />
  );
}

