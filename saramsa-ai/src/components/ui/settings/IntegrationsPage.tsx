"use client";

import { useEffect, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { useRouter } from "next/navigation";
import type { AppDispatch, RootState } from "@/store/store";
import {
  fetchIntegrationAccounts,
  deleteIntegrationAccount,
  testIntegrationConnection,
  clearError,
  fetchExternalProjects,
  clearExternalProjects,
} from "@/store/features/integrations/integrationsSlice";
import { apiRequest } from "@/lib/apiRequest";
import { encryptProjectId } from "@/lib/encryption";
import { motion } from "framer-motion";
import {
  Plus,
  Settings,
  Trash2,
  TestTube,
  CheckCircle,
  XCircle,
  AlertCircle,
  AlertTriangle,
  ExternalLink,
  Loader2,
  Shield,
  Cloud,
  Download,
  Folder,
  Users,
  MessageSquare,
} from "lucide-react";
import { SlackIntegrationForm } from "@/components/ui/settings/SlackIntegrationForm";
import { DashboardIntegrationModal } from "@/components/ui/dashboard/DashboardIntegrationModal";
import { BaseModal } from "@/components/ui/modals/BaseModal";
import type { IntegrationAccount } from "@/store/features/integrations/integrationsSlice";
import { Button } from "@/components/ui/button";

export function IntegrationsPage() {
  const dispatch = useDispatch<AppDispatch>();
  const router = useRouter();
  const {
    accounts,
    loading,
    error,
    testingConnection,
    externalProjects,
    fetchingProjects,
  } = useSelector((state: RootState) => state.integrations);

  const [showDashboardIntegrationModal, setShowDashboardIntegrationModal] = useState(false);
  const [showSlackForm, setShowSlackForm] = useState(false);
  const [creatingProject, setCreatingProject] = useState<string | null>(null);
  const [accountPendingDeletion, setAccountPendingDeletion] = useState<IntegrationAccount | null>(null);
  const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);
  const [slackMessage, setSlackMessage] = useState<string | null>(null);

  // Check if integrations already exist
  const hasAzureIntegration = accounts.some(
    (account) => account.provider === "azure"
  );
  const hasJiraIntegration = accounts.some(
    (account) => account.provider === "jira"
  );
  const hasSlackIntegration = accounts.some(
    (account) => (account.provider as string) === "slack"
  );

  useEffect(() => {
    // Fetch integration accounts from the new API
    dispatch(fetchIntegrationAccounts());

    // Handle Slack OAuth callback redirect
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (params.get("slack_connected") === "true") {
        setSlackMessage("Slack workspace connected successfully!");
        window.history.replaceState({}, "", window.location.pathname);
      } else if (params.get("slack_error")) {
        setSlackMessage(`Slack connection failed: ${params.get("slack_error")}`);
        window.history.replaceState({}, "", window.location.pathname);
      }
    }
  }, [dispatch]);

  const handleTestConnection = async (accountId: string) => {
    await dispatch(testIntegrationConnection(accountId));
  };

  const handleOpenDashboardIntegrationModal = () => {
    setShowDashboardIntegrationModal(true);
  };

  const handleCloseDashboardIntegrationModal = () => {
    setShowDashboardIntegrationModal(false);
    dispatch(fetchIntegrationAccounts());
  };

  const handleDeleteAccount = (account: IntegrationAccount) => {
    setAccountPendingDeletion(account);
  };

  const closeDeleteModal = () => {
    if (deletingAccountId) return;
    setAccountPendingDeletion(null);
  };

  const handleConfirmDeleteAccount = async () => {
    if (!accountPendingDeletion) return;
    setDeletingAccountId(accountPendingDeletion.id);
    try {
      await dispatch(deleteIntegrationAccount(accountPendingDeletion.id)).unwrap();
      dispatch(clearExternalProjects());
    } catch (error: any) {
      console.error("Failed to delete integration account:", error);
      alert(error?.message || "Failed to delete integration. Please try again.");
    } finally {
      setDeletingAccountId(null);
      setAccountPendingDeletion(null);
    }
  };

  const handleFetchProjects = async (provider: "azure" | "jira") => {
    const account = accounts.find((acc) => acc.provider === provider);
    if (account) {
      await dispatch(
        fetchExternalProjects({ provider, accountId: account.id })
      );
    }
  };

  const handleCreateOrNavigateToProject = async (project: any) => {
    setCreatingProject(project.id);

    try {
      const account = accounts.find((acc) => acc.provider === project.provider);
      if (!account) {
        throw new Error("Integration account not found");
      }

      // Create the project using the same endpoint as config pages
      const res = await apiRequest(
        "post",
        "/integrations/projects/create/",
        {
          project_name: project.name,
          description: `Imported from ${
            project.provider === "azure" ? "Azure DevOps" : "Jira"
          }`,
          platform: project.provider === "azure" ? "azure_devops" : "jira",
          external_project_id: project.id,
          external_url: project.url || "",
          integration_account_id: account.id,
          ...(project.provider === "jira" &&
            project.key && { jira_project_key: project.key }),
          ...(project.provider === "azure" &&
            project.templateName && {
              azure_process_template: project.templateName,
            }),
        },
        true
      );

      if (!res.data.success) {
        throw new Error(res.data.detail || res.data.error || "Failed to create project");
      }

      const payload = res.data?.data ?? {};
      const createdProject = payload.project ?? payload;

      if (!createdProject?.id) {
        throw new Error("Project created but response did not include a project id");
      }

      // Store project info in localStorage for compatibility
      localStorage.setItem("project_id", createdProject.id);
      localStorage.setItem("selected_project_name", project.name);

      // Handle both new project creation and existing project navigation
      if (payload.already_exists || res.data.already_exists) {
        console.log("Project already exists, navigating to existing project");
      } else {
        console.log("Project created successfully, navigating to dashboard");
      }

      // Navigate to project dashboard
      const encryptedProjectId = encryptProjectId(createdProject.id);
      router.push(`/projects/${encryptedProjectId}/dashboard/`);
    } catch (error: any) {
      console.error("Failed to create/navigate to project:", error);
      alert(
        `Failed to create/navigate to project: ${
          error.message || "Unknown error"
        }`
      );
      router.push("/projects/");
    } finally {
      setCreatingProject(null);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "active":
        return <CheckCircle className="w-5 h-5 text-green-500" />;
      case "error":
        return <XCircle className="w-5 h-5 text-red-500" />;
      case "expired":
        return <AlertCircle className="w-5 h-5 text-yellow-500" />;
      default:
        return <AlertCircle className="w-5 h-5 text-muted-foreground" />;
    }
  };

  const getProviderIcon = (provider: string) => {
    switch (provider) {
      case "azure":
        return (
          <div className="w-8 h-8 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to rounded-xl flex items-center justify-center shadow-lg">
            <Cloud className="w-4 h-4 text-white" />
          </div>
        );
      case "jira":
        return (
          <div className="w-8 h-8 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to rounded-xl flex items-center justify-center shadow-lg">
            <span className="text-white text-sm font-bold">J</span>
          </div>
        );
      case "slack":
        return (
          <div className="w-8 h-8 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to rounded-xl flex items-center justify-center shadow-lg">
            <MessageSquare className="w-4 h-4 text-white" />
          </div>
        );
      default:
        return (
          <div className="w-8 h-8 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to rounded-xl flex items-center justify-center shadow-lg">
            <Settings className="w-4 h-4 text-white" />
          </div>
        );
    }
  };

  const getProviderLabel = (provider: string) => {
    switch (provider) {
      case "azure": return "Azure DevOps";
      case "jira": return "Jira Cloud";
      case "slack": return "Slack";
      default: return provider;
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Integrations</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Connect your DevOps platforms to import projects and sync work items.
          </p>
        </div>
        {accounts.length > 0 && (
          <div className="flex gap-2 flex-shrink-0">
            {(!hasAzureIntegration || !hasJiraIntegration) && (
              <Button
                onClick={handleOpenDashboardIntegrationModal}
                variant="saramsa"
                size="sm"
                className="flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Add integration
              </Button>
            )}
            {!hasSlackIntegration && (
              <Button
                onClick={() => setShowSlackForm(true)}
                variant="outline"
                size="sm"
                className="flex items-center gap-2"
              >
                <MessageSquare className="w-4 h-4" />
                Add Slack
              </Button>
            )}
          </div>
        )}
      </header>

      {/* Slack OAuth Message */}
      {slackMessage && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className={`rounded-xl p-4 ${
            slackMessage.includes("successfully")
              ? "bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800"
              : "bg-destructive/10 border border-destructive/20"
          }`}
        >
          <div className="flex items-center gap-2">
            {slackMessage.includes("successfully") ? (
              <CheckCircle className="w-5 h-5 text-green-600" />
            ) : (
              <XCircle className="w-5 h-5 text-destructive" />
            )}
            <span className={slackMessage.includes("successfully") ? "text-green-700 dark:text-green-400" : "text-destructive"}>
              {slackMessage}
            </span>
            <Button
              onClick={() => setSlackMessage(null)}
              variant="ghost"
              size="sm"
              className="ml-auto"
            >
              x
            </Button>
          </div>
        </motion.div>
      )}

      {/* Error Display */}
      {error && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-destructive/10 border border-destructive/20 rounded-xl p-4"
        >
          <div className="flex items-center gap-2">
            <XCircle className="w-5 h-5 text-destructive" />
            <span className="text-destructive">{error}</span>
            <Button
              onClick={() => dispatch(clearError())}
              variant="ghost"
              size="sm"
              className="ml-auto text-destructive hover:text-destructive/80"
            >
              x
            </Button>
          </div>
        </motion.div>
      )}

      {/* Integration Accounts */}
      <div className="space-y-4">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          </div>
        ) : accounts.length === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-center py-12 bg-muted/50 rounded-xl border-2 border-dashed border-border"
          >
            <Shield className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
            <h3 className="text-lg font-medium text-foreground mb-2">
              No integrations configured
            </h3>
            <p className="text-muted-foreground mb-6">
              Connect your Azure DevOps, Jira, or Slack accounts to start importing
              projects and feedback
            </p>
            <div className="flex gap-3 justify-center">
              {(!hasAzureIntegration || !hasJiraIntegration) && (
                <Button
                  onClick={handleOpenDashboardIntegrationModal}
                  variant="saramsa"
                  className="flex items-center gap-2 px-4 py-2"
                >
                  <Plus className="w-4 h-4" />
                  Configure Integration
                </Button>
              )}
              {!hasSlackIntegration && (
                <Button
                  onClick={() => setShowSlackForm(true)}
                  variant="outline"
                  className="flex items-center gap-2 px-4 py-2 border-saramsa-brand/20 hover:border-saramsa-brand/40 hover:bg-saramsa-brand/10"
                >
                  <MessageSquare className="w-4 h-4" />
                  Connect Slack
                </Button>
              )}
            </div>
          </motion.div>
        ) : (
          <div className="grid gap-4">
            {accounts.map((account, index) => (
              <motion.div
                key={account.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1 }}
                className="bg-card/80 dark:bg-card/90 backdrop-blur-sm border-2 border-border/60 dark:border-border/60 hover:border-saramsa-brand/30 transition-all duration-300 rounded-xl p-6"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    {getProviderIcon(account.provider)}
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-foreground dark:text-foreground">
                          {account.displayName}
                        </h3>
                        {getStatusIcon(account.status)}
                      </div>
                      <div className="flex items-center gap-4 mt-1 text-sm text-muted-foreground dark:text-muted-foreground">
                        <span>{getProviderLabel(account.provider)}</span>
                        <span>|</span>
                        <span>
                          Connected{" "}
                          {new Date(account.savedAt).toLocaleDateString()}
                        </span>
                        {account.metadata?.organization && (
                          <>
                            <span>|</span>
                            <span>{account.metadata.organization}</span>
                          </>
                        )}
                        {account.metadata?.domain && (
                          <>
                            <span>|</span>
                            <span>{account.metadata.domain}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {(account.provider as string) !== "slack" && (
                      <Button
                        onClick={() => handleFetchProjects(account.provider as "azure" | "jira")}
                        disabled={fetchingProjects[account.provider]}
                        variant="saramsa"
                        className="flex items-center gap-2 px-3 py-2 text-sm"
                      >
                        {fetchingProjects[account.provider] ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Download className="w-4 h-4" />
                        )}
                        Fetch Projects
                      </Button>
                    )}

                    <Button
                      onClick={() => handleTestConnection(account.id)}
                      disabled={testingConnection[account.id]}
                      variant="outline"
                      className="flex items-center gap-2 px-3 py-2 text-sm border-saramsa-brand/20 hover:border-saramsa-brand/40 hover:bg-saramsa-brand/10"
                    >
                      {testingConnection[account.id] ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <TestTube className="w-4 h-4" />
                      )}
                      Test
                    </Button>

                    {account.metadata?.baseUrl && (
                      <a
                        href={account.metadata?.baseUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2 px-3 py-2 text-sm border-2 border-saramsa-brand/20 hover:border-saramsa-brand/40 hover:bg-saramsa-brand/10 transition-all duration-300 rounded-xl"
                      >
                        <ExternalLink className="w-4 h-4" />
                        Open
                      </a>
                    )}

                    <Button
                      onClick={() => handleDeleteAccount(account)}
                      variant="outline"
                      className="flex items-center gap-2 px-3 py-2 text-sm text-destructive border-destructive/20 hover:bg-destructive/10"
                    >
                      <Trash2 className="w-4 h-4" />
                      Delete
                    </Button>
                  </div>
                </div>

                {/* Scopes */}
                <div className="mt-4 pt-4 border-t border-border/60 dark:border-border/60">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground dark:text-muted-foreground">
                    <span className="font-medium">Permissions:</span>
                    <div className="flex gap-2 flex-wrap">
                      {(account.scopes || []).map((scope) => (
                        <span
                          key={scope}
                          className="px-2 py-1 bg-saramsa-brand/10 text-saramsa-brand rounded text-xs font-medium"
                        >
                          {scope}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      {/* Projects Section */}
      {externalProjects.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-4"
        >
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xl font-semibold text-foreground">
                Available Projects
              </h3>
              <p className="text-muted-foreground text-sm mt-1">
                Click on any project to create or navigate to it
              </p>
            </div>
            <Button
              onClick={() => dispatch(clearExternalProjects())}
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
            >
              Clear
            </Button>
          </div>

          {/* Success Message */}
          <div className="p-4 bg-green-50 dark:bg-green-900/20 rounded-xl border border-green-200 dark:border-green-800">
            <div className="flex items-center gap-2">
              <CheckCircle className="w-5 h-5 text-green-600" />
              <p className="text-sm font-medium text-green-700 dark:text-green-400">
                Successfully connected! Found {externalProjects.length} projects
              </p>
            </div>
          </div>

          {/* Project Selection with Folder Icons */}
          <div className="space-y-3">
            <label className="text-sm font-medium text-muted-foreground dark:text-muted-foreground">
              Select Project to Import
            </label>
            <div className="grid gap-3 max-h-96 overflow-y-auto">
              {externalProjects.map((project, index) => (
                <motion.div
                  key={project.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.05 }}
                  className={`p-4 border-2 rounded-xl cursor-pointer transition-all duration-300 ${
                    creatingProject === project.id
                      ? "border-saramsa-brand/60 bg-saramsa-brand/10 dark:bg-saramsa-brand/20"
                      : "border-border/60 dark:border-border/60 hover:border-saramsa-brand/60/50 hover:bg-saramsa-brand/10"
                  }`}
                  onClick={() => handleCreateOrNavigateToProject(project)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="flex-shrink-0">
                        <Folder className="w-6 h-6 text-saramsa-brand" />
                      </div>
                      <div className="flex-1">
                        <div className="font-medium text-foreground dark:text-foreground text-base">
                          {project.name}
                        </div>
                        {project.description && (
                          <div className="text-sm text-muted-foreground dark:text-muted-foreground mt-1">
                            {project.description}
                          </div>
                        )}

                        {/* Project metadata */}
                        <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground dark:text-muted-foreground">
                          {project.key && (
                            <div className="flex items-center gap-1">
                              <span className="font-medium">Key:</span>
                              <span className="px-2 py-1 bg-saramsa-brand/10 text-saramsa-brand dark:bg-saramsa-brand/20 dark:text-saramsa-brand rounded">
                                {project.key}
                              </span>
                            </div>
                          )}
                          {project.templateName && (
                            <div className="flex items-center gap-1">
                              <Users className="w-3 h-3" />
                              <span className="px-2 py-1 bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 rounded">
                                {project.templateName}
                              </span>
                            </div>
                          )}
                          <div className="flex items-center gap-1">
                            <Cloud className="w-3 h-3" />
                            <span className="capitalize">
                              {(project as any).provider}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      {project.url && (
                        <a
                          href={project.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="flex items-center gap-1 px-3 py-2 text-sm border-2 border-saramsa-brand/20 hover:border-saramsa-brand/40 hover:bg-saramsa-brand/10 transition-all duration-300 rounded-xl"
                        >
                          <ExternalLink className="w-4 h-4" />
                          Open
                        </a>
                      )}

                      <div className="flex items-center gap-2">
                        {creatingProject === project.id && (
                          <Loader2 className="w-4 h-4 animate-spin text-saramsa-brand" />
                        )}
                        {creatingProject !== project.id && (
                          <CheckCircle className="w-5 h-5 text-saramsa-brand" />
                        )}
                      </div>
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground dark:text-muted-foreground">
              Click on any project to create or navigate to it in your dashboard
            </p>
          </div>
        </motion.div>
      )}

      {/* Integration Forms */}
      <DashboardIntegrationModal
        isOpen={showDashboardIntegrationModal}
        onClose={handleCloseDashboardIntegrationModal}
        projectId=""
      />

      {showSlackForm && (
        <SlackIntegrationForm
          onClose={() => setShowSlackForm(false)}
          onSuccess={() => {
            setShowSlackForm(false);
            dispatch(fetchIntegrationAccounts());
          }}
        />
      )}

      {accountPendingDeletion && (
        <BaseModal
          isOpen={!!accountPendingDeletion}
          onClose={closeDeleteModal}
          size="sm"
          icon={
            <div className="w-12 h-12 rounded-2xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
              <AlertTriangle className="w-6 h-6 text-red-600 dark:text-red-400" />
            </div>
          }
          title="Delete integration"
          description={
            <>
              Deleting <strong>{accountPendingDeletion.displayName}</strong> will remove the{" "}
              {getProviderLabel(accountPendingDeletion.provider)} connection and all related data.
            </>
          }
          footer={
            <div className="flex items-center justify-end gap-3">
              <Button
                type="button"
                onClick={closeDeleteModal}
                disabled={!!deletingAccountId}
                variant="outline"
                className="px-4 py-2 text-muted-foreground dark:text-muted-foreground"
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleConfirmDeleteAccount}
                disabled={!!deletingAccountId}
                variant="destructive"
                className="flex items-center gap-2 px-4 py-2"
              >
                {deletingAccountId ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete integration
                  </>
                )}
              </Button>
            </div>
          }
        >
          <div className="space-y-3 text-sm text-muted-foreground dark:text-muted-foreground">
            <p>
              This action will also delete every project created through this integration along with their analysis history and AI-generated work items.
            </p>
            <ul className="list-disc list-inside space-y-1 text-muted-foreground dark:text-muted-foreground">
              <li>Associated Saramsa projects</li>
              <li>Stored analysis results</li>
              <li>Work items generated from those projects</li>
            </ul>
            <p className="text-red-600 dark:text-red-400 font-medium">This cannot be undone.</p>
          </div>
        </BaseModal>
      )}
    </div>
  );
}
