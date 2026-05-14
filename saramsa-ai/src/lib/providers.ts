import type { LucideIcon } from "lucide-react";
import { CheckSquare, Cloud, Download, Globe, Triangle } from "lucide-react";

export type WorkProvider = "azure" | "jira" | "asana" | "linear";

type ProviderStatus = "available" | "configured";

interface ProviderSelectionContent {
  availableDescription: string;
  configuredDescription: string;
  features: string[];
  configuredCtaLabel: string;
  availableCtaLabel: string;
}

interface ProviderWorkItemBehavior {
  processTemplate: string;
  emptyCommentsMessage: string;
  missingWorkItemsMessage: string;
}

interface ProviderDefinition {
  id: WorkProvider;
  label: string;
  shortLabel: string;
  drawerTitle: string;
  icon: LucideIcon;
  projectBadgeIcon?: LucideIcon;
  projectBadgeGlyph?: string;
  capabilities: {
    supportsImport: boolean;
    supportsPush: boolean;
    supportsProjectSync: boolean;
    supportsWebhookBootstrap: boolean;
  };
  workItems: ProviderWorkItemBehavior;
  configSelection: ProviderSelectionContent;
  dashboardSelection: ProviderSelectionContent;
}

export const providerOrder: WorkProvider[] = ["azure", "jira", "asana", "linear"];

