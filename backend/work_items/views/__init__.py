"""
Work Items Views Package

Organized views for work item operations:
- work_item_views: Core work item CRUD operations
"""

from .work_item_views import (
    WorkItemGenerationView,
    WorkItemSubmissionView,
    WorkItemsListView,
    WorkItemDetailView,
    WorkItemUpdateView,
    WorkItemRemovalView,
    WorkItemQualityRulesView,
    WorkItemQualityCheckView,
)

from .review_views import (
    ReviewQueueListView,
    ReviewQueueStatsView,
    CandidateApproveView,
    CandidateDismissView,
    CandidateSnoozeView,
    CandidateMergeView,
    CandidateBatchApproveView,
    CandidateUpdateView,
    CandidateRetryPushView,
)

__all__ = [
    'WorkItemGenerationView',
    'WorkItemSubmissionView',
    'WorkItemsListView',
    'WorkItemDetailView',
    'WorkItemUpdateView',
    'WorkItemRemovalView',
    'ReviewQueueListView',
    'ReviewQueueStatsView',
    'CandidateApproveView',
    'CandidateDismissView',
    'CandidateSnoozeView',
    'CandidateMergeView',
    'CandidateBatchApproveView',
    'CandidateUpdateView',
    'CandidateRetryPushView',
]
