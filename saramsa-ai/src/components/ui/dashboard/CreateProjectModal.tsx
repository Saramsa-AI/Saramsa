'use client';

import { useState, useEffect, useMemo } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store/store';
import { fetchExternalProjects, clearExternalProjects } from '@/store/features/integrations/integrationsSlice';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FolderPlus, Cloud, Loader2, Search, ExternalLink, Link2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import type { IntegrationAccount } from '@/store/features/integrations/integrationsSlice';
import {
  getProviderShortLabel,
  providerOrder,
  type WorkProvider,
} from '@/lib/providers';

interface CreateProjectModalProps {
  onClose: () => void;
  onCreate: (name: string, description?: string, externalLink?: { provider: WorkProvider, accountId: string, projectId: string, projectName: string, projectUrl?: string, projectKey?: string }) => void;
  onImport: (provider: WorkProvider) => void;
  integrations: IntegrationAccount[];
  loading?: boolean;
}

export function CreateProjectModal({ onClose, onCreate, onImport, integrations, loading = false }: CreateProjectModalProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { externalProjects, fetchingProjects } = useSelector((state: RootState) => state.integrations);
  const { projects } = useSelector((state: RootState) => state.projects);
  
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [selectedProvider, setSelectedProvider] = useState<'none' | WorkProvider>('none');
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [selectedExternalProject, setSelectedExternalProject] = useState<any>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const maxDescriptionLength = 100;
  const providerIntegrations = useMemo(
    () =>
      providerOrder.reduce((acc, provider) => {
        acc[provider] = integrations.filter(
          (integration) =>
            integration.provider === provider && integration.status === 'active'
        );
        return acc;
      }, {} as Record<WorkProvider, IntegrationAccount[]>),
    [integrations]
  );

  // Helper function to check if an external project is already linked
  const getLinkedProject = (externalProjectId: string) => {
    return projects.find(p => 
      p.externalLinks?.some(link => 
        link.provider === selectedProvider && 
        link.externalId === externalProjectId
      )
    );
  };

  // Fetch external projects when provider is selected
  useEffect(() => {
    if (selectedProvider !== 'none' && selectedAccount) {
      dispatch(clearExternalProjects());
      dispatch(fetchExternalProjects({ provider: selectedProvider, accountId: selectedAccount }));
    }
  }, [dispatch, selectedProvider, selectedAccount]);

  // Auto-select first account when provider changes
  useEffect(() => {
    if (selectedProvider !== 'none' && providerIntegrations[selectedProvider].length > 0) {
      setSelectedAccount(providerIntegrations[selectedProvider][0].id);
    } else if (selectedProvider === 'none') {
      setSelectedAccount('');
      setSelectedExternalProject(null);
    }
  }, [selectedProvider, providerIntegrations]);

  const isDuplicateName = projects.some(
    p => p.name.toLowerCase() === name.trim().toLowerCase()
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() && !isDuplicateName) {
      let externalLink = undefined;
      if (selectedProvider !== 'none' && selectedExternalProject && selectedAccount) {
        externalLink = {
          provider: selectedProvider,
          accountId: selectedAccount,
          projectId: selectedExternalProject.id,
          projectName: selectedExternalProject.name,
          projectUrl: selectedExternalProject.url,
          projectKey: selectedExternalProject.key
        };
      }
      onCreate(name.trim(), description.trim() || undefined, externalLink);
    }
  };

  const filteredProjects = externalProjects.filter(project =>
    project.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (project.description && project.description.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  const isFetchingProjects = selectedProvider !== 'none' && fetchingProjects[selectedProvider];

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.95 }}
          className="bg-card/95 rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between p-6 border-b border-border/60">
            <h2 className="text-xl font-semibold text-foreground">
              Create New Project
            </h2>
            <Button
              onClick={onClose}
              variant="ghost"
              size="icon"
              className="h-8 w-8 hover:bg-secondary/40 dark:hover:bg-accent/60"
              disabled={loading}
            >
              <X className="w-5 h-5 text-muted-foreground" />
            </Button>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="p-6 space-y-4">
            <div>
              <label htmlFor="project-name" className="block text-sm font-medium text-muted-foreground mb-2">
                Project Name *
              </label>
              <Input
                id="project-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 border border-border/60 rounded-xl bg-background/80 text-foreground focus:ring-2 focus:ring-saramsa-brand/30 focus:border-saramsa-brand/40"
                placeholder="Enter project name"
                required
                disabled={loading}
                autoFocus
              />
              {isDuplicateName && (
                <p className="text-xs text-red-500 mt-1">
                  A project with this name already exists. Please choose a different name.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="project-description" className="block text-sm font-medium text-muted-foreground mb-2">
                Description
              </label>
              <Textarea
                id="project-description"
                value={description}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value.length <= maxDescriptionLength) {
                    setDescription(value);
                  }
                }}
                rows={3}
                className="w-full px-3 py-2 border border-border/60 rounded-xl bg-background/80 text-foreground focus:ring-2 focus:ring-saramsa-brand/30 focus:border-saramsa-brand/40 resize-none"
                placeholder="Enter project description (optional)"
                disabled={loading}
              />
              <div className="flex justify-end items-center mt-1">
                <p className="text-xs text-muted-foreground">
                  {description.length}/{maxDescriptionLength}
                </p>
              </div>
            </div>

            {/* Link to External Project */}
            {providerOrder.some((provider) => providerIntegrations[provider].length > 0) && (
              <div className="pt-4 border-t border-border/60">
                <label className="block text-sm font-medium text-muted-foreground mb-3">
                  Link to External Project (Optional)
                </label>
                
                {/* Provider Selection */}
                <div className="grid grid-cols-4 gap-2 mb-3">
                  <Button
                    type="button"
                    onClick={() => setSelectedProvider('none')}
                    variant="outline"
                    size="sm"
                    className={`px-3 py-2 text-sm rounded-xl border transition-colors ${
                      selectedProvider === 'none'
                        ? 'border-saramsa-brand/60 bg-saramsa-brand/10 text-saramsa-brand dark:text-saramsa-brand'
                        : 'border-border/60 text-muted-foreground hover:bg-accent/60'
                    }`}
                    disabled={loading}
                  >
                    None
                  </Button>
                  {providerOrder.map((provider) =>
                    providerIntegrations[provider].length > 0 ? (
                    <Button
                      key={provider}
                      type="button"
                      onClick={() => setSelectedProvider(provider)}
                      variant="outline"
                      size="sm"
                      className={`px-3 py-2 text-sm rounded-xl border transition-colors ${
                        selectedProvider === provider
                          ? 'border-saramsa-brand/60 bg-saramsa-brand/10 text-saramsa-brand dark:text-saramsa-brand'
                          : 'border-border/60 text-muted-foreground hover:bg-accent/60'
                      }`}
                      disabled={loading}
                    >
                      {getProviderShortLabel(provider)}
                    </Button>
                    ) : null
                  )}
                </div>

                {/* Account Selection (if multiple accounts) */}
                {selectedProvider !== 'none' && (
                  <>
                    {providerIntegrations[selectedProvider].length > 1 && (
                      <div className="mb-3">
                        <label className="block text-xs text-muted-foreground dark:text-muted-foreground mb-1">
                          Select Account
                        </label>
                        <select
                          value={selectedAccount}
                          onChange={(e) => setSelectedAccount(e.target.value)}
                          className="w-full px-3 py-2 text-sm border border-border/60 rounded-xl bg-background/80 text-foreground"
                          disabled={loading}
                        >
                          {providerIntegrations[selectedProvider].map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.displayName}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}

                    {/* Search Projects */}
                    <div className="mb-3">
                      <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                        <Input
                          type="text"
                          value={searchTerm}
                          onChange={(e) => setSearchTerm(e.target.value)}
                          placeholder="Search projects..."
                          className="w-full pl-9 pr-3 py-2 text-sm border border-border/60 rounded-xl bg-background/80 text-foreground placeholder:text-muted-foreground"
                          disabled={loading}
                        />
                      </div>
                    </div>

                    {/* Info Banner */}
                    {!isFetchingProjects && filteredProjects.length > 0 && (
                      <div className="mb-2 p-2 bg-secondary/60 rounded-xl">
                        <p className="text-xs text-muted-foreground">
                          {filteredProjects.filter(p => getLinkedProject(p.id)).length > 0 ? (
                            <>
                              <strong>{filteredProjects.filter(p => getLinkedProject(p.id)).length}</strong> project(s) already linked. 
                              They are disabled and show which Saramsa project they're connected to.
                            </>
                          ) : (
                            'All projects are available to link.'
                          )}
                        </p>
                      </div>
                    )}

                    {/* Projects List */}
                    <div className="max-h-48 overflow-y-auto border border-border/60 rounded-xl">
                      {isFetchingProjects ? (
                        <div className="flex items-center justify-center py-8">
                          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                          <span className="ml-2 text-sm text-muted-foreground dark:text-muted-foreground">Loading projects...</span>
                        </div>
                      ) : filteredProjects.length === 0 ? (
                        <div className="text-center py-8">
                          <p className="text-sm text-muted-foreground dark:text-muted-foreground">
                            {searchTerm ? 'No projects found' : 'No projects available'}
                          </p>
                        </div>
                      ) : (
                        <div className="divide-y divide-border/60">
                          {filteredProjects.map((project) => {
                            const linkedProject = getLinkedProject(project.id);
                            const isAlreadyLinked = !!linkedProject;
                            
                            return (
                              <Button
                                key={project.id}
                                type="button"
                                onClick={() => !isAlreadyLinked && setSelectedExternalProject(project)}
                                variant="ghost"
                                className={`w-full px-3 py-2 text-left transition-colors ${
                                  isAlreadyLinked
                                    ? 'opacity-60 cursor-not-allowed bg-secondary/60'
                                    : selectedExternalProject?.id === project.id
                                    ? 'bg-saramsa-brand/10 dark:bg-saramsa-brand/20'
                                    : 'hover:bg-accent/60'
                                }`}
                                disabled={loading || isAlreadyLinked}
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-foreground truncate">
                                      {project.name}
                                    </p>
                                    {project.key && (
                                      <p className="text-xs text-muted-foreground">
                                        {project.key}
                                      </p>
                                    )}
                                    {isAlreadyLinked && (
                                      <p className="text-xs text-orange-600 dark:text-orange-400 mt-1">
                                        Already linked to "{linkedProject.name}"
                                      </p>
                                    )}
                                  </div>
                                  {selectedExternalProject?.id === project.id && !isAlreadyLinked && (
                                    <div className="w-2 h-2 bg-saramsa-brand rounded-full ml-2 flex-shrink-0"></div>
                                  )}
                                  {isAlreadyLinked && (
                                    <span className="flex items-center gap-1 text-xs px-2 py-1 bg-amber-100/80 dark:bg-amber-900/30 text-orange-700 dark:text-orange-400 rounded flex-shrink-0">
                                      <Link2 className="w-3 h-3" />
                                      Linked
                                    </span>
                                  )}
                                </div>
                              </Button>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    {selectedExternalProject && (
                      <div className="mt-2 p-2 bg-saramsa-brand/10 dark:bg-saramsa-brand/20 rounded-xl">
                        <p className="text-xs text-muted-foreground">
                          <strong>Selected:</strong> {selectedExternalProject.name}
                        </p>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={onClose}
                className="flex-1"
                disabled={loading}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="saramsa"
                className="flex-1 gap-2"
                disabled={loading || !name.trim() || isDuplicateName}
              >
                <FolderPlus className="w-4 h-4" />
                {loading ? 'Creating...' : 'Create Project'}
              </Button>
            </div>
          </form>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
