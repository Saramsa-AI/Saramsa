'use client';

import { BarChart3, Loader2 } from 'lucide-react';
import { AnalysisRunItem } from './AnalysisRunItem';
import type { AnalysisHistoryEntry } from '@/store/features/analysis/analysisSlice';

interface AnalysisRunListProps {
  entries: AnalysisHistoryEntry[];
  selectedId: string | null;
  isLoading: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onDelete?: (id: string) => Promise<void>;
  onCancel?: (id: string, taskId: string) => Promise<void>;
  onRetry?: (id: string) => Promise<void>;
  projectName?: string;
}

export function AnalysisRunList({
  entries, selectedId, isLoading, onSelect, onRename, onDelete, onCancel, onRetry, projectName,
}: AnalysisRunListProps) {
  return (
    <aside className="w-[280px] flex-shrink-0 h-full flex flex-col bg-card/60 border border-border/60 rounded-none overflow-hidden">
      {/* Header with project name */}
      <div className="px-4 py-3 border-b border-border/40">
        {projectName ? (
          <>
            <h2 className="text-sm font-semibold text-foreground truncate">{projectName}</h2>
            <p className="text-xs text-muted-foreground mt-1">Tasks</p>
          </>
        ) : (
          <h2 className="text-sm font-semibold text-foreground">Tasks</h2>
        )}
      </div>

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto min-h-0 divide-y divide-border/40">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="w-6 h-6 animate-spin mb-2" />
            <span className="text-xs">Loading history...</span>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <BarChart3 className="w-8 h-8 mb-2 opacity-40" />
            <span className="text-xs text-center">No analysis runs yet.<br />Upload feedback to get started.</span>
          </div>
        ) : (
          entries.map((entry, i) => (
            <AnalysisRunItem
              key={entry.id}
              entry={entry}
              isActive={selectedId === entry.id}
              onClick={() => onSelect(entry.id)}
              onRename={onRename}
              onDelete={onDelete}
              onCancel={onCancel}
              onRetry={onRetry}
              index={i}
              totalCount={entries.length}
            />
          ))
        )}
      </div>
    </aside>
  );
}


