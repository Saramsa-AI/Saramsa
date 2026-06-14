"""
Filtered Analysis View - Apply dimension filters to analysis data
"""

import logging
from typing import Dict, List, Any
from rest_framework.views import APIView

from ..models import Analysis
from authentication.permissions import IsProjectViewer
from apis.core.response import StandardResponse

logger = logging.getLogger(__name__)


class FilteredAnalysisView(APIView):
    """
    POST /api/feedback/analysis/filtered/

    Body:
    {
        "analysis_id": "insight_xxx",
        "filters": [
            {"key": "platform", "operator": "eq", "value": "Android"},
            {"key": "rating", "operator": "gte", "value": "4"}
        ]
    }

    Returns analysis results filtered by dimensions.
    """
    permission_classes = [IsProjectViewer]

    def post(self, request):
        try:
            analysis_id = request.data.get('analysis_id')
            filters = request.data.get('filters', [])

            if not analysis_id:
                return StandardResponse.validation_error(
                    detail='analysis_id is required',
                    errors=[{'field': 'analysis_id', 'message': 'This field is required'}]
                )

            # Get analysis
            try:
                analysis = Analysis.objects.get(id=str(analysis_id), type='insight')
            except Analysis.DoesNotExist:
                return StandardResponse.not_found(
                    detail=f'Analysis {analysis_id} not found'
                )

            if not analysis.dimensions or len(analysis.dimensions) == 0:
                return StandardResponse.validation_error(
                    detail='This analysis does not have dimension data',
                    errors=[{'field': 'analysis_id', 'message': 'No dimensions available'}]
                )

            # Filter comments by dimensions
            filtered_indices = []
            for idx, dim_dict in enumerate(analysis.dimensions):
                if not isinstance(dim_dict, dict):
                    continue

                if self._matches_all_filters(dim_dict, filters):
                    filtered_indices.append(idx)

            if len(filtered_indices) == 0:
                return StandardResponse.success(
                    data={
                        'analysis_id': analysis_id,
                        'total_comments': len(analysis.dimensions),
                        'filtered_comments': 0,
                        'filters_applied': len(filters),
                        'message': 'No comments match the applied filters'
                    }
                )

            # Get filtered comments
            comments = analysis.comments or []
            filtered_comments = [comments[i] for i in filtered_indices if i < len(comments)]

            # Recalculate feature sentiments based on filtered comments
            # For now, just return counts - full recalculation would require re-analysis
            result = analysis.result or {}
            features = result.get('features', [])

            # Simple approach: return same features but with filtered comment info
            return StandardResponse.success(
                data={
                    'analysis_id': analysis_id,
                    'total_comments': len(analysis.dimensions),
                    'filtered_comments': len(filtered_indices),
                    'filtered_percentage': round(len(filtered_indices) / len(analysis.dimensions) * 100, 1),
                    'filters_applied': len(filters),
                    'features': features,  # Same features, but note they're from full dataset
                    'note': 'Feature sentiments are from full dataset. Filtered feature calculation requires re-analysis.',
                    'filtered_comment_sample': filtered_comments[:10]  # First 10 for preview
                }
            )

        except Exception as e:
            logger.exception("Failed to filter analysis")
            return StandardResponse.error(
                title='Filtered Analysis Failed',
                detail=str(e),
                status_code=500,
                error_type='filtered-analysis-error'
            )

    def _matches_all_filters(self, dim_dict: Dict, filters: List[Dict]) -> bool:
        """Check if dimension dict matches all filters"""
        for filter_obj in filters:
            key = filter_obj.get('key')
            operator = filter_obj.get('operator', 'eq')
            value = filter_obj.get('value')

            if key not in dim_dict:
                return False

            dim_value = dim_dict[key]
            if dim_value is None:
                return False

            if not self._apply_operator(str(dim_value), operator, value):
                return False

        return True

    def _apply_operator(self, dim_value: str, operator: str, filter_value: Any) -> bool:
        """Apply comparison operator"""
        try:
            if operator == 'eq':
                return dim_value == str(filter_value)
            elif operator == 'in':
                if isinstance(filter_value, list):
                    return dim_value in [str(v) for v in filter_value]
                elif isinstance(filter_value, str):
                    return dim_value in filter_value.split(',')
            elif operator in ['gte', 'lte', 'gt', 'lt']:
                val_num = float(dim_value)
                target_num = float(filter_value)
                if operator == 'gte':
                    return val_num >= target_num
                elif operator == 'lte':
                    return val_num <= target_num
                elif operator == 'gt':
                    return val_num > target_num
                elif operator == 'lt':
                    return val_num < target_num
            return False
        except (ValueError, TypeError):
            return False
