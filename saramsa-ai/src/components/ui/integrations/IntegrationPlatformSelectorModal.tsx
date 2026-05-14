"use client";

import { BaseModal } from "@/components/ui/modals/BaseModal";
import { DashboardPlatformSelection } from "@/components/ui/dashboard/DashboardPlatformSelection";
import type { WorkProvider } from "@/lib/providers";

interface IntegrationPlatformSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  onPlatformSelect: (platform: WorkProvider) => void;
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
      description="Connect your project to Azure DevOps, Jira, Asana, or Linear to push work items"
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
