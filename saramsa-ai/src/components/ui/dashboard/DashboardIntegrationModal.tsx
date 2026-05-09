"use client";

import { useState } from "react";
import { IntegrationConfigDrawer } from "./IntegrationConfigDrawer";
import { IntegrationPlatformSelectorModal } from "@/components/ui/integrations/IntegrationPlatformSelectorModal";

interface DashboardIntegrationModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
}

export function DashboardIntegrationModal({
  isOpen,
  onClose,
  projectId,
}: DashboardIntegrationModalProps) {
  void projectId;
  const [selectedPlatform, setSelectedPlatform] = useState<'azure' | 'jira' | 'asana' | null>(null);

  const handlePlatformSelect = (platform: 'azure' | 'jira' | 'asana') => {
    setSelectedPlatform(platform);
    if (typeof window !== 'undefined') {
      localStorage.setItem('selected_platform', platform);
    }
  };

  const handleBackToSelector = () => {
    setSelectedPlatform(null);
  };

  const handleConfigComplete = () => {
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
        isOpen={isOpen && !selectedPlatform}
        onClose={handleClose}
        onPlatformSelect={handlePlatformSelect}
      />

      <IntegrationConfigDrawer
        platform={selectedPlatform}
        open={isOpen && !!selectedPlatform}
        onClose={handleClose}
        onBackToSelector={handleBackToSelector}
        onConfigured={handleConfigComplete}
      />
    </>
  );
}
