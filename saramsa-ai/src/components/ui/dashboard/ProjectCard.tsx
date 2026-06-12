'use client';

import { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import {
  Calendar, 
  BarChart3, 
  ExternalLink, 
  MoreVertical, 
  Trash2,
  ArrowRight,
  Edit,
  RefreshCw,
  Settings
} from 'lucide-react';
import type { Project } from '@/store/features/projects/projectsSlice';
import { DeleteProjectModal } from './DeleteProjectModal';
import { Button } from '@/components/ui/button';
import { encryptProjectId } from '@/lib/encryption';
import {
  getProviderLabel,
  providerDefinitions,
  type WorkProvider,
} from '@/lib/providers';

interface ProjectCardProps {
  project: Project;
  onClick: () => void;
  onDelete: (projectId: string) => void;
  onEdit?: (project: Project) => void;
  onSync?: (project: Project, provider: WorkProvider) => void;
  onGoToProject?: (project: Project) => void;
  isSelected?: boolean;
  deleteLoading?: boolean;
  syncLoading?: boolean;
}

export function ProjectCard({ project, onClick, onDelete, onEdit, onSync, onGoToProject, isSelected = false, deleteLoading = false, syncLoading = false }: ProjectCardProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  
  const hasExternalLinks = project.externalLinks && project.externalLinks.length > 0;
  
  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };
    
    if (showMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showMenu]);

  const getProviderIcon = (provider: WorkProvider) => {
    const definition = providerDefinitions[provider];
    const IconComponent = definition.projectBadgeIcon;

    if (IconComponent) {
      return <IconComponent className="w-3 h-3" />;
    }

    if (definition.projectBadgeGlyph) {
      return <span className="text-xs font-bold">{definition.projectBadgeGlyph}</span>;
    }

    return null;
  };

  const getProviderColor = () =>
    'bg-secondary/80 text-foreground border border-border/60';

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    });
  };

  const navigateToSettings = () => {
    try {
      const encryptedId = encryptProjectId(project.id);
      router.push(`/projects/${encryptedId}/settings/`);
    } catch (error) {
      console.error('Failed to navigate to project settings:', error);
      router.push(`/projects/${project.id}/settings/`);
    }
  };

  return (
    <motion.div
      whileHover={{ y: -1 }}
      className={`relative bg-card/80 rounded-2xl border transition-all duration-200 cursor-pointer group flex flex-col shadow-sm ${
        isSelected 
          ? 'border-border/70' 
          : 'border-border/60 hover:border-border'
      }`}
      onClick={onClick}
    >
      {/* Header */}
      <div className="p-6 pb-3">
        <div className="flex items-start justify-between mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-foreground truncate">
              {project.name}
            </h3>
            <div className="h-5 mt-1">
              {project.description && (
                <p className="text-sm text-muted-foreground line-clamp-1">
                  {project.description}
                </p>
              )}
            </div>
          </div>
          
          {/* Menu Button */}
          <div className="relative" ref={menuRef}>
            <Button
              onClick={(e) => {
                e.stopPropagation();
                setShowMenu(!showMenu);
              }}
              variant="ghost"
              size="icon"
              className="h-7 w-7 hover:bg-accent/60 rounded-lg transition-opacity"
            >
              <MoreVertical className="w-4 h-4 text-muted-foreground" />
            </Button>
            
            {showMenu && (
              <div className="absolute right-0 top-8 bg-popover border border-border/60 rounded-xl shadow-lg dark:bg-popover/95 z-10 min-w-[160px] py-1">
                {onEdit && (
                  <Button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowMenu(false);
                      onEdit(project);
                    }}
                    variant="ghost"
                    className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 transition-colors"
                  >
                    <Edit className="w-4 h-4" />
                    Edit
                  </Button>
                )}
                {hasExternalLinks && onSync && (
                  <Button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowMenu(false);
                      const provider = project.externalLinks[0].provider;
                      onSync(project, provider);
                    }}
                    disabled={syncLoading}
                    variant="ghost"
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-secondary/60 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    <RefreshCw className={`w-4 h-4 ${syncLoading ? 'animate-spin' : ''}`} />
                    Sync with {getProviderLabel(project.externalLinks[0].provider)}
                  </Button>
                )}
                <Button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowMenu(false);
                    navigateToSettings();
                  }}
                  variant="ghost"
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 transition-colors"
                >
                  <Settings className="w-4 h-4" />
                  Settings
                </Button>
                <Button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowMenu(false);
                    setShowDeleteModal(true);
                  }}
                  variant="ghost"
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-muted-foreground hover:bg-secondary/60 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </Button>
              </div>
            )}
          </div>
        </div>

        {/* Provider Badges */}
        <div className="flex gap-2">
          {project.externalLinks && project.externalLinks.length > 0 ? (
            project.externalLinks.map((link, index) => (
              <span
                key={index}
                className={`inline-flex items-center gap-1 px-2 py-0.5 ${getProviderColor()} text-xs rounded-full h-5`}
              >
                {getProviderIcon(link.provider)}
                <span>{getProviderLabel(link.provider)}</span>
              </span>
            ))
          ) : null}
        </div>
      </div>

      {/* Footer */}
      <div className="px-6 py-3 border-t border-border/60 bg-secondary/60 dark:bg-secondary/40 rounded-b-2xl mt-auto">
        <div className="flex items-center justify-between text-xs text-muted-foreground mb-2">
          <div className="flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            Created {formatDate(project.createdAt)}
          </div>
          
          {project.externalLinks && project.externalLinks.length > 0 && (
            <div className="flex items-center gap-1">
              <ExternalLink className="w-3 h-3" />
              {project.externalLinks.length} integration{project.externalLinks.length !== 1 ? 's' : ''}
            </div>
          )}
        </div>
        
        {project.metadata?.lastAnalysisAt && (
          <div className="flex items-center gap-1 mb-2 text-xs text-muted-foreground">
            <BarChart3 className="w-3 h-3" />
            Last analyzed {formatDate(project.metadata.lastAnalysisAt)}
          </div>
        )}
        
        {/* Go to Project Button */}
        {onGoToProject && (
          <Button
            onClick={(e) => {
              e.stopPropagation();
              onGoToProject(project);
            }}
            variant="saramsa"
            className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm"
          >
            <ArrowRight className="w-4 h-4" />
            Go to Analysis
          </Button>
        )}
      </div>

      {/* Selection Indicator */}
      {isSelected && (
        <div className="absolute top-4 right-4">
          <div className="w-3 h-3 bg-saramsa-brand rounded-full shadow-[0_0_10px_rgba(139,95,191,0.7)]"></div>
        </div>
      )}
      
      {/* Delete Confirmation Modal */}
      {showDeleteModal && (
        <DeleteProjectModal
          project={project}
          onConfirm={() => {
            onDelete(project.id);
            setShowDeleteModal(false);
          }}
          onCancel={() => setShowDeleteModal(false)}
          loading={deleteLoading}
        />
      )}
    </motion.div>
  );
}
