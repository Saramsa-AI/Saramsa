from django.urls import path
from .views import (
    WorkItemGenerationView,
    WorkItemSubmissionView,
    WorkItemsListView,
    WorkItemUpdateView,
    WorkItemRemovalView,
    WorkItemQualityRulesView,
    WorkItemQualityCheckView,
)
from .views.review_views import (
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
from .v2.views import WorkItemV2PreviewView

urlpatterns = [
    # Review queue endpoints (Must come BEFORE generic <work_item_id> pattern)
    path('review/', ReviewQueueListView.as_view(), name='review_queue_list'),
    path('review/stats/', ReviewQueueStatsView.as_view(), name='review_queue_stats'),
    path('review/approve/', CandidateApproveView.as_view(), name='review_approve'),
    path('review/dismiss/', CandidateDismissView.as_view(), name='review_dismiss'),
    path('review/snooze/', CandidateSnoozeView.as_view(), name='review_snooze'),
    path('review/merge/', CandidateMergeView.as_view(), name='review_merge'),
    path('review/batch-approve/', CandidateBatchApproveView.as_view(), name='review_batch_approve'),
    path('review/update/', CandidateUpdateView.as_view(), name='review_update'),
    path('review/retry-push/', CandidateRetryPushView.as_view(), name='review_retry_push'),

    # Core work items endpoints
    path('generate/', WorkItemGenerationView.as_view(), name='work_items_generate'),
    path('submit/', WorkItemSubmissionView.as_view(), name='work_items_submit'),
    path('', WorkItemsListView.as_view(), name='work_items_list'),
    path('remove/', WorkItemRemovalView.as_view(), name='work_items_remove'),
    path('quality-rules/', WorkItemQualityRulesView.as_view(), name='work_item_quality_rules'),
    path('quality-check/', WorkItemQualityCheckView.as_view(), name='work_item_quality_check'),

    # Experimental V2 pipeline (preview-only, see work_items/v2/CONTRACT.md)
    path('v2/generate-preview/', WorkItemV2PreviewView.as_view(), name='work_items_v2_preview'),

    # Generic patterns MUST come last
    path('<str:work_item_id>/update/', WorkItemUpdateView.as_view(), name='work_item_update'),
]
