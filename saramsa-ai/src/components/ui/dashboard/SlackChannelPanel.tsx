"use client";

import { useEffect, useState, useMemo } from "react";
import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store/store";
import {
  fetchSlackChannels,
  fetchFeedbackSources,
  createFeedbackSource,
  syncFeedbackSource,
} from "@/store/features/integrations/integrationsSlice";
import {
  fetchAnalysisHistory,
  fetchAnalysisById,
  getLatestAnalysis,
  pollTaskStatus,
  prependToHistory,
  removeFromHistory,
  replaceInHistory,
  setSelectedAnalysisId,
} from "@/store/features/analysis/analysisSlice";
import {
  MessageSquare,
  Search,
  Hash,
  Lock,
  Users,
  RefreshCw,
  Check,
  Loader2,
  Pencil,
  X,
} from "lucide-react";
import { Button } from "../button";
import { Input } from "../input";

interface SlackChannelPanelProps {
  projectId: string;
  slackAccountId: string;
  slackDisplayName: string | null;
}

export function SlackChannelPanel({
  projectId,
  slackAccountId,
  slackDisplayName,
}: SlackChannelPanelProps) {
  const dispatch = useDispatch<AppDispatch>();
  const {
    slackChannels,
    feedbackSources,
    fetchingChannels,
    fetchingSources,
    creatingSource,
    syncingSource,
  } = useSelector((state: RootState) => state.integrations);

  const [selectedChannels, setSelectedChannels] = useState<
    Map<string, string>
  >(new Map());
  const [searchTerm, setSearchTerm] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [syncTriggered, setSyncTriggered] = useState(false);
  const [syncInProgress, setSyncInProgress] = useState(false);
  const [syncStatusText, setSyncStatusText] = useState<string | null>(null);

  // Find existing Slack source for this project
  const existingSource = useMemo(() => {
    return feedbackSources.find(
      (s) =>
        s.provider === "slack" &&
        s.accountId === slackAccountId
    );
  }, [feedbackSources, slackAccountId]);

  const isSyncing = syncInProgress || (existingSource
    ? syncingSource[existingSource.id]
    : false);

  // Fetch channels and sources on mount
  useEffect(() => {
    if (slackAccountId) {
      dispatch(fetchSlackChannels({ accountId: slackAccountId }));
    }
  }, [dispatch, slackAccountId]);

  useEffect(() => {
    if (projectId) {
      dispatch(fetchFeedbackSources({ projectId }));
    }
  }, [dispatch, projectId]);

  // Pre-select channels when editing
  useEffect(() => {
    if (isEditing && existingSource) {
      const map = new Map<string, string>();
      for (const ch of existingSource.config.channels) {
        map.set(ch.id, ch.name);
      }
      setSelectedChannels(map);
    }
  }, [isEditing, existingSource]);

  const filteredChannels = useMemo(() => {
    if (!searchTerm) return slackChannels;
    const term = searchTerm.toLowerCase();
    return slackChannels.filter((ch) => ch.name.toLowerCase().includes(term));
  }, [slackChannels, searchTerm]);

  const toggleChannel = (id: string, name: string) => {
    setSelectedChannels((prev) => {
      const next = new Map(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.set(id, name);
      }
      return next;
    });
  };

  const pollTaskUntilDone = async (taskId: string) => {
    const startedAt = Date.now();
    const timeoutMs = 15 * 60 * 1000;
    const intervalMs = 2000;

    while (Date.now() - startedAt < timeoutMs) {
      const payload: any = await dispatch(pollTaskStatus({ taskId })).unwrap();
      const status = payload?.status;
      if (status === "SUCCESS" || status === "PARTIAL") {
        return { ok: true, result: payload?.result };
      }
      if (status === "FAILURE" || status === "FAILED") {
        return {
          ok: false,
          error: payload?.error || "Slack analysis failed during processing.",
        };
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }

    return { ok: false, error: "Slack analysis timed out." };
  };

  const startSyncAndTrack = async (sourceId: string) => {
    setSyncTriggered(true);
    setSyncInProgress(true);
    setSyncStatusText("Syncing Slack feedback...");
    let tempId: string | null = null;
    try {
      const syncResult = await dispatch(syncFeedbackSource({ sourceId }));
      if (!syncFeedbackSource.fulfilled.match(syncResult)) {
        setSyncStatusText("Failed to start Slack sync.");
        return;
      }

      const taskId = syncResult.payload?.taskId;
      if (!taskId) {
        setSyncStatusText("Sync started, but analysis status is unavailable.");
        return;
      }

      tempId = `analyzing_${Date.now()}`;
      dispatch(
        prependToHistory({
          id: tempId,
          analysis_date: new Date().toISOString(),
          comments_count: 0,
          positive_pct: 0,
          status: "analyzing",
          name: "Slack Sync Run",
        })
      );
      dispatch(setSelectedAnalysisId(tempId));

      setSyncStatusText("Sync complete. Analysis is running...");
      const pollResult = await pollTaskUntilDone(taskId);
      if (!pollResult.ok) {
        if (tempId) dispatch(removeFromHistory(tempId));
        setSyncStatusText(pollResult.error || "Slack analysis failed.");
        return;
      }

      const syncedCount = Number(pollResult?.result?.messages_synced ?? 0);
      if (!syncedCount) {
        if (tempId) dispatch(removeFromHistory(tempId));
        setSyncStatusText("No new messages for analysis.");
      } else {
        setSyncStatusText("Loading new analysis result...");
      }
      const newInsightId = pollResult?.result?.insight_id as string | undefined;
      if (newInsightId) {
        let loaded = false;
        for (let attempt = 0; attempt < 5; attempt += 1) {
          try {
            await dispatch(fetchAnalysisById(newInsightId)).unwrap();
            loaded = true;
            break;
          } catch {
            await new Promise((resolve) => setTimeout(resolve, 1200));
          }
        }
        if (loaded) {
          dispatch(setSelectedAnalysisId(newInsightId));
          if (tempId) {
            dispatch(
              replaceInHistory({
                oldId: tempId,
                entry: {
                  id: newInsightId,
                  analysis_date: new Date().toISOString(),
                  comments_count: syncedCount,
                  positive_pct: 0,
                  status: "completed",
                  name: "Slack Sync Run",
                },
              })
            );
          }
          setSyncStatusText("Analysis complete.");
        }
      } else if (tempId) {
        dispatch(removeFromHistory(tempId));
      }
      dispatch(fetchFeedbackSources({ projectId }));
      dispatch(getLatestAnalysis(projectId));
      dispatch(fetchAnalysisHistory(projectId));
      if (typeof window !== "undefined") {
        const target = document.getElementById("analysis-results-section");
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }
    } catch (error: any) {
      setSyncStatusText(error?.message || "Slack sync/analysis failed.");
    } finally {
      setSyncInProgress(false);
    }
  };

  const handleSaveAndSync = async () => {
    const channels = Array.from(selectedChannels.entries()).map(
      ([id, name]) => ({ id, name })
    );
    const result = await dispatch(
      createFeedbackSource({
        projectId,
        accountId: slackAccountId,
        channels,
      })
    );
    if (createFeedbackSource.fulfilled.match(result)) {
      const newSource = result.payload;
      await startSyncAndTrack(newSource.id);
      setIsEditing(false);
      setSelectedChannels(new Map());
    }
  };

  const handleSyncNow = async () => {
    if (existingSource) {
      await startSyncAndTrack(existingSource.id);
    }
  };

  const isLoading = fetchingChannels || fetchingSources;

  // Loading state
  if (isLoading) {
    return (
      <section id="slack-channel-panel" className="space-y-3 py-2">
        <div className="border rounded-2xl px-4 sm:px-5 py-3 sm:py-3.5 bg-background/40 border-border/60">
          <div className="flex items-center gap-4 animate-pulse">
            <div className="w-9 h-9 rounded-lg bg-muted" />
            <div className="flex-1 space-y-2">
              <div className="h-4 bg-muted rounded w-1/3" />
              <div className="h-3 bg-muted rounded w-1/2" />
            </div>
          </div>
        </div>
      </section>
    );
  }

  // Source exists — show status
  if (existingSource && !isEditing) {
    const lastSynced = existingSource.config.last_synced_at;
    const channels = existingSource.config.channels || [];

    return (
      <section id="slack-channel-panel" className="space-y-3 py-2">
        <div className="border rounded-2xl px-4 sm:px-5 py-3 sm:py-3.5 bg-background/40 border-border/60">
          <div className="flex items-start gap-4">
            <div className="w-9 h-9 rounded-lg border border-border/60 bg-secondary/50 flex items-center justify-center shrink-0">
              <MessageSquare className="w-4 h-4 text-saramsa-brand" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">
                Slack Feedback Channels
                {slackDisplayName && (
                  <span className="text-muted-foreground font-normal">
                    {" "}
                    &middot; {slackDisplayName}
                  </span>
                )}
              </p>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {channels.map((ch) => (
                  <span
                    key={ch.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-saramsa-brand/10 text-saramsa-brand border border-saramsa-brand/20"
                  >
                    <Hash className="w-3 h-3" />
                    {ch.name}
                  </span>
                ))}
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                {syncTriggered && syncStatusText
                  ? syncStatusText
                  : lastSynced
                  ? `Last synced: ${new Date(lastSynced).toLocaleString()}`
                  : "Not yet synced"}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="outline"
                size="sm"
                className="border-border/60 hover:bg-accent/60"
                onClick={() => setIsEditing(true)}
                title="Edit channels"
              >
                <Pencil className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="saramsa"
                size="sm"
                onClick={handleSyncNow}
                disabled={isSyncing}
              >
                {isSyncing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                Sync and Analyse
              </Button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  // Channel picker mode (no source yet, or editing)
  return (
    <section id="slack-channel-panel" className="space-y-3 py-2">
      <div className="border rounded-2xl px-4 sm:px-5 py-3 sm:py-3.5 bg-background/40 border-border/60 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg border border-border/60 bg-secondary/50 flex items-center justify-center shrink-0">
              <MessageSquare className="w-4 h-4 text-saramsa-brand" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">
                {isEditing ? "Edit Slack Channels" : "Choose Slack Channels"}
              </p>
              <p className="text-xs text-muted-foreground">
                Select channels to sync feedback and analyse
              </p>
            </div>
          </div>
          {isEditing && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => {
                setIsEditing(false);
                setSelectedChannels(new Map());
              }}
            >
              <X className="w-4 h-4 text-muted-foreground" />
            </Button>
          )}
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search channels..."
            className="w-full pl-9 pr-3 py-2 text-sm border border-border/60 rounded-xl bg-background/80 text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {/* Channel list */}
        <div className="max-h-48 overflow-y-auto border border-border/60 rounded-xl">
          {filteredChannels.length === 0 ? (
            <div className="text-center py-6">
              <p className="text-sm text-muted-foreground">
                {searchTerm ? "No channels found" : "No channels available"}
              </p>
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {filteredChannels.map((ch) => {
                const isSelected = selectedChannels.has(ch.id);
                return (
                  <button
                    key={ch.id}
                    type="button"
                    onClick={() => toggleChannel(ch.id, ch.name)}
                    className={`w-full flex items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-accent/60 ${
                      isSelected ? "bg-saramsa-brand/5" : ""
                    }`}
                  >
                    <div
                      className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                        isSelected
                          ? "bg-saramsa-brand border-saramsa-brand"
                          : "border-border/80"
                      }`}
                    >
                      {isSelected && (
                        <Check className="w-3 h-3 text-white" />
                      )}
                    </div>
                    {ch.is_private ? (
                      <Lock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    ) : (
                      <Hash className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    )}
                    <span className="text-sm text-foreground flex-1 truncate">
                      {ch.name}
                    </span>
                    <span className="text-xs text-muted-foreground flex items-center gap-1 shrink-0">
                      <Users className="w-3 h-3" />
                      {ch.num_members}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Selected count + save button */}
        <div className="flex items-center justify-between pt-1">
          <p className="text-xs text-muted-foreground">
            {selectedChannels.size} channel
            {selectedChannels.size !== 1 ? "s" : ""} selected
          </p>
          <Button
            variant="saramsa"
            size="sm"
            onClick={handleSaveAndSync}
            disabled={selectedChannels.size === 0 || creatingSource || syncInProgress}
          >
            {creatingSource || syncInProgress ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Save, Sync and Analyse
          </Button>
        </div>
      </div>
    </section>
  );
}
