'use client';

import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
import { 
  fetchExternalProjects,
  clearExternalProjects 
} from '@/store/features/integrations/integrationsSlice';
import { importProjectFromExternal } from '@/store/features/projects/projectsSlice';
import { motion } from 'framer-motion';
import {
  X,
  Search,
  Cloud,
  CheckSquare,
  ExternalLink,
  Loader2,
  CheckCircle,
  AlertCircle,
  ArrowRight,
  Users,
  Triangle,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

interface ImportProjectModalProps {
  provider: 'azure' | 'jira' | 'asana' | 'linear';
  onClose: () => void;
  onSuccess: () => void;
}

export function ImportProjectModal({ provider, onClose, onSuccess }: ImportProjectModalProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { externalProjects, fetchingProjects, error, accounts } = useSelector((state: RootState) => state.integrations);
  const { projects, importing, importError } = useSelector((state: RootState) => state.projects);
  
  const fetchingProjectsForProvider = fetchingProjects[provider] || false;
  
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedProject, setSelectedProject] = useState<any>(null);
  const [selectedAccount, setSelectedAccount] = useState<string>('');

  const providerAccounts = accounts.filter(acc => acc.provider === provider && acc.status === 'active');

  // Helper function to check if an external project is already linked
  const getLinkedProject = (externalProjectId: string) => {
    return projects.find(p => 
      p.externalLinks?.some(link => 
        link.provider === provider && 
        link.externalId === externalProjectId
      )
    );
  };

  useEffect(() => {
    if (providerAccounts.length > 0) {
      setSelectedAccount(providerAccounts[0].id);
    }
  }, [providerAccounts]);

  useEffect(() => {
    if (selectedAccount) {
      dispatch(clearExternalProjects());
      dispatch(fetchExternalProjects({ provider, accountId: selectedAccount }));
    }
  }, [dispatch, provider, selectedAccount]);

  const filteredProjects = externalProjects.filter(project =>
    project.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (project.description && project.description.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  const handleImport = async () => {
    if (!selectedProject || !selectedAccount) return;

    try {
      await dispatch(importProjectFromExternal({
        provider,
        integrationAccountId: selectedAccount,
        externalProject: selectedProject,
      })).unwrap();
      
      onSuccess();
    } catch (err) {
      console.error('Failed to import project:', err);
    }
  };

  const getProviderConfig = () => {
    switch (provider) {
      case 'azure':
        return {
          name: 'Azure DevOps',
          color: 'bg-secondary/70 border border-border/60',
          icon: <Cloud className="w-5 h-5 text-foreground" />,
          baseUrl: 'https://dev.azure.com'
        };
      case 'jira':
        return {
          name: 'Jira',
          color: 'bg-secondary/70 border border-border/60',
          icon: <span className="text-foreground font-bold">J</span>,
          baseUrl: 'https://atlassian.net'
        };
      case 'asana':
        return {
          name: 'Asana',
          color: 'bg-secondary/70 border border-border/60',
          icon: <CheckSquare className="w-5 h-5 text-foreground" />,
          baseUrl: 'https://app.asana.com'
        };
      case 'linear':
        return {
          name: 'Linear',
          color: 'bg-secondary/70 border border-border/60',
          icon: <Triangle className="w-5 h-5 text-foreground" />,
          baseUrl: 'https://linear.app'
        };
    }
  };

  const config = getProviderConfig();

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="bg-card/95 rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-border/60">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 ${config.color} rounded-xl flex items-center justify-center`}>
              {config.icon}
            </div>
            <div>
              <h2 className="text-xl font-semibold text-foreground">
                Import from {config.name}
              </h2>
              <p className="text-sm text-muted-foreground">
                Select a project to import into Saramsa AI
              </p>
            </div>
          </div>
          <Button
            onClick={onClose}
            variant="ghost"
            size="icon"
            className="h-9 w-9 hover:bg-accent/60"
          >
            <X className="w-5 h-5" />
          </Button>
        </div>

        {/* Account Selection */}
        {providerAccounts.length > 1 && (
          <div className="p-6 border-b border-border/60">
            <label className="block text-sm font-medium text-muted-foreground mb-2">
              Select Account
            </label>
            <select
              value={selectedAccount}
              onChange={(e) => setSelectedAccount(e.target.value)}
              className="w-full px-3 py-2 border border-border/60 rounded-xl bg-background/80 text-foreground"
            >
              {providerAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.displayName}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Search */}
        <div className="p-6 border-b border-border/60">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search projects..."
              className="w-full pl-10 pr-4 py-2 border border-border/60 rounded-xl bg-background/80 text-foreground placeholder:text-muted-foreground focus:ring-2 focus:ring-saramsa-brand/30 focus:border-saramsa-brand/40"
            />
          </div>
        </div>

        {/* Error Display */}
        {(error || importError) && (
          <div className="p-6 border-b border-border/60">
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-red-500" />
                <span className="text-red-700 dark:text-red-300">{error || importError}</span>
              </div>
            </div>
          </div>
        )}

        {/* Info Banner */}
        {!fetchingProjectsForProvider && filteredProjects.length > 0 && (
          <div className="px-6 pb-4">
            <div className="p-3 bg-secondary/60 rounded-xl">
              <p className="text-xs text-muted-foreground">
                {filteredProjects.filter(p => getLinkedProject(p.id)).length > 0 ? (
                  <>
                    <strong>{filteredProjects.filter(p => getLinkedProject(p.id)).length}</strong> project(s) already linked. 
                    They cannot be imported again.
                  </>
                ) : (
                  'All projects are available to import.'
                )}
              </p>
            </div>
          </div>
        )}

        {/* Projects List */}
        <div className="flex-1 overflow-y-auto max-h-96">
          {fetchingProjectsForProvider ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              <span className="ml-2 text-muted-foreground">Loading projects...</span>
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="text-center py-12">
              <div className="w-16 h-16 bg-secondary/40 dark:bg-secondary/40 rounded-full flex items-center justify-center mx-auto mb-4">
                <Search className="w-8 h-8 text-muted-foreground" />
              </div>
              <h3 className="text-lg font-medium text-foreground mb-2">
                {searchTerm ? 'No projects found' : 'No projects available'}
              </h3>
              <p className="text-muted-foreground">
                {searchTerm 
                  ? 'Try adjusting your search terms'
                  : `No projects found in your ${config.name} account`
                }
              </p>
            </div>
          ) : (
            <div className="p-6 space-y-3">
              {filteredProjects.map((project) => {
                const linkedProject = getLinkedProject(project.id);
                const isAlreadyLinked = !!linkedProject;
                
                return (
                  <motion.div
                    key={project.id}
                    whileHover={{ scale: isAlreadyLinked ? 1 : 1.01 }}
                    className={`p-4 border-2 rounded-xl transition-all ${
                      isAlreadyLinked
                        ? 'opacity-60 cursor-not-allowed border-border/60 bg-secondary/60'
                        : selectedProject?.id === project.id
                        ? 'border-saramsa-brand/60 bg-saramsa-brand/10 dark:bg-saramsa-brand/20 cursor-pointer'
                        : 'border-border/60 hover:border-border/60 dark:hover:border-border cursor-pointer'
                    }`}
                    onClick={() => !isAlreadyLinked && setSelectedProject(project)}
                  >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <h4 className="font-medium text-foreground">
                          {project.name}
                        </h4>
                        {project.key && (
                          <span className="px-2 py-1 bg-secondary/40 dark:bg-secondary/40 text-xs rounded">
                            {project.key}
                          </span>
                        )}
                      </div>
                      {project.description && (
                        <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                          {project.description}
                        </p>
                      )}
                      
                      {/* Already linked warning */}
                      {isAlreadyLinked && (
                        <div className="mt-2 p-2 bg-amber-100/80 dark:bg-amber-900/30 rounded">
                          <p className="text-xs text-orange-700 dark:text-orange-400">
                            Already linked to "{linkedProject.name}"
                          </p>
                        </div>
                      )}
                      
                      {/* Project metadata */}
                      <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                        {project.templateName && (
                          <div className="flex items-center gap-1">
                            <Users className="w-3 h-3" />
                            {project.templateName}
                          </div>
                        )}
                        {project.url && (
                          <a
                            href={project.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="flex items-center gap-1 hover:text-saramsa-brand"
                          >
                            <ExternalLink className="w-3 h-3" />
                            View in {config.name}
                          </a>
                        )}
                      </div>
                    </div>
                    
                    {selectedProject?.id === project.id && !isAlreadyLinked && (
                      <CheckCircle className="w-5 h-5 text-saramsa-brand" />
                    )}
                    {isAlreadyLinked && (
                      <span className="text-xs px-2 py-1 bg-amber-100/80 dark:bg-amber-900/30 text-orange-700 dark:text-orange-400 rounded">
                        Linked
                      </span>
                    )}
                  </div>
                </motion.div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-border/60 bg-secondary/40">
          <div className="flex items-center justify-between">
            <div className="text-sm text-muted-foreground">
              {selectedProject ? (
                getLinkedProject(selectedProject.id) ? (
                  <span className="text-orange-600 dark:text-orange-400">
                    This project is already linked to "{getLinkedProject(selectedProject.id)?.name}"
                  </span>
                ) : (
                  <span>Selected: <strong>{selectedProject.name}</strong></span>
                )
              ) : (
                'Select a project to import'
              )}
            </div>
            
            <div className="flex gap-3">
              <Button
                variant="outline"
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button
                variant="saramsa"
                onClick={handleImport}
                disabled={!selectedProject || importing || (selectedProject && !!getLinkedProject(selectedProject.id))}
                className="gap-2"
              >
                {importing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Importing...
                  </>
                ) : (
                  <>
                    Import Project
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

