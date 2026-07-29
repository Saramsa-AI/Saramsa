'use client';

import { useEffect } from 'react';
import { useAppDispatch, useAppSelector } from '@/store/hooks';
import { fetchReviewStats } from '@/store/features/review/reviewSlice';

interface ReviewQueueStatsProps {
  projectId: string;
}

/**
 * Every card's headline number is an ALL-TIME total, so the four are directly
 * comparable. Previously Pending/Snoozed were all-time while Approved/Dismissed
 * were week-filtered — identical styling, different basis — so a project with 6
 * approved items displayed "2" and read as though 4 had gone missing. The week
 * figure is still useful as a velocity signal, so it's kept as a sub-line.
 */
const statCards = [
  { key: 'pending' as const, label: 'Pending', color: 'text-saramsa-brand', weekKey: undefined },
  { key: 'approved' as const, label: 'Approved', color: 'text-foreground', weekKey: 'approved_this_week' as const },
  { key: 'dismissed' as const, label: 'Dismissed', color: 'text-muted-foreground', weekKey: 'dismissed_this_week' as const },
  { key: 'snoozed' as const, label: 'Snoozed', color: 'text-foreground', weekKey: undefined },
];

export function ReviewQueueStats({ projectId }: ReviewQueueStatsProps) {
  const dispatch = useAppDispatch();
  const { stats, statsLoading } = useAppSelector((s) => s.review);

  useEffect(() => {
    dispatch(fetchReviewStats(projectId));
  }, [dispatch, projectId]);

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {statCards.map(({ key, label, color, weekKey }) => {
        const thisWeek = weekKey ? stats?.[weekKey] ?? 0 : 0;
        return (
          <div
            key={key}
            className="rounded-2xl border border-border/60 bg-background/40 p-5 flex flex-col items-center justify-center gap-1"
          >
            {statsLoading ? (
              <div className="h-9 w-14 bg-secondary/60 animate-pulse rounded-lg" />
            ) : (
              <span className={`text-3xl font-bold ${color}`}>
                {stats?.[key] ?? 0}
              </span>
            )}
            <span className="text-xs text-muted-foreground text-center">{label}</span>
            {!statsLoading && weekKey && (
              <span className="text-[11px] text-muted-foreground/70 text-center">
                {thisWeek} this week
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
