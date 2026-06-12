"use client";

import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import { motion } from "framer-motion";
import {
  ArrowRight,
  CheckCircle,
  Shield,
} from 'lucide-react';
import type { AppDispatch, RootState } from "@/store/store";
import { fetchIntegrationAccounts } from "@/store/features/integrations/integrationsSlice";
import {
  getProviderSelectionCards,
  type WorkProvider,
} from "@/lib/providers";

interface DashboardPlatformSelectionProps {
  onPlatformSelect: (platform: WorkProvider) => void;
  className?: string;
}

export function DashboardPlatformSelection({
  onPlatformSelect,
  className = "",
}: DashboardPlatformSelectionProps) {
  const dispatch = useDispatch<AppDispatch>();
  const { accounts } = useSelector((state: RootState) => state.integrations);

  useEffect(() => {
    // Fetch existing integrations to check status
    dispatch(fetchIntegrationAccounts());
  }, [dispatch]);

  const activeProviders = accounts
    .filter((account) => account.status === "active")
    .map((account) => account.provider);
  const platforms = getProviderSelectionCards(
    "dashboardSelection",
    activeProviders
  );

  return (
    <div className={`p-6 ${className}`}>
      <div className="max-w-2xl mx-auto space-y-6">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="text-center space-y-3"
        >
          <h2 className="text-xl font-semibold text-foreground dark:text-foreground">
            Choose Your Integration Platform
          </h2>
          <p className="text-sm text-muted-foreground dark:text-muted-foreground">
            Connect this project to a delivery platform to enable automated work item creation and synchronization.
          </p>
        </motion.div>

        {/* Platform Options */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="space-y-3"
        >
          {platforms.map((platform, index) => (
            <motion.div
              key={platform.id}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.4, delay: 0.2 + index * 0.1 }}
            >
              <div
                className={`relative bg-card/80 border-2 rounded-xl p-5 transition-all duration-300 cursor-pointer hover:border-border/70 hover:shadow-sm ${
                  platform.status === "configured"
                    ? "border-saramsa-brand/25 bg-card/85 shadow-[0_16px_40px_-34px_rgba(139,95,191,0.4)]"
                    : "border-border/60"
                }`}
                onClick={() => onPlatformSelect(platform.id)}
              >
                {platform.status === "configured" && (
                  <div className="absolute top-3 right-3">
                    <span className="inline-flex items-center rounded-full border border-border/60 bg-secondary/70 px-2.5 py-1 text-xs font-medium text-foreground">
                      <CheckCircle className="w-3 h-3 mr-1" />
                      Configured
                    </span>
                  </div>
                )}

                <div className="flex items-start gap-4">
                  <div
                    className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl border border-border/60 bg-secondary/70 text-foreground"
                  >
                    <platform.icon className="w-8 h-8" />
                  </div>

                  <div className="flex-1 space-y-2">
                    <div>
                      <h3 className="text-lg font-semibold text-foreground dark:text-foreground">
                        {platform.label}
                      </h3>
                      <p className="text-sm text-muted-foreground dark:text-muted-foreground">
                        {platform.description}
                      </p>
                    </div>

                    {/* Features */}
                    <div className="flex flex-wrap gap-2">
                      {platform.features.map((feature) => (
                        <span
                          key={feature}
                          className="inline-flex items-center px-2 py-1 rounded-md text-xs font-medium bg-secondary/40 text-muted-foreground dark:bg-secondary/40 dark:text-muted-foreground"
                        >
                          {feature}
                        </span>
                      ))}
                    </div>
                  </div>

                  <ArrowRight className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-1" />
                </div>
              </div>
            </motion.div>
          ))}
        </motion.div>

        {/* Security Footer */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.3 }}
          className="text-center space-y-2 pt-4"
        >
          <div className="w-full h-px bg-border/70" />
          <div className="flex items-center justify-center gap-2">
            <Shield className="w-4 h-4 text-muted-foreground" />
            <p className="text-xs text-muted-foreground dark:text-muted-foreground">
              Your credentials are encrypted and securely stored
            </p>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
