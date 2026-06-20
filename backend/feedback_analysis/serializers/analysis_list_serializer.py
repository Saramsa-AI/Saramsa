"""
Lightweight serializer for analysis history list view.

Optimized for sidebar display - returns only essential fields
to minimize payload size and improve performance.
"""

from rest_framework import serializers
from typing import Dict, Any


class AnalysisListSerializer(serializers.Serializer):
    """
    Minimal serializer for analysis list items.

    Returns only fields needed for sidebar:
    - Basic identifiers (id, name)
    - Timestamps
    - Status
    - Comment count
    - Positive sentiment percentage

    Excludes heavy fields like comments, result, dimensions, full payload.
    """

    id = serializers.CharField(read_only=True)
    name = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True, source='createdAt')
    status = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    positive_pct = serializers.SerializerMethodField()
    display_number = serializers.IntegerField(read_only=True, allow_null=True)
    task_id = serializers.CharField(read_only=True, allow_blank=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def get_name(self, obj: Dict[str, Any]) -> str:
        """Extract analysis name from various possible locations."""
        # Check direct field
        if obj.get('name'):
            return obj['name']

        # Check payload
        payload = obj.get('payload', {})
        if isinstance(payload, dict) and payload.get('name'):
            return payload['name']

        # Check analysisData
        analysis_data = obj.get('analysisData', {})
        if isinstance(analysis_data, dict) and analysis_data.get('name'):
            return analysis_data['name']

        # Fallback to formatted created_at
        created_at = obj.get('createdAt', '')
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                return f"Analysis {dt.strftime('%Y-%m-%d %H:%M')}"
            except Exception:
                pass

        return "Unnamed Analysis"

    def get_status(self, obj: Dict[str, Any]) -> str:
        """Determine analysis status."""
        # Check direct status field
        if obj.get('status'):
            return obj['status']

        # Check if result exists and is not empty
        result = obj.get('result', {})
        analysis_data = obj.get('analysisData', {})

        if result or analysis_data:
            return "completed"

        return "pending"

    def get_comments_count(self, obj: Dict[str, Any]) -> int:
        """Get total comments count from metadata."""
        if obj.get('comments_count') is not None:
            return int(obj.get('comments_count') or 0)

        # Try to get from counts metadata first (most efficient)
        counts = (
            obj.get('counts') or
            obj.get('analysisData', {}).get('counts') or
            obj.get('result', {}).get('counts') or
            {}
        )

        if isinstance(counts, dict) and counts.get('total'):
            return int(counts['total'])

        # Fallback: count comments array length (less efficient but necessary)
        comments = obj.get('comments', [])
        if isinstance(comments, list):
            return len(comments)

        return 0

    def get_positive_pct(self, obj: Dict[str, Any]) -> float:
        """Calculate positive sentiment percentage from metadata."""
        if obj.get('positive_pct') is not None:
            return float(obj.get('positive_pct') or 0.0)

        # Try to get from sentiment summary first
        sentiment_summary = (
            obj.get('sentimentsummary') or
            obj.get('sentiment_summary') or
            obj.get('analysisData', {}).get('sentimentsummary') or
            obj.get('analysisData', {}).get('sentiment_summary') or
            obj.get('result', {}).get('sentimentsummary') or
            obj.get('result', {}).get('sentiment_summary') or
            {}
        )

        if isinstance(sentiment_summary, dict):
            positive = sentiment_summary.get('positive', 0)
            negative = sentiment_summary.get('negative', 0)
            neutral = sentiment_summary.get('neutral', 0)
            total = positive + negative + neutral

            if total > 0:
                return round((positive / total) * 100, 1)

        return 0.0
