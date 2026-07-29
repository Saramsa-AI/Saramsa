"use client";

import { BaseModal } from "./modals/BaseModal";
import { Badge } from "./badge";
import { Button } from "./button";
import type { ActionItem } from "@/store/features/workItems/workItemsSlice";

interface WorkItemReviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  items: ActionItem[];
  platformLabel: string;
  isSubmitting?: boolean;
  /** Push failure text. Rendered in place so the modal can stay open on
   *  failure instead of closing and losing the reason. */
  errorMessage?: string | null;
}

export function WorkItemReviewModal({
  isOpen,
  onClose,
  onConfirm,
  items,
  platformLabel,
  isSubmitting = false,
  errorMessage = null,
}: WorkItemReviewModalProps) {
  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      title="Review User Stories"
      description={`Confirm the items that will be pushed to ${platformLabel}.`}
      size="lg"
    >
      <div className="space-y-4">
        <div className="text-sm text-muted-foreground">
          {items.length} item{items.length === 1 ? "" : "s"} selected
        </div>

        <div className="max-h-[420px] space-y-3 overflow-y-auto pr-2">
          {items.map((item) => (
            <div
              key={item.id}
              className="rounded-2xl border border-border/60 bg-card/80 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-foreground">
                    {item.title}
                  </h4>
                  {item.description && (
                    <p className="text-xs text-muted-foreground">
                      {item.description.length > 180
                        ? `${item.description.slice(0, 180)}…`
                        : item.description}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Badge
                    variant={
                      item.priority === "high" || item.priority === "critical"
                        ? "destructive"
                        : item.priority === "medium"
                        ? "default"
                        : "secondary"
                    }
                  >
                    {item.priority}
                  </Badge>
                  <Badge variant="outline">{item.type}</Badge>
                </div>
              </div>
              {item.tags && item.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {item.tags.map((tag) => (
                    <Badge key={tag} variant="outline" className="text-xs">
                      {tag}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {errorMessage && (
          <div
            role="alert"
            className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            <span className="font-medium">Push failed.</span> {errorMessage}
          </div>
        )}

        <div className="flex items-center justify-end gap-3 pt-2">
          <Button variant="outline" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button
            onClick={() => { void onConfirm(); }}
            disabled={items.length === 0 || isSubmitting}
            className="bg-gradient-to-r from-saramsa-gradient-from to-saramsa-gradient-to text-white"
          >
            {isSubmitting
              ? `Pushing to ${platformLabel}...`
              : errorMessage
              ? 'Retry push'
              : 'Approve & Push'}
          </Button>
        </div>
      </div>
    </BaseModal>
  );
}