export const providerDefinitions: Record<WorkProvider, ProviderDefinition> = {
  azure: {
    id: "azure",
    label: "Azure DevOps",
    shortLabel: "Azure",
    drawerTitle: "Configure Azure DevOps",
    icon: Download,
    projectBadgeIcon: Cloud,
    capabilities: {
      supportsImport: true,
      supportsPush: true,
      supportsProjectSync: true,
      supportsWebhookBootstrap: false,
    },
    workItems: {
      processTemplate: "Agile",
      emptyCommentsMessage:
        "No comments available. Please upload feedback data to use Azure DevOps integration.",
      missingWorkItemsMessage:
        "No deep analysis data available. Please upload feedback data to generate work items.",
    },
    configSelection: {
      availableDescription:
        "Connect your Azure DevOps organization for seamless work item creation",
      configuredDescription: "Azure DevOps integration is already configured",
      features: [
        "AI-Powered Analysis",
        "Auto Work Item Creation",
        "Real-time Sync",
        "Team Assignment",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Azure",
    },
    dashboardSelection: {
      availableDescription:
        "Connect your project to Azure DevOps for seamless work item creation",
      configuredDescription:
        "Azure DevOps integration is already configured. You can reconfigure or link to a different organization.",
      features: [
        "AI-Powered Analysis",
        "Auto Work Item Creation",
        "Real-time Sync",
        "Team Assignment",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Azure",
    },
  },
  jira: {
    id: "jira",
    label: "Jira",
    shortLabel: "Jira",
    drawerTitle: "Configure Jira Integration",
    icon: Globe,
    projectBadgeGlyph: "J",
    capabilities: {
      supportsImport: true,
      supportsPush: true,
      supportsProjectSync: true,
      supportsWebhookBootstrap: false,
    },
    workItems: {
      processTemplate: "Agile",
      emptyCommentsMessage:
        "No comments available. Please upload feedback data to use Jira integration.",
      missingWorkItemsMessage:
        "No deep analysis data available. Please upload feedback data to generate work items.",
    },
    configSelection: {
      availableDescription:
        "Integrate with Jira for comprehensive project management",
      configuredDescription: "Jira integration is already configured",
      features: [
        "Dynamic Project Detection",
        "AI-Powered Issue Classification",
        "Automatic Priority Assignment",
        "Rich Issue Details",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Jira",
    },
    dashboardSelection: {
      availableDescription:
        "Integrate your project with Jira for comprehensive project management",
      configuredDescription:
        "Jira integration is already configured. You can reconfigure or link to a different workspace.",
      features: [
        "Dynamic Project Detection",
        "AI-Powered Issue Classification",
        "Automatic Priority Assignment",
        "Rich Issue Details",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Jira",
    },
  },
  asana: {
    id: "asana",
    label: "Asana",
    shortLabel: "Asana",
    drawerTitle: "Configure Asana Integration",
    icon: CheckSquare,
    projectBadgeIcon: CheckSquare,
    capabilities: {
      supportsImport: true,
      supportsPush: true,
      supportsProjectSync: true,
      supportsWebhookBootstrap: true,
    },
    workItems: {
      processTemplate: "Agile",
      emptyCommentsMessage:
        "No comments available. Please upload feedback data to use Asana integration.",
      missingWorkItemsMessage:
        "No deep analysis data available. Please upload feedback data to generate tasks.",
    },
    configSelection: {
      availableDescription:
        "Connect Asana to mirror customer insights into task planning",
      configuredDescription: "Asana integration is already configured",
      features: [
        "Workspace Import",
        "Project-level Mapping",
        "Task Sync Readiness",
        "Webhook-backed Updates",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Asana",
    },
    dashboardSelection: {
      availableDescription:
        "Integrate your project with Asana for mapped task creation and sync readiness",
      configuredDescription:
        "Asana integration is already configured. You can link another workspace project.",
      features: [
        "Workspace Detection",
        "Project Import",
        "Task Mapping",
        "Webhook Bootstrap",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Asana",
    },
  },
  linear: {
    id: "linear",
    label: "Linear",
    shortLabel: "Linear",
    drawerTitle: "Configure Linear Integration",
    icon: Triangle,
    projectBadgeIcon: Triangle,
    capabilities: {
      supportsImport: true,
      supportsPush: true,
      supportsProjectSync: true,
      supportsWebhookBootstrap: false,
    },
    workItems: {
      processTemplate: "Agile",
      emptyCommentsMessage:
        "No comments available. Please upload feedback data to use Linear integration.",
      missingWorkItemsMessage:
        "No deep analysis data available. Please upload feedback data to generate issues.",
    },
    configSelection: {
      availableDescription:
        "Connect Linear so triaged feedback turns into team-ready issues",
      configuredDescription: "Linear integration is already configured",
      features: [
        "Personal API Key Auth",
        "Team-level Mapping",
        "Auto Issue Creation",
        "Markdown Descriptions",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Linear",
    },
    dashboardSelection: {
      availableDescription:
        "Integrate your project with Linear so feedback flows into team issue queues",
      configuredDescription:
        "Linear integration is already configured. You can link another team.",
      features: [
        "Team Detection",
        "Issue Creation",
        "Priority Mapping",
        "Markdown Descriptions",
      ],
      configuredCtaLabel: "Review setup",
      availableCtaLabel: "Connect Linear",
    },
  },
};

export function getProviderLabel(provider: WorkProvider): string {
  return providerDefinitions[provider].label;
}

export function getProviderShortLabel(provider: WorkProvider): string {
  return providerDefinitions[provider].shortLabel;
}

export function getProviderDrawerTitle(provider: WorkProvider): string {
  return providerDefinitions[provider].drawerTitle;
}

export function getProviderProcessTemplate(provider: WorkProvider): string {
  return providerDefinitions[provider].workItems.processTemplate;
}

export function getProviderEmptyCommentsMessage(provider: WorkProvider): string {
  return providerDefinitions[provider].workItems.emptyCommentsMessage;
}

export function getProviderMissingWorkItemsMessage(provider: WorkProvider): string {
  return providerDefinitions[provider].workItems.missingWorkItemsMessage;
}

export function getProviderStatus(
  provider: WorkProvider,
  activeProviders: Iterable<string>
): ProviderStatus {
  return new Set(activeProviders).has(provider) ? "configured" : "available";
}

export function getProviderSelectionCards(
  variant: "configSelection" | "dashboardSelection",
  activeProviders: Iterable<string>
) {
  const activeProviderSet = new Set(activeProviders);

  return providerOrder.map((provider) => {
    const definition = providerDefinitions[provider];
    const status: ProviderStatus = activeProviderSet.has(provider)
      ? "configured"
      : "available";
    const content = definition[variant];

    return {
      id: provider,
      label: definition.label,
      icon: definition.icon,
      status,
      description:
        status === "configured"
          ? content.configuredDescription
          : content.availableDescription,
      features: content.features,
      ctaLabel:
        status === "configured"
          ? content.configuredCtaLabel
          : content.availableCtaLabel,
    };
  });
}

export function isWorkProvider(value: string): value is WorkProvider {
  return value === "azure" || value === "jira" || value === "asana" || value === "linear";
}
