'use client';

import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Check, Edit2, X, Clock, GitMerge, MessageSquare } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import type { ReviewCandidate } from '@/store/features/review/reviewSlice';

interface ReviewQueueItemProps {
  candidate: ReviewCandidate;
  isSelected: boolean;
  onToggleSelect: (id: string) => void;
  onApprove: (id: string) => void;
  onEdit: (candidate: ReviewCandidate) => void;
  onDismiss: (id: string, reason: string) => void;
  onSnooze: (id: string, days: number) => void;
  onMerge?: (id: string) => void;
}

// Numbered 1-4 (1 = most urgent), matching the priority badge on the user
// stories list. These were P0-P3 here while the list showed the word form,
// so the same candidate appeared as "P0" in one place and "critical" in the
// other; keep the two in sync via workItemPriorityNumber().
const priorityConfig: Record<string, { color: string; label: string }> = {
  critical: { color: 'bg-red-500/90 text-white dark:bg-red-500/80', label: '1' },
  high: { color: 'bg-saramsa-brand text-white', label: '2' },
  medium: { color: 'bg-muted text-foreground dark:bg-muted/80', label: '3' },
  low: { color: 'bg-secondary text-muted-foreground', label: '4' },
};

const dismissReasons = [
  { value: 'not_relevant', label: 'Not relevant' },
  { value: 'already_known', label: 'Already known' },
  { value: 'will_not_fix', label: 'Will not fix' },
  { value: 'duplicate', label: 'Duplicate' },
];

const snoozeOptions = [
  { days: 7, label: '1 week' },
  { days: 14, label: '2 weeks' },
  { days: 30, label: '1 month' },
];

export function ReviewQueueItem({
  candidate,
  isSelected,
  onToggleSelect,
  onApprove,
  onEdit,
  onDismiss,
  onSnooze,
  onMerge,
}: ReviewQueueItemProps) {
  const [showDismiss, setShowDismiss] = useState(false);
  const [showSnooze, setShowSnooze] = useState(false);
  const prio = priorityConfig[candidate.priority] || priorityConfig.low;
  const actionsRef = useRef<HTMLDivElement>(null);

  // Close the Dismiss/Snooze menus on outside click or Escape. Without this
  // they stayed open indefinitely — and because each row owns its own state,
  // opening a menu on one row left any already-open menu on another row open too.
  const anyMenuOpen = showDismiss || showSnooze;
  useEffect(() => {
    if (!anyMenuOpen) return;
    const closeAll = () => {
      setShowDismiss(false);
      setShowSnooze(false);
    };
    const onPointerDown = (e: MouseEvent) => {
      if (actionsRef.current && !actionsRef.current.contains(e.target as Node)) closeAll();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeAll();
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [anyMenuOpen]);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: -100, height: 0 }}
      transition={{ duration: 0.25 }}
      className="group rounded-2xl border border-border/60 bg-background/40 hover:border-border/80 p-4 transition-all"
    >
      <div className="flex items-start gap-3">
        <Checkbox
          checked={isSelected}
          onCheckedChange={() => onToggleSelect(candidate.id)}
          className="mt-1 data-[state=checked]:bg-saramsa-brand data-[state=checked]:border-saramsa-brand"
        />

        <span
          className={`px-2.5 py-1 rounded-lg text-xs font-semibold tabular-nums ${prio.color} shrink-0`}
          title={`Priority ${prio.label} (${candidate.priority}) — 1 is most urgent`}
        >
          {prio.label}
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-foreground truncate">{candidate.title}</h3>
            {candidate.feature_area && (
              <Badge variant="secondary" className="text-xs shrink-0 bg-secondary/80">
                {candidate.feature_area}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground line-clamp-2 mt-1">
            {candidate.description}
          </p>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {candidate.comment_count != null && candidate.comment_count > 0 && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <MessageSquare className="w-3.5 h-3.5" />
              {candidate.comment_count}
            </span>
          )}
          <span className="text-xs text-muted-foreground">
            {new Date(candidate.createdAt).toLocaleDateString()}
          </span>
        </div>
      </div>

      {/* Action buttons */}
      <div ref={actionsRef} className="flex items-center gap-2 mt-4 ml-10">
        <Button
          size="sm"
          onClick={() => onApprove(candidate.id)}
          className="bg-saramsa-brand hover:bg-saramsa-brand/90 text-white"
        >
          <Check className="w-3.5 h-3.5 mr-1.5" />
          Approve
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() => onEdit(candidate)}
          className="border-border/70 hover:bg-accent/60"
        >
          <Edit2 className="w-3.5 h-3.5 mr-1.5" />
          Edit
        </Button>

        <div className="relative">
          <Button
            size="sm"
            variant="outline"
            onClick={() => { setShowDismiss(!showDismiss); setShowSnooze(false); }}
            className="border-border/70 hover:bg-accent/60"
          >
            <X className="w-3.5 h-3.5 mr-1.5" />
            Dismiss
          </Button>
          {showDismiss && (
            <div className="absolute top-full left-0 mt-2 z-20 w-44 rounded-xl border border-border/60 bg-popover shadow-lg py-1">
              {dismissReasons.map((r) => (
                <button
                  key={r.value}
                  onClick={() => { onDismiss(candidate.id, r.value); setShowDismiss(false); }}
                  className="w-full text-left px-3 py-2 text-sm text-foreground hover:bg-accent transition-colors"
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="relative">
          <Button
            size="sm"
            variant="outline"
            onClick={() => { setShowSnooze(!showSnooze); setShowDismiss(false); }}
            className="border-border/70 hover:bg-accent/60"
          >
            <Clock className="w-3.5 h-3.5 mr-1.5" />
            Snooze
          </Button>
          {showSnooze && (
            <div className="absolute top-full left-0 mt-2 z-20 w-36 rounded-xl border border-border/60 bg-popover shadow-lg py-1">
              {snoozeOptions.map((o) => (
                <button
                  key={o.days}
                  onClick={() => { onSnooze(candidate.id, o.days); setShowSnooze(false); }}
                  className="w-full text-left px-3 py-2 text-sm text-foreground hover:bg-accent transition-colors cursor-pointer"
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Merge — only rendered when the page supplies a handler. The backend
            endpoint and service existed all along, but no UI ever called it. */}
        {onMerge && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onMerge(candidate.id)}
            className="border-border/70 hover:bg-accent/60"
            title="Merge this item into another (its evidence moves to the target)"
          >
            <GitMerge className="w-3.5 h-3.5 mr-1.5" />
            Merge
          </Button>
        )}
      </div>
    </motion.div>
  );
}
