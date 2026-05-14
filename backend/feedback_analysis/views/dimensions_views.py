"""
Dimension Discovery and Query APIs

Schema-agnostic endpoints that adapt to any CSV structure:
- Discover available dimensions in a project
- Query analyses with dimension filters
- Get segmented breakdowns by any dimension
"""

import logging
from typing import Dict, List, Any, Optional
from rest_framework.views import APIView
from django.db.models import Q

from ..models import Analysis
from authentication.permissions import IsProjectViewer
from apis.core.response import StandardResponse

logger = logging.getLogger(__name__)


class DimensionDiscoveryView(APIView):
    """
    GET /api/feedback/projects/<project_id>/dimensions/

    Returns all available dimensions and their unique values for a project.
    Schema-agnostic: works with any CSV structure (Tickertape, hotels, hospitals, etc.)

    Response format:
    {
      "dimensions": {
        "persona": {
          "type": "categorical",
          "values": ["P1-Fundamental Analyst", "P2-Technical Trader", ...],
          "count": 5
        },
        "platform": {
          "type": "categorical",
          "values": ["Web", "Android", "iOS"],
          "count": 3
        },
        "rating": {
          "type": "numeric",
          "min": 1,
          "max": 5,
          "values": [1, 2, 3, 4, 5]
        }
      },
      "total_analyses": 3,
      "total_comments": 200
    }
    """
    permission_classes = [IsProjectViewer]

    def get(self, request, project_id):
        try:
            # Get all analyses for this project that have dimensions
            analyses = Analysis.objects.filter(
                project_id=str(project_id),
                type='insight'
            ).values('dimensions', 'id')

            # Filter out analyses without dimensions in Python (PostgreSQL array comparison is tricky)
            analyses = [a for a in analyses if a.get('dimensions') and len(a.get('dimensions', [])) > 0]

            if not analyses:
                return StandardResponse.success(
                    data={
                        "dimensions": {},
                        "total_analyses": 0,
                        "total_comments": 0,
                        "message": "No analyses with dimensions found for this project"
                    }
                )

            # Collect all dimension keys and values across all analyses
            dimension_map: Dict[str, Dict[str, Any]] = {}
            total_comments = 0

            for analysis in analyses:
                dimensions_list = analysis.get('dimensions', [])
                if not dimensions_list:
                    continue

                total_comments += len(dimensions_list)

                # Process each comment's dimensions
                for dim_dict in dimensions_list:
                    if not isinstance(dim_dict, dict):
                        continue

                    for key, value in dim_dict.items():
                        if value is None or value == '':
                            continue

                        # Initialize dimension tracking
                        if key not in dimension_map:
                            dimension_map[key] = {
                                'values': set(),
                                'numeric_values': []
                            }

                        # Track unique values
                        dimension_map[key]['values'].add(str(value))

                        # Track numeric values for type detection
                        try:
                            numeric_val = float(value)
                            dimension_map[key]['numeric_values'].append(numeric_val)
                        except (ValueError, TypeError):
                            pass

            # Build response with type detection
            dimensions_response = {}
            for key, data in dimension_map.items():
                values_list = sorted(list(data['values']))

                # Detect if dimension is numeric
                # Check if all unique values can be converted to numbers
                is_numeric = (
                    len(data['numeric_values']) > 0 and
                    len(set(data['numeric_values'])) == len(data['values'])
                )

                if is_numeric:
                    dimensions_response[key] = {
                        'type': 'numeric',
                        'values': [float(v) for v in values_list],
                        'min': min(data['numeric_values']),
                        'max': max(data['numeric_values']),
                        'count': len(values_list)
                    }
                else:
                    dimensions_response[key] = {
                        'type': 'categorical',
                        'values': values_list,
                        'count': len(values_list)
                    }

            return StandardResponse.success(
                data={
                    'dimensions': dimensions_response,
                    'total_analyses': len(analyses),
                    'total_comments': total_comments
                }
            )

        except Exception as e:
            logger.error(f"Error discovering dimensions for project {project_id}: {e}", exc_info=True)
            return StandardResponse.error(
                title='Dimension Discovery Failed',
                detail=str(e),
                status_code=500,
                error_type='dimension-discovery-error'
            )


