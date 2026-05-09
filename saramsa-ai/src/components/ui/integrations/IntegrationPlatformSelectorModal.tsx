"use client";

import { BaseModal } from "@/components/ui/modals/BaseModal";
import { DashboardPlatformSelection } from "@/components/ui/dashboard/DashboardPlatformSelection";

interface IntegrationPlatformSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  onPlatformSelect: (platform: "azure" | "jira" | "asana") => void;
}

export function IntegrationPlatformSelectorModal({
  isOpen,
  onClose,
  onPlatformSelect,
}: IntegrationPlatformSelectorModalProps) {
  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      title="Configure Integration"
      description="Connect your project to Azure DevOps, Jira, or Asana to push work items"
      size="md"
      className="max-h-[90vh] overflow-y-auto"
    >
      <DashboardPlatformSelection
        onPlatformSelect={onPlatformSelect}
        className="-m-6"
      />
    </BaseModal>
  );
}
