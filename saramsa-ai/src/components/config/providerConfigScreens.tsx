"use client";

import type { ComponentType } from "react";
import { AsanaConfigScreen } from "@/components/config/asana/AsanaConfigScreen";
import { AzureDevOpsConfigScreen } from "@/components/config/azure/AzureDevOpsConfigScreen";
import { JiraConfigScreen } from "@/components/config/jira/JiraConfigScreen";
import type { WorkProvider } from "@/lib/providers";

interface ProviderConfigScreenProps {
  onContinue: (projectId?: string) => void;
  onBack: () => void;
}

export const providerConfigScreens: Record<
  WorkProvider,
  ComponentType<ProviderConfigScreenProps>
> = {
  azure: AzureDevOpsConfigScreen,
  jira: JiraConfigScreen,
  asana: AsanaConfigScreen,
};
