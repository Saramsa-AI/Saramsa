/**
 * Single source of truth for the placeholder ID convention used to mark
 * an in-flight analysis in Redux state.
 *
 * Convention: when an upload is dispatched, frontend creates an
 * AnalysisHistoryEntry with id = `analyzing_<task_id>` and uses that
 * same string as the placeholder for state.selectedAnalysisId. When the
 * task reaches a terminal state, both are swapped to the real
 * insight_<analysis_id> via the resolveAnalyzingTask reducer.
 */
export const ANALYZING_PREFIX = 'analyzing_';

export function makeAnalyzingId(taskId: string): string {
  return `${ANALYZING_PREFIX}${taskId}`;
}

export function isAnalyzingPlaceholder(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(ANALYZING_PREFIX);
}

export function extractTaskIdFromPlaceholder(id: string | null | undefined): string | null {
  if (!isAnalyzingPlaceholder(id)) return null;
  return (id as string).slice(ANALYZING_PREFIX.length);
}

/** Status values used on AnalysisHistoryEntry.status. */
export const HISTORY_STATUS = {
  ANALYZING: 'analyzing',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
  FAILED: 'failed',
} as const;
export type HistoryStatus = (typeof HISTORY_STATUS)[keyof typeof HISTORY_STATUS];

/** Status values used on state.analysisStatus. */
export const TASK_STATUS = {
  IDLE: 'idle',
  PENDING: 'pending',
  PROCESSING: 'processing',
  SUCCESS: 'success',
  FAILURE: 'failure',
} as const;
export type TaskStatus = (typeof TASK_STATUS)[keyof typeof TASK_STATUS];

/** Backend's task-status response — what `/api/insights/task-status/<id>/` returns. */
export const BACKEND_TERMINAL_STATUSES = new Set(['SUCCESS', 'PARTIAL', 'FAILURE', 'FAILED', 'CANCELLED', 'REVOKED']);

export function isBackendTerminal(status: string | null | undefined): boolean {
  return typeof status === 'string' && BACKEND_TERMINAL_STATUSES.has(status);
}

// ============================================================================
// STATE MACHINE TYPE DEFINITION
// ============================================================================

/**
 * Analysis Lifecycle State Machine
 *
 * Single source of truth for all analysis lifecycle states.
 */

export enum AnalysisLifecycleState {
  /** No analysis running, viewing historical results */
  IDLE = 'idle',

  /** Task submitted to backend, waiting for processing to start */
  QUEUED = 'queued',

  /** Backend is reading/parsing uploaded file */
  INGESTING = 'ingesting',

  /** Running sentiment analysis & feature extraction on feedback */
  ANALYZING = 'analyzing',

  /** GPT generating narrative insights from analysis results */
  SYNTHESIZING = 'synthesizing',

  /** Creating actionable work items from insights */
  GENERATING_WORKITEMS = 'generating_workitems',

  /** Analysis pipeline complete, showing final results */
  COMPLETED = 'completed',

  /** Task was cancelled by user or system */
  CANCELLED = 'cancelled',

  /** Error occurred during any pipeline stage */
  FAILED = 'failed',
}

/**
 * Per-task tracking structure. Keyed by analysis ID (either placeholder
 * `analyzing_<task_id>` for in-flight or `insight_<id>` for completed).
 */
export interface AnalysisTaskState {
  /** Current lifecycle state */
  state: AnalysisLifecycleState;

  /** Project this analysis belongs to */
  projectId: string;

  /** Backend task ID for polling */
  taskId: string | null;

  /** Real insight ID once backend completes (null while in-flight) */
  insightId: string | null;

  /** Timestamp when this task entered current state */
  stateEnteredAt: number;

  /** Error message if state is FAILED */
  error: string | null;

  /** AbortController for work items generation (if GENERATING_WORKITEMS) */
  workItemsAbortController: AbortController | null;

  /** Metadata for history sidebar (updated as task progresses) */
  metadata: {
    fileName?: string;
    commentsCount: number;
    positivePct: number;
    analysisDate: string;
    name?: string | null;
  };
}
