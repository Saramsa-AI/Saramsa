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
  Sparkles,
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
      badgeClass:
        "bg-secondary/70 text-foreground border border-border/60",
      ctaLabel: hasAzureIntegration ? "Review setup" : "Connect Azure",
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
      badgeClass:
        "bg-secondary/70 text-foreground border border-border/60",
      ctaLabel: hasJiraIntegration ? "Review setup" : "Connect Jira",
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
      badgeClass:
        "bg-secondary/70 text-foreground border border-border/60",
      ctaLabel: hasAsanaIntegration ? "Review setup" : "Connect Asana",
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
          className="w-full max-w-4xl rounded-[2rem] border border-border/60 bg-[radial-gradient(circle_at_top,rgba(255,118,72,0.08),transparent_30%),rgba(12,12,12,0.92)] p-6 shadow-[0_40px_90px_-55px_rgba(0,0,0,0.8)] backdrop-blur-sm sm:p-8"
          initial={{ opacity: 0, x: -50 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        >
          <div className="mx-auto w-full max-w-3xl space-y-8">
            {/* Header */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="space-y-5 text-center"
            >
              <div className="flex items-center justify-center gap-4">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-saramsa-gradient-from to-saramsa-gradient-to shadow-lg shadow-saramsa-brand/25">
                  <Zap className="w-4 h-4 text-white" />
                </div>
                <span className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-secondary/40 px-3 py-1 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                  <Sparkles className="h-3.5 w-3.5 text-saramsa-brand" />
                  Workspace setup
                </span>
              </div>

              <div className="space-y-3">
                <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                  Connect the platform your team already works in
                </h1>
                <p className="mx-auto max-w-2xl text-sm leading-7 text-muted-foreground sm:text-base">
                  Choose one integration to bring customer feedback, triage, and work item creation into the same operational flow. Existing connections stay reusable across future projects.
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
              <div className="grid grid-cols-1 gap-4">
                {platforms.map((platform, index) => (
                  <motion.div
                    key={platform.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.4, delay: 0.2 + index * 0.1 }}
                  >
                    <div
                      className={`relative overflow-hidden rounded-[1.75rem] border p-6 transition-all duration-300 ${
                        selectedPlatform === platform.id
                          ? "border-saramsa-brand/55 bg-card shadow-[0_30px_70px_-45px_rgba(139,95,191,0.45)]"
                          : platform.status === "configured"
                          ? "border-border/80 bg-card/90 shadow-[0_18px_45px_-42px_rgba(255,118,72,0.25)]"
                          : platform.comingSoon
                          ? "border-border/60 opacity-50"
                          : "border-border/60 bg-card/80 hover:border-saramsa-brand/35 hover:bg-card/95 hover:shadow-[0_18px_40px_-34px_rgba(15,23,42,0.55)]"
                      }`}
                    >
                      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
                      {platform.comingSoon && (
                        <div className="absolute top-3 right-3">
                          <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400">
                            Coming Soon
                          </span>
                        </div>
                      )}

                      <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex items-start gap-4">
                        <div
                          className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br ${platform.color} text-white shadow-lg shadow-saramsa-brand/20`}
                        >
                          {platform.icon}
                        </div>

                        <div className="flex-1 space-y-4">
                          <div className="space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="text-xl font-semibold text-foreground">
                                {platform.name}
                              </h3>
                              {platform.status === "configured" && (
                                <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium ${platform.badgeClass}`}>
                                  <CheckCircle className="w-3 h-3 mr-1" />
                                  Configured
                                </span>
                              )}
                            </div>
                            <p className="max-w-xl text-sm leading-6 text-muted-foreground">
                              {platform.description}
                            </p>
                          </div>

                          <div className="flex flex-wrap gap-2">
                            {platform.features.map((feature) => (
                              <span
                                key={feature}
                                className="inline-flex items-center rounded-full border border-border/60 bg-secondary/30 px-3 py-1 text-xs font-medium text-muted-foreground"
                              >
                                {feature}
                              </span>
                            ))}
                          </div>
                        </div>
                        </div>

                        <div className="flex shrink-0 items-center sm:pl-4">
                          {!platform.comingSoon && (
                            <Button
                              type="button"
                              variant={platform.status === "configured" ? "outline" : "saramsa"}
                              className="min-w-[152px] justify-center"
                              onClick={() =>
                                handlePlatformSelect(platform.id as "azure" | "jira" | "asana")
                              }
                            >
                              {platform.ctaLabel}
                              <ArrowRight className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
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