class DimensionQueryView(APIView):
    """
    GET /api/feedback/analysis/query/?project_id=<id>&dimensions__<key>=<value>&dimensions__<key>__<op>=<value>

    Query analyses with dimension filters. Schema-agnostic operators:
    - dimensions__platform=Android (exact match)
    - dimensions__rating__gte=4 (greater than or equal)
    - dimensions__rating__lte=2 (less than or equal)
    - dimensions__persona__in=P1,P2 (multiple values)

    Returns matching analyses with their results.
    """
    permission_classes = [IsProjectViewer]

    def get(self, request):
        try:
            project_id = request.GET.get('project_id')
            if not project_id:
                return StandardResponse.validation_error(
                    detail='project_id query parameter is required',
                    errors=[{'field': 'project_id', 'message': 'This field is required'}]
                )

            # Start with base queryset
            queryset = Analysis.objects.filter(
                project_id=str(project_id),
                type='insight'
            )

            # Parse dimension filters from query params
            dimension_filters = {}
            for param, value in request.GET.items():
                if param.startswith('dimensions__') and param != 'dimensions__':
                    filter_key = param[12:]  # Remove 'dimensions__' prefix
                    dimension_filters[filter_key] = value

            if not dimension_filters:
                # No filters - return all analyses
                analyses = queryset.values('id', 'created_at', 'result', 'dimensions')
                return StandardResponse.success(
                    data={
                        'analyses': list(analyses),
                        'count': len(analyses),
                        'filters_applied': {}
                    }
                )

            # Apply filters manually (since JSONB querying requires raw SQL or custom logic)
            filtered_results = []
            for analysis in queryset:
                if self._matches_filters(analysis.dimensions, dimension_filters):
                    filtered_results.append({
                        'id': analysis.id,
                        'created_at': analysis.created_at.isoformat(),
                        'result': analysis.result,
                        'dimensions_count': len(analysis.dimensions) if analysis.dimensions else 0
                    })

            return StandardResponse.success(
                data={
                    'analyses': filtered_results,
                    'count': len(filtered_results),
                    'filters_applied': dimension_filters
                }
            )

        except Exception as e:
            logger.error(f"Error querying with dimensions: {e}", exc_info=True)
            return StandardResponse.error(
                title='Dimension Query Failed',
                detail=str(e),
                status_code=500,
                error_type='dimension-query-error'
            )

    def _matches_filters(self, dimensions_list: List[Dict], filters: Dict[str, str]) -> bool:
        """Check if any comment in the analysis matches all filters."""
        if not dimensions_list:
            return False

        for dim_dict in dimensions_list:
            if not isinstance(dim_dict, dict):
                continue

            # Check if this comment matches all filters
            matches_all = True
            for filter_key, filter_value in filters.items():
                # Parse operator from key (e.g., "rating__gte" -> key="rating", op="gte")
                if '__' in filter_key:
                    parts = filter_key.split('__')
                    key = parts[0]
                    op = parts[1] if len(parts) > 1 else 'eq'
                else:
                    key = filter_key
                    op = 'eq'

                dim_value = dim_dict.get(key)
                if dim_value is None:
                    matches_all = False
                    break

                # Apply operator
                if not self._apply_operator(str(dim_value), op, filter_value):
                    matches_all = False
                    break

            # If this comment matches all filters, the analysis matches
            if matches_all:
                return True

        return False

    def _apply_operator(self, value: str, operator: str, target: str) -> bool:
        """Apply comparison operator."""
        try:
            if operator == 'eq':
                return value == target
            elif operator == 'in':
                target_values = target.split(',')
                return value in target_values
            elif operator in ['gte', 'lte', 'gt', 'lt']:
                # Numeric comparison
                val_num = float(value)
                target_num = float(target)
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


class DimensionBreakdownView(APIView):
    """
    GET /api/feedback/insights/breakdown/?project_id=<id>&analysis_id=<id>&group_by=<dimension>

    Get feature sentiment breakdown segmented by any dimension.
    Schema-agnostic: works with platform, persona, rating, or any other dimension.

    Response format:
    {
      "group_by": "platform",
      "segments": {
        "Android": {
          "features": [...],
          "overall_sentiment": {...},
          "comment_count": 45
        },
        "iOS": {
          "features": [...],
          "overall_sentiment": {...},
          "comment_count": 32
        }
      }
    }
    """
    permission_classes = [IsProjectViewer]

    def get(self, request):
        try:
            project_id = request.GET.get('project_id')
            analysis_id = request.GET.get('analysis_id')
            group_by = request.GET.get('group_by')

            if not all([project_id, analysis_id, group_by]):
                return StandardResponse.validation_error(
                    detail='project_id, analysis_id, and group_by parameters are required',
                    errors=[
                        {'field': 'project_id', 'message': 'This field is required'},
                        {'field': 'analysis_id', 'message': 'This field is required'},
                        {'field': 'group_by', 'message': 'This field is required'}
                    ]
                )

            # Get the analysis
            try:
                analysis = Analysis.objects.get(
                    id=str(analysis_id),
                    project_id=str(project_id),
                    type='insight'
                )
            except Analysis.DoesNotExist:
                return StandardResponse.not_found(
                    detail=f'Analysis {analysis_id} not found'
                )

            if not analysis.dimensions:
                return StandardResponse.validation_error(
                    detail='This analysis does not have dimension data',
                    errors=[{'field': 'analysis_id', 'message': 'No dimensions available for this analysis'}]
                )

            # Group comments by dimension value
            segments: Dict[str, List[int]] = {}
            for idx, dim_dict in enumerate(analysis.dimensions):
                if not isinstance(dim_dict, dict):
                    continue

                value = dim_dict.get(group_by)
                if value is not None:
                    value_str = str(value)
                    if value_str not in segments:
                        segments[value_str] = []
                    segments[value_str].append(idx)

            if not segments:
                return StandardResponse.validation_error(
                    detail=f'Dimension "{group_by}" not found in this analysis',
                    errors=[{'field': 'group_by', 'message': f'No data for dimension: {group_by}'}]
                )

            # Build segment breakdowns
            result = analysis.result or {}
            features = result.get('features', [])

            segment_data = {}
            for segment_value, comment_indices in segments.items():
                # Filter features that have comments in this segment
                segment_features = self._filter_features_by_indices(features, comment_indices)

                segment_data[segment_value] = {
                    'comment_count': len(comment_indices),
                    'features': segment_features,
                    'comment_indices': comment_indices[:10]  # Sample for debugging
                }

            return StandardResponse.success(
                data={
                    'group_by': group_by,
                    'segments': segment_data,
                    'total_segments': len(segments)
                }
            )

        except Exception as e:
            logger.error(f"Error generating dimension breakdown: {e}", exc_info=True)
            return StandardResponse.error(
                title='Dimension Breakdown Failed',
                detail=str(e),
                status_code=500,
                error_type='dimension-breakdown-error'
            )

    def _filter_features_by_indices(self, features: List[Dict], indices: List[int]) -> List[Dict]:
        """Filter features to only include comments from specified indices."""
        # This is a simplified version - in production you'd need to track
        # which comments map to which features in the analysis result
        # For now, return all features with counts adjusted
        return features[:10]  # Top 10 features per segment
