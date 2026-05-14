'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Folder, FolderOpen, Loader2, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

interface Project {
  id: string;
  name: string;
  description?: string;
  status?: string;
  externalLinks?: Array<{
    provider: 'azure' | 'jira' | 'asana' | 'linear';
    externalId: string;
    externalKey?: string;
  }>;
  metadata?: {
    totalComments?: number;
    lastAnalyzed?: string;
  };
}

interface AnalysisProjectSelectorProps {
  projects: Project[];
  selectedProjectId: string;
  loading?: boolean;
  error?: string | null;
  onProjectSelect: (projectId: string) => void;
  onRefreshProjects?: () => void;
}

export function AnalysisProjectSelector({
  projects,
  selectedProjectId,
  loading = false,
  error = null,
  onProjectSelect,
  onRefreshProjects
}: AnalysisProjectSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selectedProject = projects.find(p => p.id === selectedProjectId);

  // Filter projects based on search term
  const filteredProjects = projects.filter(project =>
    project.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    project.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getProviderIcon = (provider: 'azure' | 'jira' | 'asana' | 'linear') => {
    if (provider === 'azure') {
      return (
        <div className="w-4 h-4 bg-saramsa-brand rounded flex items-center justify-center">
          <span className="text-white text-xs font-bold">A</span>
        </div>
      );
    }
    if (provider === 'asana') {
      return (
        <div className="w-4 h-4 bg-saramsa-brand rounded flex items-center justify-center">
          <span className="text-white text-xs font-bold">S</span>
        </div>
      );
    }
    if (provider === 'linear') {
      return (
        <div className="w-4 h-4 bg-saramsa-brand rounded flex items-center justify-center">
          <span className="text-white text-xs font-bold">L</span>
        </div>
      );
    }
    return (
      <div className="w-4 h-4 bg-saramsa-brand rounded flex items-center justify-center">
        <span className="text-white text-xs font-bold">J</span>
      </div>
    );
  };

  const getProviderLabel = (provider: 'azure' | 'jira' | 'asana' | 'linear') => {
    switch (provider) {
      case 'azure':
        return 'Azure DevOps';
      case 'jira':
        return 'Jira';
      case 'asana':
        return 'Asana';
      case 'linear':
        return 'Linear';
      default:
        return provider;
    }
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return null;
    try {
      return new Date(dateString).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      });
    } catch {
      return null;
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Selected Project Display */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center justify-between w-80 px-4 py-3 bg-card/90 dark:bg-card/95 border border-border/60 dark:border-border/60 rounded-xl cursor-pointer hover:border-border dark:hover:border-border transition-colors"
      >
        <div className="flex items-center gap-3">
          {selectedProject ? (
            <>
              <FolderOpen className="w-5 h-5 text-saramsa-brand dark:text-saramsa-brand" />
              <div className="flex flex-col">
                <span className="text-sm font-medium text-foreground dark:text-foreground truncate">
                  {selectedProject.name}
                </span>
                {selectedProject.externalLinks && selectedProject.externalLinks.length > 0 && (
                  <div className="flex items-center gap-1">
                    {getProviderIcon(selectedProject.externalLinks[0].provider)}
                    <span className="text-xs text-muted-foreground dark:text-muted-foreground">
                      {getProviderLabel(selectedProject.externalLinks[0].provider)}
                    </span>
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <Folder className="w-5 h-5 text-muted-foreground" />
              <span className="text-sm text-muted-foreground dark:text-muted-foreground">
                Select a project
              </span>
            </>
          )}
        </div>
        
        <ChevronDown 
          className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`} 
        />
      </div>

      {/* Dropdown Menu */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className="absolute top-full left-0 right-0 mt-2 bg-card/90 dark:bg-card/95 border border-border/60 dark:border-border/60 rounded-xl shadow-lg z-50 max-h-80 overflow-hidden"
          >
            {/* Search Bar */}
            <div className="p-3 border-b border-border/60 dark:border-border/60">
              <Input
                type="text"
                placeholder="Search projects..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="h-9 text-sm bg-secondary/40 dark:bg-secondary/40 border border-border/60 dark:border-border/60 rounded-md focus:ring-2 focus:ring-saramsa-brand/30 focus:border-saramsa-brand/40"
                onClick={(e) => e.stopPropagation()}
              />
            </div>

            {/* Loading State */}
            {loading && (
              <div className="p-4 text-center">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground mx-auto mb-2" />
                <p className="text-sm text-muted-foreground dark:text-muted-foreground">Loading projects...</p>
              </div>
            )}

            {/* Error State */}
            {error && (
              <div className="p-4 text-center">
                <AlertCircle className="w-5 h-5 text-red-500 mx-auto mb-2" />
                <p className="text-sm text-red-600 dark:text-red-400 mb-2">{error}</p>
                {onRefreshProjects && (
                  <Button
                    onClick={onRefreshProjects}
                    variant="link"
                    size="sm"
                    className="text-saramsa-brand dark:text-saramsa-brand"
                  >
                    Try again
                  </Button>
                )}
              </div>
            )}

            {/* Projects List */}
            {!loading && !error && (
              <div className="max-h-60 overflow-y-auto">
                {filteredProjects.length === 0 ? (
                  <div className="p-4 text-center">
                    <Folder className="w-8 h-8 text-muted-foreground/70 mx-auto mb-2" />
                    <p className="text-sm text-muted-foreground dark:text-muted-foreground">
                      {searchTerm ? 'No projects found matching your search' : 'No projects available'}
                    </p>
                    {!searchTerm && onRefreshProjects && (
                      <Button
                        onClick={onRefreshProjects}
                        variant="link"
                        size="sm"
                        className="mt-2 text-saramsa-brand dark:text-saramsa-brand"
                      >
                        Refresh projects
                      </Button>
                    )}
                  </div>
                ) : (
                  filteredProjects.map((project) => (
                    <motion.div
                      key={project.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.1 }}
                      onClick={() => {
                        onProjectSelect(project.id);
                        setIsOpen(false);
                      }}
                      className={`p-3 cursor-pointer hover:bg-secondary/40 dark:hover:bg-accent/60 transition-colors ${
                        selectedProjectId === project.id ? 'bg-saramsa-brand/10 dark:bg-saramsa-brand/20' : ''
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <FolderOpen className={`w-5 h-5 mt-0.5 ${
                          selectedProjectId === project.id ? 'text-saramsa-brand dark:text-saramsa-brand' : 'text-muted-foreground'
                        }`} />
                        
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="text-sm font-medium text-foreground dark:text-foreground truncate">
                              {project.name}
                            </h3>
                            {project.externalLinks && project.externalLinks.length > 0 && (
                              getProviderIcon(project.externalLinks[0].provider)
                            )}
                          </div>
                          
                          {project.description && (
                            <p className="text-xs text-muted-foreground dark:text-muted-foreground mb-1 line-clamp-2">
                              {project.description}
                            </p>
                          )}
                          
                          <div className="flex items-center gap-3 text-xs text-muted-foreground dark:text-muted-foreground">
                            {project.metadata?.totalComments && (
                              <span>{project.metadata.totalComments} comments</span>
                            )}
                            {project.metadata?.lastAnalyzed && (
                              <span>Last analyzed: {formatDate(project.metadata.lastAnalyzed)}</span>
                            )}
                            <span className={`px-2 py-0.5 rounded-full text-xs ${
                              project.status === 'active' 
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                                : 'bg-secondary/40 text-muted-foreground dark:bg-secondary/40 dark:text-muted-foreground'
                            }`}>
                              {project.status || 'active'}
                            </span>
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  ))
                )}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

