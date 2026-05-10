'use client';

import { useEffect, useState, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
import { 
  fetchProjects, 
  createProject, 
  updateProject,
  syncProjectWithExternal,
  setCurrentProject,
  clearError,
  deleteProject,
  type Project 
} from '@/store/features/projects/projectsSlice';
import { fetchIntegrationAccounts } from '@/store/features/integrations/integrationsSlice';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import { 
  Plus, 
  FolderPlus, 
  Download, 
  Calendar,
  BarChart3,
  Users,
  AlertCircle,
  ExternalLink,
  Cloud,
  Loader2,
  ChevronDown,
  ArrowRight
} from 'lucide-react';
import { ProjectCard } from '@/components/ui/dashboard/ProjectCard';
import { NewProjectDropdown } from '@/components/ui/dashboard/NewProjectDropdown';
import { ImportProjectModal } from '@/components/ui/dashboard/ImportProjectModal';
import { CreateProjectModal } from '@/components/ui/dashboard/CreateProjectModal';
import { EditProjectModal } from '@/components/ui/dashboard/EditProjectModal';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/useAuth';
import { Building2 } from 'lucide-react';

interface ProjectDashboardProps {
  onNavigateToAnalysis?: () => void;
  onGoToProject?: (project: Project) => void;
}

export function ProjectDashboard({ onNavigateToAnalysis, onGoToProject }: ProjectDashboardProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { user } = useAuth();
  const { projects, currentProject, loading, error } = useSelector((state: RootState) => state.projects);
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const activeWorkspaceName =
    user?.active_organization?.name ||
    user?.organizations?.find((o) => o.id === user?.active_organization_id)?.name ||
    null;
  
  const [showNewProjectDropdown, setShowNewProjectDropdown] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<'azure' | 'jira' | null>(null);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);
  const [syncingProjectId, setSyncingProjectId] = useState<string | null>(null);
  const hasFetchedRef = useRef(false);

  useEffect(() => {
    // Prevent double fetching in development strict mode
    if (hasFetchedRef.current) return;
    hasFetchedRef.current = true;
    
    dispatch(fetchProjects());
    dispatch(fetchIntegrationAccounts());
  }, [dispatch]);

  const handleCreateProject = async (
    name: string, 
    description?: string, 
    externalLink?: { provider: 'azure' | 'jira', accountId: string, projectId: string, projectName: string, projectUrl?: string, projectKey?: string }
  ) => {
    try {
      let result;
      if (externalLink) {
        // Create project with external link
        const externalLinks = [{
          provider: externalLink.provider,
          integrationAccountId: externalLink.accountId,
          externalId: externalLink.projectId,
          externalKey: externalLink.projectKey,
          url: externalLink.projectUrl,
          status: 'ok',
          lastSyncedAt: null,
          syncMetadata: {}
        }];
        
        result = await dispatch(createProject({ 
          name, 
          description,
          externalLinks 
        })).unwrap();
      } else {
        // Create standalone project
        result = await dispatch(createProject({ name, description })).unwrap();
      }
      
      dispatch(setCurrentProject(result));
      setShowCreateModal(false);
      toast.success('Project created successfully');
      
      // Refetch projects to ensure consistency
      await dispatch(fetchProjects());
    } catch (err: any) {
      console.error('Failed to create project:', err);
      const errorMessage = typeof err === 'string' ? err : err?.message || 'Failed to create project';
      
      if (errorMessage.includes('already imported') || errorMessage.includes('already linked')) {
        const match = errorMessage.match(/Project "([^"]+)"/);
        const projectName = match ? match[1] : 'another project';
        toast.error(`This external project is already linked to ${projectName}`);
      } else {
        toast.error(errorMessage);
      }
    }
  };

  const handleOpenCreateModal = () => {
    setShowNewProjectDropdown(false);
    setShowCreateModal(true);
  };

  const handleImportProject = (provider: 'azure' | 'jira') => {
    const hasIntegration = accounts.some(acc => acc.provider === provider && acc.status === 'active');
    
    if (!hasIntegration) {
      // Show error and redirect to settings
      alert(`No ${provider === 'azure' ? 'Azure DevOps' : 'Jira'} integration found. Please go to Settings > Integrations to connect your account.`);
      return;
    }
    
    setSelectedProvider(provider);
    setShowImportModal(true);
    setShowNewProjectDropdown(false);
  };

  const handleEditProject = (project: Project) => {
    setEditingProject(project);
    setShowEditModal(true);
  };

  const handleSaveEdit = async (projectId: string, name: string, description?: string) => {
    try {
      await dispatch(updateProject({ id: projectId, name, description })).unwrap();
      toast.success('Project updated successfully');
    } catch (err: any) {
      console.error('Failed to update project:', err);
      toast.error(err?.message || 'Failed to update project. Please try again.');
      throw err;
    }
  };

  const handleSyncProject = async (project: Project, provider: 'azure' | 'jira') => {
    try {
      setSyncingProjectId(project.id);
      await dispatch(syncProjectWithExternal({ projectId: project.id, provider })).unwrap();
      // Refresh projects to get updated data
      await dispatch(fetchProjects());
      alert(`Successfully synced "${project.name}" with ${provider === 'azure' ? 'Azure DevOps' : 'Jira'}`);
    } catch (err: any) {
      console.error('Failed to sync project:', err);
      alert(err?.message || 'Failed to sync project. Please try again.');
    } finally {
      setSyncingProjectId(null);
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    try {
      setDeletingProjectId(projectId);
      await dispatch(deleteProject(projectId)).unwrap();
      toast.success('Project deleted successfully');
    } catch (err: any) {
      console.error('Failed to delete project:', err);
      
      let errorMessage = 'Failed to delete project. Please try again.';
      if (err?.message?.includes('404')) {
        errorMessage = 'Project not found. It may have already been deleted.';
      } else if (err?.message?.includes('403')) {
        errorMessage = 'You do not have permission to delete this project.';
      }
      
      toast.error(errorMessage);
    } finally {
      setDeletingProjectId(null);
    }
  };

  const getProviderBadge = (provider: 'azure' | 'jira') => {
    const config = {
      azure: { name: 'Azure DevOps', color: 'bg-saramsa-brand', IconComponent: Cloud },
      jira: { name: 'Jira', color: 'bg-saramsa-brand', IconComponent: null }
    };
    
    const { name, color, IconComponent } = config[provider];
    
    return (
      <div className={`inline-flex items-center gap-1 px-2 py-1 ${color} text-white text-xs rounded-full`}>
        {IconComponent ? (
          <IconComponent className="w-3 h-3" />
        ) : (
          <span className="font-bold">J</span>
        )}
        {name}
      </div>
    );
  };

  if (loading && projects.length === 0) {
    return (
      <div className="min-h-screen bg-background p-6">
        <div className="max-w-7xl mx-auto">
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background p-6">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="space-y-0.5">
              {activeWorkspaceName && (
                <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
                  <Building2 className="h-3 w-3 text-saramsa-brand" />
                  <span>{activeWorkspaceName}</span>
                </div>
              )}
              <h1 className="text-3xl font-semibold text-foreground">Projects</h1>
            </div>

            {/* Stats Cards */}
            {projects.length > 0 && (
              <div className="flex items-center gap-3">
                <div className="bg-card/80 border border-border/60 rounded-2xl px-3 py-2 flex items-center gap-2 shadow-[0_10px_24px_-20px_rgba(15,23,42,0.45)]">
                  <FolderPlus className="w-4 h-4 text-saramsa-brand" />
                  <div className="flex items-baseline gap-1">
                    <span className="text-lg font-semibold text-foreground">{projects.length}</span>
                    <span className="text-xs text-muted-foreground">{projects.length === 1 ? 'Project' : 'Projects'}</span>
                  </div>
                </div>
                <div className="bg-card/80 border border-border/60 rounded-2xl px-3 py-2 flex items-center gap-2 shadow-[0_10px_24px_-20px_rgba(15,23,42,0.45)]">
                  <ExternalLink className="w-4 h-4 text-saramsa-brand" />
                  <div className="flex items-baseline gap-1">
                    <span className="text-lg font-semibold text-foreground">
                      {projects.filter(p => p.externalLinks && p.externalLinks.length > 0).length}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {projects.filter(p => p.externalLinks && p.externalLinks.length > 0).length === 1 ? 'Integration' : 'Integrations'}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
          
          <div className="flex items-center gap-3">
            {/* Redirect to Analysis Button */}
            {onNavigateToAnalysis && (
              <Button
                onClick={onNavigateToAnalysis}
                variant="outline"
                className="flex items-center gap-2 px-4 py-2 cursor-pointer"
                title="Go to Analysis Dashboard"
              >
                <ArrowRight className="w-4 h-4" />
                Analysis Dashboard
              </Button>
            )}
            
            {/* New Project Button */}
            <div className="relative">
              <Button
                onClick={() => setShowNewProjectDropdown(!showNewProjectDropdown)}
                variant="saramsa"
                className="flex items-center gap-2 px-4 py-2"
              >
                <Plus className="w-4 h-4" />
                New Project
                <ChevronDown className={`w-4 h-4 transition-transform ${showNewProjectDropdown ? 'rotate-180' : ''}`} />
              </Button>
              
              {showNewProjectDropdown && (
                <NewProjectDropdown
                  onClose={() => setShowNewProjectDropdown(false)}
                  onCreateProject={handleOpenCreateModal}
                  onImportProject={handleImportProject}
                  integrations={accounts}
                />
              )}
            </div>
          </div>
        </div>

        {/* Error Display */}
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-red-50/80 dark:bg-red-900/20 border border-red-200/70 dark:border-red-800/60 rounded-2xl p-4"
          >
            <div className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-red-500" />
              <span className="text-red-700 dark:text-red-300">{error}</span>
              <Button
                onClick={() => dispatch(clearError())}
                variant="ghost"
                size="sm"
                className="ml-auto text-red-500 hover:text-red-700"
              >
                x
              </Button>
            </div>
          </motion.div>
        )}

        {/* Projects Grid */}
        {projects.length === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-center py-20 bg-card rounded-2xl border border-dashed border-border/70 shadow-md dark:bg-card/80"
          >
            <FolderPlus className="w-16 h-16 text-muted-foreground mx-auto mb-6" />
            <h3 className="text-xl font-semibold text-foreground mb-2">
              No projects yet
            </h3>
            <p className="text-muted-foreground mb-8 max-w-md mx-auto">
              Create your first project to start analyzing feedback and generating insights. 
              You can create a blank project or import from your DevOps platforms.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Button
                onClick={() => setShowCreateModal(true)}
                variant="saramsa"
                className="flex items-center gap-2 px-6 py-3"
              >
                <FolderPlus className="w-5 h-5" />
                Create Project
              </Button>
              
              <Button
                onClick={() => setShowNewProjectDropdown(true)}
                variant="outline"
                className="flex items-center gap-2 px-6 py-3"
              >
                <Download className="w-5 h-5" />
                Import from DevOps
              </Button>
            </div>
            
            {accounts.length === 0 && (
              <div className="mt-6 p-4 bg-secondary/80 dark:bg-secondary/60 rounded-2xl max-w-md mx-auto border border-border/60">
                <p className="text-sm text-muted-foreground">
                  Tip: Connect your Azure DevOps or Jira accounts in{' '}
                  <a href="/settings" className="underline hover:no-underline">
                    Settings {'>'} Integrations
                  </a>{' '}
                  to import existing projects.
                </p>
              </div>
            )}
          </motion.div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project, index) => (
              <motion.div
                key={project.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1 }}
              >
                <ProjectCard
                  project={project}
                  onClick={() => dispatch(setCurrentProject(project))}
                  onEdit={handleEditProject}
                  onSync={handleSyncProject}
                  onDelete={handleDeleteProject}
                  onGoToProject={onGoToProject}
                  isSelected={currentProject?.id === project.id}
                  deleteLoading={deletingProjectId === project.id}
                  syncLoading={syncingProjectId === project.id}
                />
              </motion.div>
            ))}
          </div>
        )}


      </div>

      {/* Create Project Modal */}
      {showCreateModal && (
        <CreateProjectModal
          onClose={() => setShowCreateModal(false)}
          onCreate={handleCreateProject}
          onImport={handleImportProject}
          integrations={accounts}
          loading={loading}
        />
      )}

      {/* Edit Project Modal */}
      {showEditModal && editingProject && (
        <EditProjectModal
          project={editingProject}
          onClose={() => {
            setShowEditModal(false);
            setEditingProject(null);
          }}
          onSave={handleSaveEdit}
          loading={loading}
        />
      )}

      {/* Import Project Modal */}
      {showImportModal && selectedProvider && (
        <ImportProjectModal
          provider={selectedProvider}
          onClose={() => {
            setShowImportModal(false);
            setSelectedProvider(null);
          }}
          onSuccess={() => {
            setShowImportModal(false);
            setSelectedProvider(null);
            dispatch(fetchProjects());
          }}
        />
      )}
    </div>
  );
}


