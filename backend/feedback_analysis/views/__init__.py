"""
Views package for feedback analysis.

Organized into logical modules:
- analysis_views: Core analysis operations + per-analysis read/edit
- task_status_views: Celery task status (JSON + SSE) and recent-task list
- insights_views: Insights and reporting views
- file_upload_views: File upload and processing views
- file_ingest_views: PDF/DOCX/TXT ingestion endpoint
- digest_views: Insight digest preferences + previews
- ingestion_schedule_views: Slack-source ingestion scheduling
"""

from .analysis_views import (
    AnalyzeCommentsView,
    UpdateKeywordsView,
    GetUserCommentsView,
    AnalysisByIdView,
    AnalysisRenameView,
)
from .task_status_views import (
    TaskStatusView,
    TaskListView,
)
from .insights_views import (
    InsightsListView,
    InsightDetailView,
    InsightsByTypeView,
    AnalysisHistoryView,
    AnalysisByQuarterView,
    CumulativeAnalysisView,
    AnalysisComparisonView,
    UserStoriesView,
    UserStoryUpdateView,
    UserStoryDeleteView,
    UserStoryBulkDeleteView,
    InsightReviewListView,
    InsightReviewUpdateView,
    InsightRulesView,
    InsightRulesApplyView,
)
from .ingestion_schedule_views import (
    IngestionScheduleView,
    IngestionRunNowView,
)
from .file_upload_views import (
    FeedbackFileUploadView,
)
from .file_ingest_views import (
    FeedbackFileIngestView,
)
from .digest_views import (
    DigestPreferenceView,
    DigestPreviewView,
    DigestSendNowView,
)

__all__ = [
    # Analysis views
    'AnalyzeCommentsView',
    'UpdateKeywordsView',
    'GetUserCommentsView',
    'TaskStatusView',
    'TaskListView',
    'AnalysisByIdView',
    'AnalysisRenameView',
    # Insights views
    'InsightsListView',
    'InsightDetailView',
    'InsightsByTypeView',
    'AnalysisHistoryView',
    'AnalysisByQuarterView',
    'CumulativeAnalysisView',
    'AnalysisComparisonView',
    'UserStoriesView',
    'UserStoryUpdateView',
    'UserStoryDeleteView',
    'UserStoryBulkDeleteView',
    'InsightReviewListView',
    'InsightReviewUpdateView',
    'InsightRulesView',
    'InsightRulesApplyView',
    # Ingestion schedule views
    'IngestionScheduleView',
    'IngestionRunNowView',
    # File upload views
    'FeedbackFileUploadView',
    'FeedbackFileIngestView',
    # Digest views
    'DigestPreferenceView',
    'DigestPreviewView',
    'DigestSendNowView',
]

