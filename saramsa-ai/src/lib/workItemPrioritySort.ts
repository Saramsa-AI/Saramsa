/** Display order: critical first, then high, medium, low; unknown values last. */
const PRIORITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function workItemPriorityRank(priority: string | undefined): number {
  const key = (priority ?? 'medium').toLowerCase().trim();
  return key in PRIORITY_RANK ? PRIORITY_RANK[key] : 4;
}

/**
 * Display the priority tier as a number, matching the Azure DevOps / Jira
 * convention where 1 is the most urgent: critical=1, high=2, medium=3, low=4.
 * Returns null for an absent or unrecognized value so the caller can omit the
 * badge rather than render a misleading number.
 */
export function workItemPriorityNumber(priority: string | undefined): number | null {
  if (!priority) return null;
  const key = String(priority).toLowerCase().trim();
  // Already numeric ("1") or in P0-P3 form — normalize both onto 1-4.
  const pForm = /^p([0-3])$/.exec(key);
  if (pForm) return Number(pForm[1]) + 1;
  if (/^[1-4]$/.test(key)) return Number(key);
  return key in PRIORITY_RANK ? PRIORITY_RANK[key] + 1 : null;
}

/**
 * Sort by priority tier first (critical..low), then — within the same tier —
 * by descending comment count (most feedback-backed items first) when a
 * count is available. Priority stays the primary key; comment count is a
 * same-tier tie-breaker, not a replacement sort.
 */
export function sortWorkItemsByPriority<T extends { priority?: string; commentCount?: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const priorityDiff = workItemPriorityRank(a.priority) - workItemPriorityRank(b.priority);
    if (priorityDiff !== 0) return priorityDiff;
    return (b.commentCount ?? 0) - (a.commentCount ?? 0);
  });
}
