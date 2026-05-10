"use client";

import { useEffect, useState } from "react";
import { Bot, Loader2, RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiRequest } from "@/lib/apiRequest";

type PromptOverride = {
  id: string;
  prompt_type: string;
  scope: string;
  organizationId?: string | null;
  content: string;
};

type OrganizationOption = {
  id: string;
  name: string;
};

type PromptSettingsPayload = {
  available_prompt_types: string[];
  organizations: OrganizationOption[];
  platform_prompts: Record<string, PromptOverride>;
  organization_prompts: Record<string, PromptOverride>;
  selected_organization_id?: string | null;
  default_prompts?: Record<string, string>;
  default_prompts_errors?: Record<string, string>;
};

export function PromptSettingsPage() {
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<PromptSettingsPayload | null>(null);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string>("");
  const [platformDrafts, setPlatformDrafts] = useState<Record<string, string>>({});
  const [organizationDrafts, setOrganizationDrafts] = useState<Record<string, string>>({});

  const loadData = async (organizationId?: string) => {
    try {
      setLoading(true);
      setError(null);
      const query = organizationId ? `?organization_id=${encodeURIComponent(organizationId)}` : "";
      const res = await apiRequest("get", `/auth/admin/prompts/${query}`, undefined, true);
      const payload: PromptSettingsPayload = res.data?.data;
      setData(payload);
      setSelectedOrganizationId(payload.selected_organization_id || organizationId || "");
      const defaults = payload.default_prompts || {};
      setPlatformDrafts(
        Object.fromEntries(
          payload.available_prompt_types.map((type) => [
            type,
            payload.platform_prompts?.[type]?.content || defaults[type] || "",
          ]),
        ),
      );
      setOrganizationDrafts(
        Object.fromEntries(
          payload.available_prompt_types.map((type) => [
            type,
            payload.organization_prompts?.[type]?.content
              || payload.platform_prompts?.[type]?.content
              || defaults[type]
              || "",
          ]),
        ),
      );
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to load prompt settings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const savePrompt = async (scope: "platform" | "organization", promptType: string) => {
    const key = `${scope}:${promptType}`;
    const content = scope === "platform" ? platformDrafts[promptType] : organizationDrafts[promptType];

    try {
      setSavingKey(key);
      setError(null);
      await apiRequest(
        "post",
        "/auth/admin/prompts/",
        {
          scope,
          prompt_type: promptType,
          content,
          organization_id: scope === "organization" ? selectedOrganizationId : undefined,
        },
        true,
      );
      await loadData(scope === "organization" ? selectedOrganizationId : selectedOrganizationId || undefined);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to save prompt.");
    } finally {
      setSavingKey(null);
    }
  };

  const resetPrompt = async (scope: "platform" | "organization", promptType: string) => {
    const key = `reset:${scope}:${promptType}`;
    try {
      setSavingKey(key);
      setError(null);
      const suffix =
        `?scope=${encodeURIComponent(scope)}` +
        `&prompt_type=${encodeURIComponent(promptType)}` +
        (scope === "organization" ? `&organization_id=${encodeURIComponent(selectedOrganizationId)}` : "");
      await apiRequest("delete", `/auth/admin/prompts/${suffix}`, undefined, true);
      await loadData(scope === "organization" ? selectedOrganizationId : selectedOrganizationId || undefined);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to reset prompt.");
    } finally {
      setSavingKey(null);
    }
  };

  const renderPromptEditor = (scope: "platform" | "organization", promptType: string) => {
    const drafts = scope === "platform" ? platformDrafts : organizationDrafts;
    const setDrafts = scope === "platform" ? setPlatformDrafts : setOrganizationDrafts;
    const value = drafts[promptType] || "";
    const saveKey = `${scope}:${promptType}`;
    const resetKey = `reset:${scope}:${promptType}`;
    const hasOverride = scope === "platform"
      ? !!data?.platform_prompts?.[promptType]
      : !!data?.organization_prompts?.[promptType];

    return (
      <section key={`${scope}-${promptType}`} className="rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-4">
          <div className="flex items-center gap-2 min-w-0">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{promptType}</p>
              <p className="text-xs text-muted-foreground capitalize">{scope} scope</p>
            </div>
            <span className={`ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${hasOverride ? "bg-saramsa-brand/15 text-saramsa-brand" : "bg-muted text-muted-foreground"}`}>
              {hasOverride ? "override active" : "using default"}
            </span>
          </div>
          <div className="flex gap-2 flex-shrink-0">
            <Button
              variant="outline"
              size="sm"
              className="h-9 px-3"
              disabled={savingKey !== null || (scope === "organization" && !selectedOrganizationId)}
              onClick={() => resetPrompt(scope, promptType)}
              title="Reset to default"
            >
              {savingKey === resetKey ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            </Button>
            <Button
              variant="saramsa"
              size="sm"
              className="h-9 px-3 flex items-center gap-2"
              disabled={savingKey !== null || (scope === "organization" && !selectedOrganizationId)}
              onClick={() => savePrompt(scope, promptType)}
            >
              {savingKey === saveKey ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          </div>
        </div>
        <div className="px-6 py-5">
          {data?.default_prompts_errors?.[promptType] && (
            <p className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              System default unavailable — saving an empty override could disable this prompt. ({data.default_prompts_errors[promptType]})
            </p>
          )}
          <textarea
            value={value}
            onChange={(e) => setDrafts((current) => ({ ...current, [promptType]: e.target.value }))}
            rows={10}
            className="w-full rounded-md border border-border bg-background px-3 py-3 text-sm text-foreground font-mono outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
            placeholder={`Override ${promptType} prompt for ${scope} scope`}
          />
        </div>
      </section>
    );
  };

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-10 flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <div className="space-y-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Prompts</h2>
          <p className="text-sm text-muted-foreground mt-2">Manage platform-wide and tenant-specific AI prompt overrides.</p>
        </div>
        <div className="inline-flex items-center gap-2 rounded-md border border-border bg-secondary/60 px-2.5 py-1.5 text-xs font-medium text-foreground">
          <Bot className="h-3.5 w-3.5 text-muted-foreground" />
          <span>Superadmin only</span>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <section className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-6 py-4">
          <h3 className="text-sm font-medium text-foreground">Tenant target</h3>
          <p className="text-xs text-muted-foreground mt-1">Choose a workspace to load its tenant-specific overrides.</p>
        </div>
        <div className="px-6 py-5">
          <select
            value={selectedOrganizationId}
            onChange={(e) => {
              const value = e.target.value;
              setSelectedOrganizationId(value);
              loadData(value || undefined);
            }}
            className="h-10 min-w-[280px] rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
          >
            <option value="">Select tenant workspace</option>
            {data.organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </div>
      </section>

      <div className="space-y-4">
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground px-1">Platform-wide prompts</h3>
        <div className="space-y-4">
          {data.available_prompt_types.map((promptType) => renderPromptEditor("platform", promptType))}
        </div>
      </div>

      <div className="space-y-4">
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground px-1">Tenant-specific prompts</h3>
        {selectedOrganizationId ? (
          <div className="space-y-4">
            {data.available_prompt_types.map((promptType) => renderPromptEditor("organization", promptType))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border bg-card px-6 py-10 text-sm text-muted-foreground">
            Select a workspace above to manage tenant-specific prompt overrides.
          </div>
        )}
      </div>
    </div>
  );
}
