"use client";

import { useState } from "react";
import { useSelector } from "react-redux";
import { motion } from "framer-motion";
import {
  Download,
  Globe,
  CheckSquare,
  Zap,
  Shield,
  ArrowRight,
  CheckCircle,
} from 'lucide-react';
import type { RootState } from "@/store/store";
import { Button } from "@/components/ui/button";

interface PlatformSelectionScreenProps {
  onPlatformSelect: (platform: "azure" | "jira" | "asana") => void;
  onSkipConfig?: () => void;
}

export function PlatformSelectionScreen({
  onPlatformSelect,
  onSkipConfig,
}: PlatformSelectionScreenProps) {
  const { accounts } = useSelector((state: RootState) => state.integrations);
  const [selectedPlatform, setSelectedPlatform] = useState<
    "azure" | "jira" | "asana" | null
  >(null);

  // Check if integrations already exist
  const hasAzureIntegration = accounts.some(
    (account) => account.provider === "azure"
  );
  const hasJiraIntegration = accounts.some(
    (account) => account.provider === "jira"
  );
  const hasAsanaIntegration = accounts.some(
    (account) => account.provider === "asana"
  );

  const platforms = [
    {
      id: "azure",
      name: "Azure DevOps",
      description: hasAzureIntegration
        ? "Azure DevOps integration is already configured"
        : "Connect your Azure DevOps organization for seamless work item creation",
      icon: <Download className="w-8 h-8" />,
      color: "from-saramsa-gradient-from to-saramsa-gradient-to",
      features: [
        "AI-Powered Analysis",
        "Auto Work Item Creation",
        "Real-time Sync",
        "Team Assignment",
      ],
      status: hasAzureIntegration ? "configured" : "available",
      comingSoon: false,
    },
    {
      id: "jira",
      name: "Jira",
      description: hasJiraIntegration
        ? "Jira integration is already configured"
        : "Integrate with Jira for comprehensive project management",
      icon: <Globe className="w-8 h-8" />,
      color: "from-saramsa-gradient-from to-saramsa-gradient-to",
      features: [
        "Dynamic Project Detection",
        "AI-Powered Issue Classification",
        "Automatic Priority Assignment",
        "Rich Issue Details",
      ],
      status: hasJiraIntegration ? "configured" : "available",
      comingSoon: false,
    },
    {
      id: "asana",
      name: "Asana",
      description: hasAsanaIntegration
        ? "Asana integration is already configured"
        : "Connect Asana to mirror customer insights into task planning",
      icon: <CheckSquare className="w-8 h-8" />,
      color: "from-saramsa-gradient-from to-saramsa-gradient-to",
      features: [
        "Workspace Import",
        "Project-level Mapping",
        "Task Sync Readiness",
        "Webhook-backed Updates",
      ],
      status: hasAsanaIntegration ? "configured" : "available",
      comingSoon: false,
    },
  ];

  const handlePlatformSelect = (platform: "azure" | "jira" | "asana") => {
    setSelectedPlatform(platform);
    // Add a small delay for animation
    setTimeout(() => {
      onPlatformSelect(platform);
    }, 300);
  };

  return (
    <div className="h-full overflow-y-auto bg-background">
      <div className="min-h-full w-full grid place-items-center px-4 py-8 sm:px-6 sm:py-10">
        <motion.div
          className="w-full max-w-2xl rounded-3xl border border-border/60 bg-card/60 p-6 shadow-sm backdrop-blur-sm sm:p-8"
          initial={{ opacity: 0, x: -50 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        >
          <div className="mx-auto w-full max-w-xl space-y-8">
            {/* Header */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="text-center space-y-4"
            >
              <div className="flex items-center gap-4 justify-center">
                <div className="w-10 h-10 bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to rounded-xl flex items-center justify-center shadow-lg">
                  <Zap className="w-4 h-4 text-white" />
                </div>
                <h1 className="text-2xl font-semibold text-foreground">
                  Choose Your Platform
                </h1>
              </div>

              <div className="space-y-2">
                <p className="text-muted-foreground leading-relaxed">
                  Integrate with Azure DevOps or Jira to unlock automated work
                  item creation and project management.
                </p>
              </div>
            </motion.div>

            {/* Platform Options */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.1 }}
              className="space-y-4"
            >
              <div className="grid grid-cols-1 gap-3">
                {platforms.map((platform, index) => (
                  <motion.div
                    key={platform.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.4, delay: 0.2 + index * 0.1 }}
                  >
                    <div
                      className={`relative bg-card/80 dark:bg-card/90 backdrop-blur-sm border-2 rounded-2xl p-6 transition-all duration-300 ${
                        selectedPlatform === platform.id
                          ? "border-saramsa-brand/60 shadow-[0_20px_50px_-35px_rgba(139,95,191,0.5)]"
                          : platform.status === "configured"
                          ? "border-green-300/70 dark:border-green-600/60 bg-green-50/60 dark:bg-green-900/20"
                          : platform.comingSoon
                          ? "border-border/60 dark:border-border/60 opacity-50"
                          : "border-border/60 dark:border-border/60 hover:border-saramsa-brand/40 hover:shadow-[0_20px_50px_-35px_rgba(15,23,42,0.55)] cursor-pointer"
                      }`}
                      onClick={() =>
                        !platform.comingSoon &&
                        handlePlatformSelect(platform.id as "azure" | "jira" | "asana")
                      }
                    >
                      {platform.status === "configured" && (
                        <div className="absolute top-3 right-3">
                          <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400">
                            <CheckCircle className="w-3 h-3 mr-1" />
                            Configured
                          </span>
                        </div>
                      )}
                      {platform.comingSoon && (
                        <div className="absolute top-3 right-3">
                          <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400">
                            Coming Soon
                          </span>
                        </div>
                      )}

                      <div className="flex items-start gap-4">
                        <div
                          className={`w-12 h-12 bg-gradient-to-br ${platform.color} rounded-xl flex items-center justify-center text-white`}
                        >
                          {platform.icon}
                        </div>

                        <div className="flex-1 space-y-3">
                          <div>
                            <h3 className="text-lg font-semibold text-foreground">
                              {platform.name}
                            </h3>
                            <p className="text-sm text-muted-foreground max-w-sm">
                              {platform.description}
                            </p>
                          </div>
                        </div>

                        {!platform.comingSoon &&
                          platform.status !== "configured" && (
                            <ArrowRight className="w-5 h-5 text-muted-foreground" />
                          )}
                        {platform.status === "configured" && (
                          <CheckCircle className="w-5 h-5 text-green-500" />
                        )}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            </motion.div>

            {/* Skip Configuration Option */}
            {onSkipConfig && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6, delay: 0.4 }}
                className="text-center"
              >
                <Button
                  onClick={onSkipConfig}
                  variant="link"
                  size="sm"
                  className="text-muted-foreground hover:text-saramsa-brand"
                >
                  Skip configuration and go to dashboard
                </Button>
              </motion.div>
            )}

            {/* Security Footer */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.6, delay: 0.3 }}
              className="text-center space-y-2"
            >
              <div className="w-full h-px bg-gradient-to-r from-transparent via-border/70 to-transparent" />
              <div className="flex items-center justify-center gap-2">
                <Shield className="w-4 h-4 text-muted-foreground" />
                <p className="text-xs text-muted-foreground">
                  Your credentials are encrypted and stored for future use.
                </p>
              </div>
            </motion.div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
