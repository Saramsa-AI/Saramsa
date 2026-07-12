'use client';

import { useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X, AlertTriangle, Trash2 } from 'lucide-react';
import type { Project } from '@/store/features/projects/projectsSlice';
import { Button } from '@/components/ui/button';
import { lockBodyScroll, unlockBodyScroll } from '@/lib/bodyScrollLock';

interface DeleteProjectModalProps {
  project: Project;
  onConfirm: () => void;
  onCancel: () => void;
  loading?: boolean;
  error?: string | null;
  isOpen?: boolean;
}

export function DeleteProjectModal({
  project,
  onConfirm,
  onCancel,
  loading = false,
  error = null,
  isOpen = true,
}: DeleteProjectModalProps) {
  const modalRoot = typeof document !== 'undefined' ? document.body : null;

  useEffect(() => {
    if (!modalRoot || !isOpen) return;

    lockBodyScroll();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      unlockBodyScroll();
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, modalRoot, onCancel]);

  const content = useMemo(() => {
    if (!modalRoot || !isOpen) return null;

    return createPortal(
      <div
        className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4"
        onClick={loading ? undefined : onCancel}
      >
        <AnimatePresence>
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.18 }}
            className="bg-card/90 dark:bg-card/95 rounded-xl shadow-xl max-w-md w-full focus:outline-none"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-project-modal-title"
          >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-border/60 dark:border-border/60">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 dark:bg-red-900/30 rounded-xl flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
            </div>
            <h3 id="delete-project-modal-title" className="text-lg font-semibold text-foreground dark:text-foreground">
              Delete Project
            </h3>
          </div>
          <Button
            onClick={loading ? undefined : onCancel}
            variant="ghost"
            size="icon"
            className="h-8 w-8 hover:bg-accent/60 dark:hover:bg-accent/60"
            aria-label="Close"
            type="button"
            disabled={loading}
          >
            <X className="w-5 h-5 text-muted-foreground" />
          </Button>
        </div>

        {/* Content */}
        <div className="p-6">
          <p className="text-muted-foreground dark:text-muted-foreground mb-4">
            Are you sure you want to delete the project <strong>"{project.name}"</strong>?
            This action cannot be undone and will permanently remove:
          </p>

          <ul className="space-y-2 text-sm text-muted-foreground dark:text-muted-foreground mb-6">
            <li className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-muted-foreground rounded-full" />
              All project data and files
            </li>
            <li className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-muted-foreground rounded-full" />
              Analysis results and insights
            </li>
            <li className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-muted-foreground rounded-full" />
              Integration connections
            </li>
          </ul>

          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-3">
            <p className="text-sm text-red-700 dark:text-red-300">
              <strong>Warning:</strong> This action is permanent and cannot be reversed.
            </p>
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="px-6 -mt-2 pb-2">
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-3">
              <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-border/60 dark:border-border/60">
          <Button
            onClick={loading ? undefined : onCancel}
            type="button"
            disabled={loading}
            variant="outline"
            className="px-4 py-2 text-muted-foreground dark:text-muted-foreground"
          >
            Cancel
          </Button>
          <Button
            onClick={onConfirm}
            type="button"
            disabled={loading}
            variant="destructive"
            className="flex items-center gap-2 px-4 py-2"
          >
            {loading ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Deleting...
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                Delete Project
              </>
            )}
          </Button>
        </div>
          </motion.div>
        </AnimatePresence>
      </div>,
      modalRoot
    );
  }, [isOpen, loading, error, modalRoot, onCancel, onConfirm, project.name]);

  return content;
}


