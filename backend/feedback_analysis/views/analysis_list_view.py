"""
Lightweight analysis history list endpoint.

Optimized for sidebar use case - returns minimal data with pagination support.
"""

from rest_framework.views import APIView
from authentication.permissions import IsProjectViewer
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors
from ..services import get_analysis_service
from ..serializers.analysis_list_serializer import AnalysisListSerializer


class AnalysisHistoryListView(APIView):
    """
    Lightweight endpoint for analysis history list.

    Returns only essential fields for sidebar display:
    - id, name, created_at, status
    - comments_count, positive_pct

    Excludes heavy fields (comments, result, dimensions, full payload)
    to reduce payload size by ~95%.

    Query params:
        project_id (required): Project ID
        page (optional): Page number (default: 1)
        page_size (optional): Items per page (default: 20, max: 100)
    """

    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request):
        """Get paginated analysis history list."""
        project_id = request.query_params.get('project_id')

        if not project_id:
            return StandardResponse.validation_error(
                detail="Project ID is required.",
                instance=request.path
            )

        # Parse pagination params
        try:
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 20))

            # Validate pagination params
            if page < 1:
                page = 1
            if page_size < 1 or page_size > 100:
                page_size = 20

        except (ValueError, TypeError):
            page = 1
            page_size = 20

        # Get analysis repository for optimized query
        from ..repositories import AnalysisRepository
        from ..models import Analysis

        repo = AnalysisRepository()

        # Calculate offset
        offset = (page - 1) * page_size

        # Get total count (for pagination metadata)
        total_count = Analysis.objects.filter(
            project_id=str(project_id)
        ).exclude(
            type__in={"slack_feedback", "slack_feedback_item"}
        ).count()

        # Fetch lightweight data using optimized repository method
        # This uses .only() to fetch minimal fields, reducing payload by ~95%
        analyses = repo.get_analysis_history_list_lightweight(
            project_id=project_id,
            offset=offset,
            limit=page_size
        )

        # Serialize with minimal serializer
        serializer = AnalysisListSerializer(analyses, many=True)

        # Calculate pagination metadata
        total_pages = (total_count + page_size - 1) // page_size

        return StandardResponse.success(data={
            "analyses": serializer.data,
            "pagination": {
                "total": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
            "project_id": project_id,
        }, message="Analysis history list retrieved successfully")
