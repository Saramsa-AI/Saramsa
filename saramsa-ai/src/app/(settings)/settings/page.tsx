"use client";

import { useEffect, useMemo, useState } from "react";
import { Bot, Building2, CreditCard, PlugZap, UserRound } from "lucide-react";
import { IntegrationsPage } from "@/components/ui/settings/IntegrationsPage";
import { WorkspacePage } from "@/components/ui/settings/WorkspacePage";
import { PromptSettingsPage } from "@/components/ui/settings/PromptSettingsPage";
import { GeneralPage } from "@/components/ui/settings/GeneralPage";
import { BillingPage } from "@/components/ui/settings/BillingPage";
import { useAuth } from "@/lib/useAuth";

type TabKey = "general" | "workspace" | "billing" | "integrations" | "prompts";

type TabDef = {
  key: TabKey;
  label: string;
  Icon: typeof UserRound;
  description: string;
};

export default function SettingsPage() {
  const { user } = useAuth();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  // Compute superadmin only after client mount so the Prompts tab can't
  // appear on the SSR pass and disappear on the client — that swap was
  // triggering a hydration mismatch.
  const isSuperadmin = mounted && !!user?.is_staff;
  const [activeTab, setActiveTab] = useState<TabKey>("general");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const tab = new URLSearchParams(window.location.search).get("tab");
    if (tab === "workspace" || tab === "integrations" || tab === "billing" || tab === "general") {
      setActiveTab(tab);
    } else if (tab === "prompts" && isSuperadmin) {
      setActiveTab("prompts");
    } else if (tab === "profile") {
      setActiveTab("general");
    }
  }, [isSuperadmin]);

  const tabs = useMemo<TabDef[]>(
    () => {
      const list: TabDef[] = [
        { key: "general", label: "General", Icon: UserRound, description: "Account" },
        { key: "workspace", label: "Workspace", Icon: Building2, description: "Members & invites" },
        { key: "billing", label: "Billing", Icon: CreditCard, description: "Subscription" },
        { key: "integrations", label: "Integrations", Icon: PlugZap, description: "Connected apps" },
      ];
      if (isSuperadmin) {
        list.push({ key: "prompts", label: "Prompts", Icon: Bot, description: "AI overrides" });
      }
      return list;
    },
    [isSuperadmin],
  );

  const handleSelect = (key: TabKey) => {
    setActiveTab(key);
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      params.set("tab", key);
      const newUrl = `${window.location.pathname}?${params.toString()}`;
      window.history.replaceState({}, "", newUrl);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-background text-foreground">
      <div className="max-w-screen-2xl mx-auto px-4 py-8 sm:px-6 lg:px-6 lg:py-14">
        <header className="mb-8 lg:mb-12">
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-foreground">Settings</h1>
          <p className="text-sm text-muted-foreground mt-2">
            Manage your account, workspace, and connected integrations.
          </p>
        </header>

        <div className="flex flex-col lg:flex-row gap-8 lg:gap-16">
          <aside className="lg:w-60 lg:flex-shrink-0">
            {/* Mobile: horizontal scroll. Desktop: vertical sticky sidebar. */}
            <nav
              aria-label="Settings sections"
              className="lg:sticky lg:top-8 -mx-4 px-4 sm:mx-0 sm:px-0 overflow-x-auto lg:overflow-visible"
            >
              <ul className="flex lg:flex-col gap-1 lg:gap-1 min-w-max lg:min-w-0">
                {tabs.map(({ key, label, Icon }) => {
                  const selected = activeTab === key;
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        onClick={() => handleSelect(key)}
                        aria-current={selected ? "page" : undefined}
                        className={`group w-full flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors whitespace-nowrap lg:whitespace-normal ${
                          selected
                            ? "bg-secondary text-foreground"
                            : "text-muted-foreground hover:text-foreground hover:bg-secondary/60"
                        }`}
                      >
                        <Icon
                          className={`h-4 w-4 flex-shrink-0 transition-colors ${
                            selected ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                          }`}
                          aria-hidden="true"
                        />
                        <span>{label}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </nav>
          </aside>

          <main className="flex-1 min-w-0">
            {activeTab === "general" && <GeneralPage />}
            {activeTab === "workspace" && <WorkspacePage />}
            {activeTab === "billing" && <BillingPage />}
            {activeTab === "integrations" && <IntegrationsPage />}
            {activeTab === "prompts" && isSuperadmin && <PromptSettingsPage />}
          </main>
        </div>
      </div>
    </div>
  );
}
