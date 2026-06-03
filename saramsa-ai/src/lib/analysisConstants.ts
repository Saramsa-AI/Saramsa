/**
 * Single source of truth for the placeholder ID convention used to mark
 * an in-flight analysis in Redux state. Eight call sites used to inline
 * the literal "analyzing_" prefix; if it ever changed by typo or rename,
 * those branches would silently break.
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

/** Status values used on AnalysisHistoryEntry.status — narrowed from `string`. */
export const HISTORY_STATUS = {
  ANALYZING: 'analyzing',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
  FAILED: 'failed',
} as const;
export type HistoryStatus = (typeof HISTORY_STATUS)[keyof typeof HISTORY_STATUS];

/** Status values used on state.analysisStatus — already narrowed in the slice. */
export const TASK_STATUS = {
  IDLE: 'idle',
  PENDING: 'pending',
  PROCESSING: 'processing',
  SUCCESS: 'success',
  FAILURE: 'failure',
} as const;
export type TaskStatus = (typeof TASK_STATUS)[keyof typeof TASK_STATUS];

/** Backend's task-status response — what `/api/insights/task-status/<id>/` returns. */
// FIX 3: Add 'REVOKED' to terminal statuses to handle task revocation edge case
export const BACKEND_TERMINAL_STATUSES = new Set(['SUCCESS', 'PARTIAL', 'FAILURE', 'FAILED', 'CANCELLED', 'REVOKED']);

export function isBackendTerminal(status: string | null | undefined): boolean {
  return typeof status === 'string' && BACKEND_TERMINAL_STATUSES.has(status);
}
