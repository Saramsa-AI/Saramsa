"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowLeft, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AsanaIntegrationForm } from "@/components/config/asana/AsanaIntegrationForm";
import { AzureDevOpsIntegrationForm } from "@/components/config/azure/AzureDevOpsIntegrationForm";
import { JiraIntegrationForm } from "@/components/config/jira/JiraIntegrationForm";
import { lockBodyScroll, unlockBodyScroll } from "@/lib/bodyScrollLock";

interface IntegrationConfigDrawerProps {
  platform: "jira" | "azure" | "asana" | null;
  projectId: string;
  open: boolean;
  onClose: () => void;
  onBackToSelector: () => void;
  onConfigured: (projectId?: string) => void;
  allowPlatformSelection: boolean;
}

export function IntegrationConfigDrawer({
  platform,
  projectId,
  open,
  onClose,
  onBackToSelector,
  onConfigured,
  allowPlatformSelection,
}: IntegrationConfigDrawerProps) {
  const modalRoot = typeof document !== "undefined" ? document.body : null;

  useEffect(() => {
    if (!open || !modalRoot) return;

    lockBodyScroll();

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };

    document.addEventListener("keydown", handleEscape);
    return () => {
      unlockBodyScroll();
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open, modalRoot, onClose]);

  if (!modalRoot || !open || !platform) return null;

  const title =
    platform === "azure"
      ? "Configure Azure DevOps"
      : platform === "asana"
      ? "Configure Asana Integration"
      : "Configure Jira Integration";

  return createPortal(
    <AnimatePresence>
      <motion.div
        key="integration-config-backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[900] bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <motion.aside
        key="integration-config-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 260 }}
        className="fixed top-0 right-0 z-[1000] h-[100vh] w-screen lg:w-[720px] flex flex-col bg-background border-l border-border/60 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border/60 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={onBackToSelector}
              disabled={!allowPlatformSelection}
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              aria-label="Back to platform selection"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          </div>

          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onClose}
            className="h-8 w-8 text-muted-foreground hover:text-foreground"
            aria-label="Close integration configuration"
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-0 lg:p-8">
          {platform === "azure" ? (
            <AzureDevOpsIntegrationForm onContinue={onConfigured} onBack={onBackToSelector} targetProjectId={projectId} />
          ) : platform === "asana" ? (
            <AsanaIntegrationForm onContinue={onConfigured} onBack={onBackToSelector} targetProjectId={projectId} />
          ) : (
            <JiraIntegrationForm onContinue={onConfigured} onBack={onBackToSelector} targetProjectId={projectId} />
          )}
        </div>
      </motion.aside>
    </AnimatePresence>,
    modalRoot
  );
}
