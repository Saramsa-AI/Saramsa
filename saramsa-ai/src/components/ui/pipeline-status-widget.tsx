"use client";

import { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import type { RootState } from "@/store/rootReducer";
import { apiRequest } from "@/lib/apiRequest";
import { getValidAccessToken } from "@/lib/auth";
import { usePathname, useRouter } from "next/navigation";
import { AlertCircle, CheckCircle2, ChevronDown, GitBranch, Loader2 } from 'lucide-react';
import { cn } from "./utils";

type StageStatus = "idle" | "pending" | "running" | "success" | "error" | "partial" | "cancelled";

type TaskItem = {
  id: string;
  // The real, fetchable analysis id (insight_/analysis_ uuid). Null while the run
  // has only a Celery task_id and no result yet — clicking such a tile must NOT
  // navigate, since `/feedback/analysis/<celery_task_id>/` 404s.
  analysisId?: string | null;
  label: string;
  detail: string;
  status: StageStatus;
  statusLabel?: string;
  statusTone?: string;
  project_id?: string | null;
  pipelineHealth?: {
    status?: string;
    errors?: Record<string, string>;
  };
  commentCount?: number | null;
  durationSeconds?: number | null;
  updatedAt: number;
};

const statusCopy: Record<StageStatus, { label: string; tone: string }> = {
  idle: { label: "Idle", tone: "text-muted-foreground" },
  pending: { label: "Queued", tone: "text-amber-600" },
  running: { label: "Processing", tone: "text-sky-600" },
  success: { label: "Done", tone: "text-emerald-600" },
  error: { label: "Failed", tone: "text-rose-600" },
  partial: { label: "Partial", tone: "text-amber-600" },
  cancelled: { label: "Cancelled", tone: "text-muted-foreground" },
};

const activeRank: Record<StageStatus, number> = {
  running: 4,
  pending: 3,
  error: 2,
  partial: 1,
  success: 1,
  cancelled: 1,
  idle: 0,
};

const dedupeTasks = (items: TaskItem[]) => {
  const byId = new Map<string, TaskItem>();
  for (const item of items) {
    const existing = byId.get(item.id);
    if (
      !existing ||
      activeRank[item.status] > activeRank[existing.status] ||
      (activeRank[item.status] === activeRank[existing.status] && item.updatedAt >= existing.updatedAt)
    ) {
      byId.set(item.id, item);
    }
  }
  return Array.from(byId.values());
};

export function PipelineStatusWidget() {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const projectId = useSelector(
    (state: RootState) => state.analysis.projectId
  );
  const currentProject = useSelector(
    (state: RootState) => state.projects.currentProject
  );
  const [apiTasks, setApiTasks] = useState<TaskItem[]>([]);

  useEffect(() => {
    let isMounted = true;
    const mapApiTask = (task: any): TaskItem => {
      // Real, fetchable id only — never the Celery task_id (a click on that 404s).
      const analysisId = task.insight_id || task.analysis_id || null;
      const routedId = analysisId || task.task_id;
      const raw = String(task?.status || "").toUpperCase();
      if (raw === "PARTIAL") {
        return {
          id: routedId,
          analysisId,
          label: toLabel(task.project_id, task.file_name),
          detail: "Sentiment + synthesis pipeline",
          status: "partial",
          project_id: task.project_id ?? null,
          pipelineHealth: task.pipeline_health,
          commentCount: task.comment_count ?? null,
          durationSeconds: task.duration_seconds ?? null,
          updatedAt: Date.now(),
        };
      }
      const status: StageStatus =
        raw === "RUNNING"
          ? "running"
          : raw === "SUCCESS"
          ? "success"
          : raw === "FAILED" || raw === "FAILURE"
          ? "error"
          : raw === "CANCELLED" || raw === "REVOKED"
          ? "cancelled"
          : "pending";
      return {
        id: routedId,
        analysisId,
        label: toLabel(task.project_id, task.file_name),
        detail: "Sentiment + synthesis pipeline",
        status,
        project_id: task.project_id ?? null,
        pipelineHealth: task.pipeline_health,
        commentCount: task.comment_count ?? null,
        durationSeconds: task.duration_seconds ?? null,
        updatedAt: Date.now(),
      };
    };

    const toLabel = (projectId?: string, fileName?: string) => {
      if (fileName) return fileName;
      if (projectId) return `Analysis - ${projectId.slice(0, 8)}`;
      return "Feedback analysis";
    };

    const fetchTasks = async () => {
      try {
        const token = getValidAccessToken();
        if (!token) {
          return;
        }
        const response = await apiRequest("get", "/insights/tasks/", undefined, true);
        const list = response.data?.data?.tasks ?? [];
        if (!isMounted || !Array.isArray(list)) {
          return;
        }
        const mapped = list.slice(0, 15).map((task: any) => mapApiTask(task));
        setApiTasks(dedupeTasks(mapped));
      } catch {
        // Keep local fallback
      }
    };

    const token = getValidAccessToken();
    if (token) {
      fetchTasks();
    }
    const interval = setInterval(fetchTasks, 30000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;

    const syncDrawerState = () => {
      setIsDrawerOpen(document.body.getAttribute("data-edit-drawer-open") === "true");
    };

    syncDrawerState();
    const observer = new MutationObserver(syncDrawerState);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["data-edit-drawer-open"],
    });

    return () => observer.disconnect();
  }, []);
  
  const activeProjectId = currentProject?.id ?? projectId ?? null;
  const filteredApiTasks = activeProjectId
    ? apiTasks.filter((t: TaskItem) => t.project_id === activeProjectId)
    : apiTasks;
  const taskSource = filteredApiTasks;
  const dedupedTaskSource = dedupeTasks(taskSource);
  const activeTasks = dedupedTaskSource.filter(
    (task) => task.status === "pending" || task.status === "running"
  );
  const historyTasks = dedupedTaskSource.filter(
    (task) => task.status !== "pending" && task.status !== "running"
  );
  const visibleHistoryTasks = historyTasks.slice(0, 3);
  const visibleTasks = [...activeTasks, ...visibleHistoryTasks];

  const buildHealthTooltip = (task: TaskItem) => {
    const errors = task.pipelineHealth?.errors;
    if (!errors) return undefined;
    const entries = Object.entries(errors);
    if (!entries.length) return undefined;
    return entries.map(([key, value]) => `${key}: ${value}`).join(" | ");
  };

  const statusLabelForTask = (task: TaskItem) =>
    task.statusLabel ?? statusCopy[task.status].label;
  const statusToneForTask = (task: TaskItem) =>
    task.statusTone ?? statusCopy[task.status].tone;
  const statusIconForTask = (task: TaskItem) => {
    if (task.status === "running") return <Loader2 className="h-4 w-4 animate-spin text-sky-600" />;
    if (task.status === "pending") return <Loader2 className="h-4 w-4 text-amber-600" />;
    if (task.status === "success") return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
    if (task.status === "partial") return <AlertCircle className="h-4 w-4 text-amber-600" />;
    if (task.status === "error") return <AlertCircle className="h-4 w-4 text-rose-600" />;
    return <div className="h-2.5 w-2.5 rounded-full bg-muted-foreground/50" />;
  };
  const handleTaskClick = (task: TaskItem) => {
    // No result to open yet (still running / only a Celery task_id). Routing to a
    // non-fetchable id would 404, so just leave the tile non-navigable.
    if (!pathname || !task.analysisId) return;
    const segments = pathname.split("/").filter(Boolean);
    if (segments[0] !== "projects" || !segments[1]) return;
    const projectIdSegment = segments[1];
    setOpen(false);
    router.push(`/projects/${projectIdSegment}/dashboard/?analysisId=${encodeURIComponent(task.analysisId)}`);
  };
  const formatTaskMeta = (task: TaskItem) => {
    const parts: string[] = [];
    if (typeof task.commentCount === "number") {
      parts.push(`${task.commentCount} comments`);
    }
    if (typeof task.durationSeconds === "number") {
      const minutes = Math.max(1, Math.round(task.durationSeconds / 60));
      parts.push(`${minutes} min`);
    }
    return parts.join(" · ");
  };

  if (isDrawerOpen) {
    return null;
  }

  return (
    <div className="pipeline-status-widget-fab fixed bottom-6 right-6 z-[100]">
      {open && (
        <div className="absolute bottom-20 right-0 w-[380px] rounded-2xl border border-border/70 bg-background/95 p-4 shadow-[0_24px_60px_-30px_rgba(15,23,42,0.6)] backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.3em] text-muted-foreground">
                Pipeline Status
              </p>
            </div>
            <button
              className="rounded-full border border-border/60 p-1 text-muted-foreground transition hover:text-foreground"
              onClick={() => setOpen(false)}
              aria-label="Collapse pipeline status"
            >
              <ChevronDown className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-2">
            <div className="mt-2 flex flex-wrap items-center gap-3 text-muted-foreground">
            </div>
          </div>

          <div className="mt-3">
            <div className="flex items-center justify-between text-xs uppercase tracking-[0.3em] text-muted-foreground">
              <span>Tasks</span>
              <span>{visibleTasks.length}</span>
            </div>

            <div className="mt-3 space-y-2">
              {visibleTasks.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border/60 px-3 py-3 text-xs text-muted-foreground">
                  No tasks yet.
                </div>
              ) : (
                visibleTasks.map((task) => (
                  <div
                    key={task.id}
                    className={cn(
                      "flex items-center gap-3 rounded-xl border border-border/50 bg-muted/30 px-3 py-2 transition",
                      task.analysisId ? "cursor-pointer hover:bg-muted/50" : "cursor-default"
                    )}
                    onClick={() => handleTaskClick(task)}
                  >
                    <div className="flex-shrink-0">
                      {statusIconForTask(task)}
                    </div>
                    <div className="flex-1">
                      <p className="text-sm font-semibold text-foreground">
                        {task.label}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className={cn("text-[11px] font-medium", statusToneForTask(task))}>
                        {statusLabelForTask(task)}
                      </p>
                      {formatTaskMeta(task) && (
                        <p className="text-[11px] text-muted-foreground" title={buildHealthTooltip(task)}>
                          {formatTaskMeta(task)}
                        </p>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col items-end">
        <button
          onClick={() => setOpen((value) => !value)}
          className={cn(
            "group relative flex h-14 w-14 cursor-pointer items-center justify-center rounded-full",
            "bg-saramsa-brand text-white hover:bg-saramsa-brand-hover",
            "shadow-[0_18px_40px_-20px_rgba(255,137,33,0.55)]",
            "transition hover:-translate-y-0.5 hover:shadow-[0_24px_55px_-24px_rgba(15,23,42,0.8)]"
          )}
          aria-label="Toggle pipeline status"
        >
          <GitBranch className="h-5 w-5" />
        </button>
      </div>
    </div>
  );
}
