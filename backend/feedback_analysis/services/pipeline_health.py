"""
Pipeline health tracking for Phase-4.

Tracks per-stage latencies, status, and failure modes.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class PipelineHealth:
    """Mutable health snapshot for a single analysis run."""

    def __init__(self, analysis_id: str, task_id: Optional[str] = None):
        self.analysis_id = analysis_id
        self.task_id = task_id
        self.status = "RUNNING"
        self.degraded = False
        self.degraded_mode = None
        self.stage_latencies: Dict[str, float] = {}
        self.stage_started: Dict[str, datetime] = {}
        self.errors: Dict[str, str] = {}
        self.started_at = datetime.now(timezone.utc)
        self.updated_at = self.started_at
        self.cost = {}

    def start_stage(self, name: str) -> None:
        self.stage_started[name] = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def end_stage(self, name: str) -> None:
        started = self.stage_started.get(name)
        if started:
            delta = datetime.now(timezone.utc) - started
            self.stage_latencies[name] = delta.total_seconds()
        self.updated_at = datetime.now(timezone.utc)

    def mark_partial(self, reason: str, key: str = "partial") -> None:
        self.status = "PARTIAL"
        self.degraded = True
        self.errors.setdefault(key, reason)
        self.updated_at = datetime.now(timezone.utc)

    def mark_degraded(self, mode: str, reason: str) -> None:
        self.status = "DEGRADED"
        self.degraded = True
        self.degraded_mode = mode
        self.errors.setdefault("budget", reason)
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, reason: str) -> None:
        self.status = "FAILED"
        self.errors.setdefault("deterministic", reason)
        self.updated_at = datetime.now(timezone.utc)

    def mark_complete(self) -> None:
        if self.status not in ("FAILED", "PARTIAL"):
            self.status = "COMPLETE"
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "task_id": self.task_id,
            "status": self.status,
            "degraded": self.degraded,
            "degraded_mode": self.degraded_mode,
            "stage_latencies": self.stage_latencies,
            "errors": self.errors,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "cost": self.cost,
        }
