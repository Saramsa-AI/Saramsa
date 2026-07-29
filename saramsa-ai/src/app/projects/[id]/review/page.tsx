'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { useAppDispatch, useAppSelector } from '@/store/hooks';
import {
  fetchCandidates,
  fetchReviewStats,
  approveCandidate,
  dismissCandidate,
  snoozeCandidate,
  batchApprove,
  mergeCandidates,
  toggleSelected,
  clearSelected,
  selectAll,
  setFilters,
  type ReviewCandidate,
} from '@/store/features/review/reviewSlice';
import { decryptProjectId, isValidEncryptedId } from '@/lib/encryption';
import { ReviewQueueStats } from '@/components/ui/review/ReviewQueueStats';
import { ReviewQueueItem } from '@/components/ui/review/ReviewQueueItem';
import { EditCandidateDrawer } from '@/components/ui/review/EditCandidateDrawer';
import { Button } from '@/components/ui/button';
import { Check, Inbox } from 'lucide-react';
import { AnimatePresence } from 'framer-motion';
import { apiRequest } from '@/lib/apiRequest';

export default function ReviewQueuePage() {
  const params = useParams();
  const dispatch = useAppDispatch();
  const { candidates, filters, selectedIds, loading } = useAppSelector((s) => s.review);

  const [projectId, setProjectId] = useState<string | null>(null);
  const [editCandidate, setEditCandidate] = useState<ReviewCandidate | null>(null);
  // Render the list in pages. Every row is a framer-motion `layout` component,
  // and this queue routinely holds 150+ candidates — mounting them all at once
  // is a real cost, so grow the window on demand instead.
  const PAGE_SIZE = 25;
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  // Decrypt project ID
  useEffect(() => {
    const encId = params.id as string;
    if (!encId) return;
    try {
      const id = isValidEncryptedId(encId) ? decryptProjectId(encId) : encId;
      setProjectId(id);
    } catch {
      setProjectId(encId);
    }
  }, [params.id]);

  // Fetch data when project ID is ready
  useEffect(() => {
    if (!projectId) return;
    dispatch(fetchCandidates({ projectId, filters }));
    dispatch(fetchReviewStats(projectId));
  }, [dispatch, projectId, filters]);

  const refresh = useCallback(() => {
    if (!projectId) return;
    dispatch(fetchCandidates({ projectId, filters }));
    dispatch(fetchReviewStats(projectId));
  }, [dispatch, projectId, filters]);

  const handleApprove = useCallback((id: string) => {
    if (!projectId) return;
    dispatch(approveCandidate({ candidateId: id, projectId })).then(refresh);
  }, [dispatch, projectId, refresh]);

  const handleDismiss = useCallback((id: string, reason: string) => {
    if (!projectId) return;
    dispatch(dismissCandidate({ candidateId: id, projectId, reason })).then(refresh);
  }, [dispatch, projectId, refresh]);

  const handleSnooze = useCallback((id: string, days: number) => {
    if (!projectId) return;
    dispatch(snoozeCandidate({ candidateId: id, projectId, snoozeDays: days })).then(refresh);
  }, [dispatch, projectId, refresh]);

  const handleBatchApprove = useCallback(() => {
    if (!projectId || selectedIds.length === 0) return;
    dispatch(batchApprove({ candidateIds: selectedIds, projectId })).then(refresh);
  }, [dispatch, projectId, selectedIds, refresh]);

  // Merge flow: select exactly ONE item (the keeper/target), then hit "Merge"
  // on any other row to fold it in. The source becomes status 'merged' and its
  // evidence is unioned into the target.
  const mergeTargetId = selectedIds.length === 1 ? selectedIds[0] : null;
  const handleMerge = useCallback((sourceId: string) => {
    if (!projectId || !mergeTargetId || mergeTargetId === sourceId) return;
    dispatch(mergeCandidates({ sourceId, targetId: mergeTargetId, projectId })).then(refresh);
  }, [dispatch, projectId, mergeTargetId, refresh]);

  // Collapse back to the first page whenever the result set changes, so a
  // filter switch doesn't leave a stale "showing 100 of 12" window.
  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [filters, projectId]);

  const handleSaveApprove = useCallback((candidate: ReviewCandidate, edits: Record<string, any>) => {
    if (!projectId) return;
    dispatch(approveCandidate({ candidateId: candidate.id, projectId, edits })).then(refresh);
  }, [dispatch, projectId, refresh]);

  const handleSaveDraft = useCallback(async (candidateId: string, updates: Record<string, any>) => {
    if (!projectId) return;
    await apiRequest('put', '/work-items/review/update/', { candidate_id: candidateId, project_id: projectId, updates }, true);
    refresh();
  }, [projectId, refresh]);

  if (!projectId) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-shrink-0 px-6 py-6 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">User Stories</h1>
            <p className="text-sm text-muted-foreground mt-1">Review and approve AI-generated user stories</p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={filters.status}
              onChange={(e) => dispatch(setFilters({ status: e.target.value }))}
              className="rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saramsa-brand/20"
            >
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="dismissed">Dismissed</option>
              <option value="snoozed">Snoozed</option>
            </select>
            <select
              value={filters.priority}
              onChange={(e) => dispatch(setFilters({ priority: e.target.value }))}
              className="rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saramsa-brand/20"
            >
              <option value="">All priorities</option>
              <option value="critical">P0 - Critical</option>
              <option value="high">P1 - High</option>
              <option value="medium">P2 - Medium</option>
              <option value="low">P3 - Low</option>
            </select>
          </div>
        </div>

        {/* Stats */}
        <ReviewQueueStats projectId={projectId} />

        {/* Batch actions. "Select All" lives OUTSIDE the selection check —
            previously the whole bar (Select All included) only rendered once
            something was already selected, so Select All could never be the
            first action you took. */}
        {candidates.length > 0 && (
          <div className="flex items-center gap-3 p-3 rounded-xl border border-saramsa-brand/20 bg-saramsa-brand/5">
            <span className="text-sm font-medium text-foreground">
              {selectedIds.length > 0
                ? `${selectedIds.length} selected`
                /* Label must follow the active status filter — hardcoding
                   "pending" showed e.g. "6 pending" while viewing Approved. */
                : `${candidates.length} ${filters.status || 'pending'}`}
            </span>
            <Button size="sm" variant="ghost" onClick={() => dispatch(selectAll())} className="hover:bg-accent">
              Select All
            </Button>
            {selectedIds.length > 0 && (
              <>
                <Button size="sm" onClick={handleBatchApprove} className="bg-saramsa-brand hover:bg-saramsa-brand/90 text-white">
                  <Check className="w-3.5 h-3.5 mr-1" />
                  Approve All
                </Button>
                <Button size="sm" variant="ghost" onClick={() => dispatch(clearSelected())} className="hover:bg-accent">
                  Clear
                </Button>
              </>
            )}
            {mergeTargetId && (
              <span className="text-xs text-muted-foreground ml-auto">
                Merge target selected — hit “Merge” on a duplicate to fold it in
              </span>
            )}
          </div>
        )}
      </div>

      {/* Scrollable candidate list */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-28 rounded-2xl bg-secondary/70 animate-pulse dark:bg-secondary/40" />
            ))}
          </div>
        ) : candidates.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Inbox className="w-16 h-16 text-muted-foreground/40 mb-4" />
            <h3 className="text-lg font-semibold text-foreground">Your review queue is clear!</h3>
            <p className="text-sm text-muted-foreground mt-1">
              No candidates to review right now. Check back later.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <AnimatePresence mode="popLayout">
              {candidates.slice(0, visibleCount).map((c) => (
                <ReviewQueueItem
                  key={c.id}
                  candidate={c}
                  isSelected={selectedIds.includes(c.id)}
                  onToggleSelect={(id) => dispatch(toggleSelected(id))}
                  onApprove={handleApprove}
                  onEdit={setEditCandidate}
                  onDismiss={handleDismiss}
                  onSnooze={handleSnooze}
                  onMerge={mergeTargetId && mergeTargetId !== c.id ? handleMerge : undefined}
                />
              ))}
            </AnimatePresence>

            {candidates.length > visibleCount && (
              <div className="flex flex-col items-center gap-2 py-4">
                <span className="text-xs text-muted-foreground">
                  Showing {visibleCount} of {candidates.length}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
                  className="border-border/70 hover:bg-accent/60"
                >
                  Load {Math.min(PAGE_SIZE, candidates.length - visibleCount)} more
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Edit drawer */}
      <EditCandidateDrawer
        candidate={editCandidate}
        isOpen={!!editCandidate}
        onClose={() => setEditCandidate(null)}
        onSaveApprove={handleSaveApprove}
        onSaveDraft={handleSaveDraft}
      />
    </div>
  );
}
