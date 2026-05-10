'use client';

import { useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { 
  FolderPlus, 
  Cloud, 
  CheckSquare,
  AlertCircle,
  ExternalLink
} from 'lucide-react';
import type { IntegrationAccount } from '@/store/features/integrations/integrationsSlice';
import { Button } from '@/components/ui/button';

interface NewProjectDropdownProps {
  onClose: () => void;
  onCreateProject: () => void;
  onImportProject: (provider: 'azure' | 'jira' | 'asana') => void;
  integrations: IntegrationAccount[];
}

export function NewProjectDropdown({ 
  onClose, 
  onCreateProject, 
  onImportProject, 
  integrations 
}: NewProjectDropdownProps) {
  const dropdownRef = useRef<HTMLDivElement>(null);

  const azureIntegrations = integrations.filter(acc => acc.provider === 'azure' && acc.status === 'active');
  const jiraIntegrations = integrations.filter(acc => acc.provider === 'jira' && acc.status === 'active');
  const asanaIntegrations = integrations.filter(acc => acc.provider === 'asana' && acc.status === 'active');

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  const handleImportClick = (provider: 'azure' | 'jira' | 'asana') => {
    onImportProject(provider);
  };

  return (
    <motion.div
      ref={dropdownRef}
      initial={{ opacity: 0, scale: 0.95, y: -10 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95, y: -10 }}
      className="absolute top-12 right-0 bg-popover border border-border/60 rounded-xl shadow-lg dark:bg-popover/95 z-50 w-72"
    >
      <div className="p-2">
        {/* Create Project */}
        <Button
          onClick={onCreateProject}
          variant="ghost"
          className="flex items-center gap-3 w-full px-3 py-3 text-left hover:bg-accent/60 rounded-xl transition-colors"
        >
          <div className="w-8 h-8 bg-saramsa-brand/15 border border-saramsa-brand/30 rounded-xl flex items-center justify-center">
            <FolderPlus className="w-4 h-4 text-saramsa-brand" />
          </div>
          <div>
            <p className="font-medium text-foreground">Create Project</p>
            <p className="text-sm text-muted-foreground">Start with a blank project</p>
          </div>
        </Button>

        <div className="my-2 border-t border-border/60"></div>

        {/* Import from Azure DevOps */}
        <Button
          onClick={() => handleImportClick('azure')}
          disabled={azureIntegrations.length === 0}
          variant="ghost"
          className="flex items-center gap-3 w-full px-3 py-3 text-left hover:bg-accent/60 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <div className="w-8 h-8 bg-secondary/70 border border-border/60 rounded-xl flex items-center justify-center">
            <Cloud className="w-4 h-4 text-foreground" />
          </div>
          <div className="flex-1">
            <p className="font-medium text-foreground">Import from Azure DevOps</p>
            <p className="text-sm text-muted-foreground">
              {azureIntegrations.length > 0 
                ? `${azureIntegrations.length} account${azureIntegrations.length !== 1 ? 's' : ''} connected`
                : 'No accounts connected'
              }
            </p>
          </div>
          {azureIntegrations.length === 0 && (
            <AlertCircle className="w-4 h-4 text-yellow-500" />
          )}
        </Button>

        {/* Import from Jira */}
        <Button
          onClick={() => handleImportClick('jira')}
          disabled={jiraIntegrations.length === 0}
          variant="ghost"
          className="flex items-center gap-3 w-full px-3 py-3 text-left hover:bg-accent/60 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <div className="w-8 h-8 bg-secondary/70 border border-border/60 rounded-xl flex items-center justify-center">
            <span className="text-foreground text-sm font-bold">J</span>
          </div>
          <div className="flex-1">
            <p className="font-medium text-foreground">Import from Jira</p>
            <p className="text-sm text-muted-foreground">
              {jiraIntegrations.length > 0 
                ? `${jiraIntegrations.length} account${jiraIntegrations.length !== 1 ? 's' : ''} connected`
                : 'No accounts connected'
              }
            </p>
          </div>
          {jiraIntegrations.length === 0 && (
            <AlertCircle className="w-4 h-4 text-yellow-500" />
          )}
        </Button>

        {/* Import from Asana */}
        <Button
          onClick={() => handleImportClick('asana')}
          disabled={asanaIntegrations.length === 0}
          variant="ghost"
          className="flex items-center gap-3 w-full px-3 py-3 text-left hover:bg-accent/60 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <div className="w-8 h-8 bg-secondary/70 border border-border/60 rounded-xl flex items-center justify-center">
            <CheckSquare className="w-4 h-4 text-foreground" />
          </div>
          <div className="flex-1">
            <p className="font-medium text-foreground">Import from Asana</p>
            <p className="text-sm text-muted-foreground">
              {asanaIntegrations.length > 0 
                ? `${asanaIntegrations.length} account${asanaIntegrations.length !== 1 ? 's' : ''} connected`
                : 'No accounts connected'
              }
            </p>
          </div>
          {asanaIntegrations.length === 0 && (
            <AlertCircle className="w-4 h-4 text-yellow-500" />
          )}
        </Button>

        {/* No integrations message */}
        {integrations.length === 0 && (
          <>
            <div className="my-2 border-t border-border/60"></div>
            <div className="px-3 py-3">
              <div className="flex items-start gap-2 p-3 bg-secondary/60 rounded-xl border border-border/60">
                <AlertCircle className="w-4 h-4 text-saramsa-brand mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-foreground">
                    Connect your DevOps platforms
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Go to Settings {'>'} Integrations to connect Azure DevOps, Jira, or Asana
                  </p>
                  <a
                    href="/settings"
                    className="inline-flex items-center gap-1 text-xs text-saramsa-brand hover:underline mt-2"
                  >
                    Open Settings
                    <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </motion.div>
  );
}

