"""
Usage accounting for narration calls (Phase-6).

Tracks per-project usage in daily/monthly buckets.
"""

from typing import Dict, Any
from datetime import datetime, timezone
import os
import logging

from apis.infrastructure.storage_service import storage_service

logger = logging.getLogger(__name__)


def _number(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class UsageService:
    def __init__(self):
        self.storage_service = storage_service
        self.container_name = 'usage'

    def record_narration_usage(self, project_id: str, period: str, input_tokens: int,
                               output_tokens: int, cache_hit: bool = False,
                               user_id: str = None) -> Dict[str, Any]:
        """Upsert usage document for a project-period bucket."""
        doc_id = f"usage:{project_id}:{period}"
        existing = None
        try:
            existing = self.storage_service.get_document(self.container_name, doc_id, project_id)
        except Exception:
            existing = None

        narration_calls = _number(existing.get('narration_calls') if existing else 0) + 1
        input_total = _number(existing.get('input_tokens') if existing else 0) + _number(input_tokens)
        output_total = _number(existing.get('output_tokens') if existing else 0) + _number(output_tokens)
        cache_hits = _number(existing.get('cache_hits') if existing else 0) + (1 if cache_hit else 0)
        cache_rate = (cache_hits / narration_calls) if narration_calls else 0.0

        # Track per-user contribution within this project-period bucket
        per_user = existing.get('per_user', {}) if existing else {}
        if user_id:
            user_entry = per_user.get(user_id, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            user_entry["calls"] = user_entry.get("calls", 0) + 1
            user_entry["input_tokens"] = user_entry.get("input_tokens", 0) + input_tokens
            user_entry["output_tokens"] = user_entry.get("output_tokens", 0) + output_tokens
            per_user[user_id] = user_entry

        data = {
            "id": doc_id,
            "projectId": project_id,
            "project_id": project_id,
            "period": period,
            "narration_calls": narration_calls,
            "input_tokens": input_total,
            "output_tokens": output_total,
            "estimated_cost": self._estimate_cost_usd(input_total, output_total),
            "cache_hits": cache_hits,
            "cache_hit_rate": cache_rate,
            "per_user": per_user,
            "last_user_id": user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            return self.storage_service.update_document(self.container_name, doc_id, project_id, data)
        except Exception:
            logger.warning("Failed to record usage")
            return data

    def get_usage(self, project_id: str, period: str) -> Dict[str, Any]:
        doc_id = f"usage:{project_id}:{period}"
        return self.storage_service.get_document(self.container_name, doc_id, project_id) or {}

    @staticmethod
    def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
        in_rate = float(os.getenv("NARRATION_COST_PER_1K_INPUT", "0"))
        out_rate = float(os.getenv("NARRATION_COST_PER_1K_OUTPUT", "0"))
        return round((input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate, 4)


_usage_service = None


def get_usage_service() -> UsageService:
    global _usage_service
    if _usage_service is None:
        _usage_service = UsageService()
    return _usage_service
