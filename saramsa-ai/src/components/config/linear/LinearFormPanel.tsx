'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Eye,
  EyeOff,
  Loader2,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Shield,
  ArrowRight,
  ArrowLeft,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

interface LinearTeam {
  id: string;
  key: string;
  name: string;
  description?: string;
  url?: string;
}

interface LinearFormPanelProps {
  config: {
    apiKey: string;
    teamId: string;
    teamKey: string;
  };
  onConfigChange: (field: string, value: string) => void;
  onValidateConfiguration: () => void;
  isLoading: boolean;
  isCreatingProject?: boolean;
  error: string;
  projects: LinearTeam[];
  selectedProject: string;
  onProjectSelect: (projectId: string) => void;
  onContinue: () => void;
  onBack: () => void;
  validationStatus: 'idle' | 'loading' | 'success' | 'error';
  isExistingIntegration?: boolean;
  linkedProjects?: { [key: string]: { id: string; name: string } };
}

export const LinearFormPanel = ({
  config,
  onConfigChange,
  onValidateConfiguration,
  isLoading,
  isCreatingProject = false,
  error,
  projects,
  selectedProject,
  onProjectSelect,
  onContinue,
  onBack,
  validationStatus,
  isExistingIntegration = false,
  linkedProjects = {},
}: LinearFormPanelProps) => {
  const router = useRouter();
  const [showApiKey, setShowApiKey] = useState(false);
  const [isTokenGuideOpen, setIsTokenGuideOpen] = useState(false);

  const toggleApiKeyVisibility = () => setShowApiKey(!showApiKey);

  const handleLinkedProjectClick = async (projectId: string) => {
    try {
      const { encryptProjectId } = await import('@/lib/encryption');
      const encryptedId = encryptProjectId(projectId);
      router.push(`/projects/${encryptedId}/dashboard/`);
    } catch (error) {
      console.error('Navigation error:', error);
      router.push(`/projects/${projectId}/dashboard/`);
    }
  };

  const tokenSteps = [
    {
      step: 1,
      title: "Open Linear Settings > API > Personal API keys",
      description: "Sign in to Linear and navigate to your account API settings."
    },
    {
      step: 2,
      title: "Click 'Create key' and give it a label",
      description: "Use a descriptive name like 'Saramsa Integration' so it's easy to revoke later."
    },
    {
      step: 3,
      title: "Copy the key and paste it here",
      description: "Linear shows the key only once. Save it securely and paste it in the field above."
    }
  ];

  return (
    <div className="relative w-full max-w-2xl space-y-8 mx-auto pb-16">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="space-y-6"
      >
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="flex justify-center sm:justify-start"
        >
          <Button
            onClick={onBack}
            variant="outline"
            className="w-full sm:w-auto h-12 gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Platform Selection
          </Button>
        </motion.div>

        <div className="bg-card/80 dark:bg-card/90 backdrop-blur-sm border-2 border-border/60 dark:border-border/60 hover:border-saramsa-brand/30 transition-all duration-300 rounded-xl p-6">
          {!isExistingIntegration && (
            <>
              <div className="space-y-2 mb-6">
                <label htmlFor="apiKey" className="text-sm font-medium text-muted-foreground dark:text-muted-foreground">
                  Linear API Key
                </label>
                <div className="relative">
                  <Input
                    id="apiKey"
                    type={showApiKey ? "text" : "password"}
                    value={config.apiKey}
                    onChange={(e) => onConfigChange('apiKey', e.target.value)}
                    placeholder="lin_api_..."
                    className="h-12 bg-background/80 border-2 border-border/60 dark:border-border/60 hover:border-saramsa-brand/50 focus:border-saramsa-brand/50 focus:ring-2 focus:ring-saramsa-brand/20 transition-all duration-300 rounded-xl px-3 pr-12"
                  />
                  <Button
                    type="button"
                    onClick={toggleApiKeyVisibility}
                    variant="ghost"
                    size="icon"
                    className="absolute right-1 top-1 h-10 w-10 text-muted-foreground hover:text-saramsa-brand"
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground dark:text-muted-foreground">
                  Personal API keys are scoped to your Linear account and workspace.
                </p>
              </div>

              <div className="mb-6">
                <Button
                  onClick={() => setIsTokenGuideOpen(!isTokenGuideOpen)}
                  variant="outline"
                  className="w-full h-auto text-left justify-between px-3 py-3 border border-saramsa-brand/20 hover:border-saramsa-brand/40 hover:bg-saramsa-brand/5"
                >
                  <div className="flex items-center gap-2">
                    <ExternalLink className="w-4 h-4 text-saramsa-brand" />
                    <span className="font-medium text-foreground dark:text-foreground">
                      How to generate a Linear API key
                    </span>
                  </div>
                  {isTokenGuideOpen ? (
                    <ChevronUp className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  )}
                </Button>

                <AnimatePresence>
                  {isTokenGuideOpen && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.3 }}
                      className="space-y-4 mt-4"
                    >
                      {tokenSteps.map((step, index) => (
                        <motion.div
                          key={step.step}
                          initial={{ opacity: 0, x: -20 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.3, delay: index * 0.1 }}
                          className="flex gap-3 p-3 bg-secondary/40 dark:bg-secondary/40 rounded-xl"
                        >
                          <div className="flex-shrink-0 w-6 h-6 bg-saramsa-brand text-white rounded-full flex items-center justify-center text-xs font-bold">
                            {step.step}
                          </div>
                          <div className="space-y-1">
                            <p className="text-sm font-medium text-foreground dark:text-foreground">
                              {step.title}
                            </p>
                            <p className="text-xs text-muted-foreground dark:text-muted-foreground">
                              {step.description}
                            </p>
                          </div>
                        </motion.div>
                      ))}

                      <div className="mt-4 p-3 bg-secondary/60 rounded-xl border border-border/60">
                        <div className="flex items-start gap-2">
                          <ExternalLink className="w-4 h-4 text-saramsa-brand mt-0.5" />
                          <div className="space-y-1">
                            <p className="text-sm font-medium text-foreground dark:text-foreground">
                              Quick Access
                            </p>
                            <a
                              href="https://linear.app/settings/account/security"
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-saramsa-brand dark:text-muted-foreground hover:underline"
                            >
                              Go directly to: https://linear.app/settings/account/security
                            </a>
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </>
          )}

          {isExistingIntegration && (
            <div className="mb-6 p-4 bg-green-50 dark:bg-green-900/20 rounded-xl border border-green-200 dark:border-green-800">
              <div className="flex items-center gap-2">
                <CheckCircle className="w-5 h-5 text-green-600" />
                <div>
                  <p className="text-sm font-medium text-green-700 dark:text-green-400">
                    Linear Integration Connected
                  </p>
                  <p className="text-xs text-green-600 dark:text-green-300">
                    Pick a team to push generated issues into.
                  </p>
                </div>
              </div>
            </div>
          )}

          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.3 }}
                className="p-3 bg-red-50 dark:bg-red-900/20 rounded-xl border border-red-200 dark:border-red-800 mb-6"
              >
                <div className="flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-red-600" />
                  <p className="text-sm text-red-700 dark:text-red-400">
                    {error}
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {projects && projects.length > 0 && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                transition={{ duration: 0.3 }}
                className="space-y-4"
              >
                <div className="p-3 bg-green-50 dark:bg-green-900/20 rounded-xl border border-green-200 dark:border-green-800">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-600" />
                    <p className="text-sm font-medium text-green-700 dark:text-green-400">
                      Successfully connected! Found {projects?.length || 0} teams
                    </p>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium text-muted-foreground dark:text-muted-foreground">
                    Select Linear Team
                  </label>

                  {Object.keys(linkedProjects).length > 0 && (
                    <div className="p-3 bg-secondary/60 rounded-xl border border-border/60">
                      <p className="text-xs text-foreground dark:text-muted-foreground">
                        <strong>{Object.keys(linkedProjects).length}</strong> team(s) already linked.
                        Click on a linked team to go to its dashboard.
                      </p>
                    </div>
                  )}

                  <div className="max-h-48 overflow-y-auto scrollbar-thin">
                    <div className="space-y-2 p-1">
                      {projects?.map((project) => {
                        const linkedProject = linkedProjects[project.id];
                        const isAlreadyLinked = !!linkedProject;

                        return (
                          <div
                            key={project.id}
                            className={`p-3 border rounded-xl transition-all ${
                              isAlreadyLinked
                                ? "cursor-pointer border-orange-200 dark:border-orange-800 bg-orange-50 dark:bg-orange-900/20 hover:border-orange-300 dark:hover:border-orange-700"
                                : selectedProject === project.id
                                ? "border-saramsa-brand/60 bg-saramsa-brand/10 dark:bg-saramsa-brand/20 cursor-pointer"
                                : "border-border/60 dark:border-border/60 hover:border-saramsa-brand/60/50 cursor-pointer"
                            }`}
                            onClick={() => isAlreadyLinked ? handleLinkedProjectClick(linkedProject.id) : onProjectSelect(project.id)}
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-3 flex-1">
                                <div className="flex-shrink-0 w-5 h-5 rounded bg-saramsa-brand text-white flex items-center justify-center">
                                  <span className="text-xs font-bold">L</span>
                                </div>
                                <div className="flex-1 min-w-0">
                                  <div className="font-medium text-foreground dark:text-foreground">
                                    {project.name}
                                  </div>
                                  <div className="text-sm text-muted-foreground">
                                    Key: {project.key}
                                  </div>
                                  {isAlreadyLinked && (
                                    <div className="text-xs text-orange-600 dark:text-orange-400 mt-1 flex items-center gap-1">
                                      Already linked to "{linkedProject.name}"
                                      <ArrowRight className="w-3 h-3" />
                                    </div>
                                  )}
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                {selectedProject === project.id && !isAlreadyLinked && (
                                  <CheckCircle className="w-4 h-4 text-saramsa-brand" />
                                )}
                                {isAlreadyLinked && (
                                  <span className="text-xs px-2 py-1 bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400 rounded whitespace-nowrap">
                                    Linked
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground dark:text-muted-foreground">
                    Issues created from feedback will land in this Linear team.
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {!projects || projects.length === 0 ? (
            <Button
              onClick={onValidateConfiguration}
              disabled={(!isExistingIntegration && !config.apiKey) || isLoading}
              variant="saramsa"
              className="w-full h-12 group"
            >
              {isLoading ? (
                <div className="flex items-center gap-3 justify-center">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span>{isExistingIntegration ? 'Fetching Teams...' : 'Validating...'}</span>
                </div>
              ) : (
                <div className="flex items-center gap-3 justify-center">
                  <Shield className="w-5 h-5 group-hover:scale-110 transition-transform" />
                  <span>{isExistingIntegration ? 'Fetch Teams' : 'Validate Configuration'}</span>
                </div>
              )}
            </Button>
          ) : (
            <Button
              onClick={onContinue}
              disabled={!selectedProject || isCreatingProject}
              variant="saramsa"
              className="w-full h-12 group"
            >
              <div className="flex items-center gap-3 justify-center">
                {isCreatingProject ? (
                  <>
                    <Loader2 className="w-5 h-5 animate-spin" />
                    <span>Creating Project...</span>
                  </>
                ) : (
                  <>
                    <span>Continue to Dashboard</span>
                    <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                  </>
                )}
              </div>
            </Button>
          )}

          {projects && projects.length > 0 && !selectedProject && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-center text-xs text-muted-foreground dark:text-muted-foreground mt-2"
            >
              Please select a team to continue
            </motion.p>
          )}
        </div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.3 }}
        className="text-center space-y-2"
      >
        <div className="w-full h-px bg-gradient-to-r from-transparent via-border/70 to-transparent" />
        <div className="flex items-center justify-center gap-2">
          <Shield className="w-4 h-4 text-muted-foreground" />
          <p className="text-xs text-muted-foreground dark:text-muted-foreground">
            Your API key is encrypted and never stored without your permission.
          </p>
        </div>
      </motion.div>

      <div className="absolute -top-10 -right-10 w-20 h-20 bg-gradient-to-br from-saramsa-brand/20 to-saramsa-gradient-to/20 rounded-full blur-xl animate-float" />
      <div className="absolute -bottom-10 -left-10 w-16 h-16 bg-gradient-to-br from-saramsa-gradient-to/20 to-saramsa-brand/20 rounded-full blur-xl animate-float" style={{ animationDelay: '2s' }} />
    </div>
  );
};
