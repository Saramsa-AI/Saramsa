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
import { X, Search, Loader2, AlertCircle, Check } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

interface ImportProjectModalProps {
  provider: 'azure' | 'jira' | 'asana' | 'linear';
  onClose: () => void;
  onSuccess: () => void;
}

const PROVIDER_NAMES: Record<ImportProjectModalProps['provider'], string> = {
  azure: 'Azure DevOps',
  jira: 'Jira',
  asana: 'Asana',
  linear: 'Linear',
};

export function ImportProjectModal({ provider, onClose, onSuccess }: ImportProjectModalProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { externalProjects, fetchingProjects, error, accounts } = useSelector((state: RootState) => state.integrations);
  const { projects, importing, importError } = useSelector((state: RootState) => state.projects);

  const fetchingProjectsForProvider = fetchingProjects[provider] || false;

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedProject, setSelectedProject] = useState<any>(null);
  const [selectedAccount, setSelectedAccount] = useState<string>('');

  const providerName = PROVIDER_NAMES[provider];
  const providerAccounts = accounts.filter(acc => acc.provider === provider && acc.status === 'active');

  const getLinkedProject = (externalProjectId: string) =>
    projects.find(p =>
      p.externalLinks?.some(link => link.provider === provider && link.externalId === externalProjectId)
    );

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

  const linkedCount = filteredProjects.filter(p => getLinkedProject(p.id)).length;

  const handleImport = async () => {
    if (!selectedProject || !selectedAccount) return;
    try {
      await dispatch(importProjectFromExternal({
        provider,
        integrationAccountId: selectedAccount,
        externalProject: selectedProject,
      })).unwrap();
      onSuccess();
    } catch {
      // Surfaced to the user via importError in the store.
    }
  };

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
        className="bg-card/95 rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden"
      >
        {/* Header (fixed) */}
        <div className="flex items-start justify-between gap-4 p-5 border-b border-border/60 flex-shrink-0">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Import from {providerName}</h2>
            <p className="text-sm text-muted-foreground">Select a project to import into Saramsa</p>
          </div>
          <Button onClick={onClose} variant="ghost" size="icon" className="h-8 w-8 flex-shrink-0 hover:bg-accent/60">
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Controls (fixed) */}
        <div className="px-5 pt-4 space-y-3 flex-shrink-0">
          {providerAccounts.length > 1 && (
            <select
              value={selectedAccount}
              onChange={(e) => setSelectedAccount(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border/60 rounded-xl bg-background/80 text-foreground"
            >
              {providerAccounts.map((account) => (
                <option key={account.id} value={account.id}>{account.displayName}</option>
              ))}
            </select>
          )}

          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search projects..."
              className="w-full pl-9 pr-3 py-2 text-sm border border-border/60 rounded-xl bg-background/80 text-foreground placeholder:text-muted-foreground"
            />
          </div>

          {(error || importError) && (
            <div className="flex items-center gap-2 rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-3 py-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0 text-red-500" />
              <span className="text-sm text-red-700 dark:text-red-300">{error || importError}</span>
            </div>
          )}

          {!fetchingProjectsForProvider && linkedCount > 0 && (
            <p className="text-xs text-muted-foreground">
              <strong>{linkedCount}</strong> already imported — shown disabled below.
            </p>
          )}
        </div>

        {/* Projects list (the only scrollable region) */}
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
          {fetchingProjectsForProvider ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="ml-2 text-sm">Loading projects...</span>
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-sm font-medium text-foreground">
                {searchTerm ? 'No projects found' : 'No projects available'}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {searchTerm ? 'Try a different search term' : `No projects found in your ${providerName} account`}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredProjects.map((project) => {
                const linkedProject = getLinkedProject(project.id);
                const isAlreadyLinked = !!linkedProject;
                const isSelected = selectedProject?.id === project.id && !isAlreadyLinked;

                return (
                  <button
                    key={project.id}
                    type="button"
                    onClick={() => !isAlreadyLinked && setSelectedProject(project)}
                    disabled={isAlreadyLinked || importing}
                    aria-pressed={isSelected}
                    className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
                      isAlreadyLinked
                        ? 'cursor-not-allowed border-border/60 bg-muted/40'
                        : isSelected
                        ? 'border-saramsa-brand/60 bg-saramsa-brand/10'
                        : 'border-border/60 hover:bg-accent/50'
                    }`}
                  >
                    {/* Selection indicator */}
                    <span
                      className={`mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border ${
                        isSelected ? 'border-saramsa-brand bg-saramsa-brand text-white' : 'border-border'
                      } ${isAlreadyLinked ? 'opacity-40' : ''}`}
                    >
                      {isSelected && <Check className="h-3 w-3" />}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`truncate text-sm font-medium ${isAlreadyLinked ? 'text-muted-foreground' : 'text-foreground'}`}>
                          {project.name}
                        </span>
                        {project.key && (
                          <span className="flex-shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                            {project.key}
                          </span>
                        )}
                      </div>
                      {project.description && (
                        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{project.description}</p>
                      )}
                      {isAlreadyLinked ? (
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">Imported as {linkedProject.name}</p>
                      ) : project.url ? (
                        <a
                          href={project.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="mt-0.5 inline-block text-xs text-muted-foreground hover:text-saramsa-brand hover:underline"
                        >
                          View in {providerName}
                        </a>
                      ) : null}
                    </div>

                    {isAlreadyLinked && (
                      <span className="flex-shrink-0 self-center rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                        Imported
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer (fixed — always visible) */}
        <div className="flex items-center justify-between gap-3 border-t border-border/60 p-4 flex-shrink-0">
          <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
            {selectedProject
              ? <>Selected: <strong className="text-foreground">{selectedProject.name}</strong></>
              : 'No project selected'}
          </p>
          <div className="flex flex-shrink-0 gap-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
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
                'Import Project'
              )}
            </Button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
