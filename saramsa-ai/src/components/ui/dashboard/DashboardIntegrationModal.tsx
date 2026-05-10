"use client";

import { useEffect, useState } from "react";
import { IntegrationConfigDrawer } from "./IntegrationConfigDrawer";
import { IntegrationPlatformSelectorModal } from "@/components/ui/integrations/IntegrationPlatformSelectorModal";

interface DashboardIntegrationModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  initialPlatform?: 'azure' | 'jira' | 'asana' | null;
}

export function DashboardIntegrationModal({
  isOpen,
  onClose,
  projectId,
  initialPlatform = null,
}: DashboardIntegrationModalProps) {
  const [selectedPlatform, setSelectedPlatform] = useState<'azure' | 'jira' | 'asana' | null>(null);
  const allowPlatformSelection = !initialPlatform;

  useEffect(() => {
    if (!isOpen) {
      setSelectedPlatform(null);
      return;
    }

    if (initialPlatform) {
      setSelectedPlatform(initialPlatform);
    }
  }, [isOpen, initialPlatform]);

  const handlePlatformSelect = (platform: 'azure' | 'jira' | 'asana') => {
    setSelectedPlatform(platform);
  };

  const handleBackToSelector = () => {
    if (!allowPlatformSelection) {
      onClose();
      return;
    }
    setSelectedPlatform(null);
  };

  const handleConfigComplete = (_projectId?: string) => {
    setSelectedPlatform(null);
    onClose();
  };

  const handleClose = () => {
    setSelectedPlatform(null);
    onClose();
  };

  return (
    <>
      <IntegrationPlatformSelectorModal
        isOpen={isOpen && allowPlatformSelection && !selectedPlatform}
        onClose={handleClose}
        onPlatformSelect={handlePlatformSelect}
      />

      <IntegrationConfigDrawer
        platform={selectedPlatform}
        projectId={projectId}
        open={isOpen && !!selectedPlatform}
        onClose={handleClose}
        onBackToSelector={handleBackToSelector}
        onConfigured={handleConfigComplete}
        allowPlatformSelection={allowPlatformSelection}
      />
    </>
  );
}
