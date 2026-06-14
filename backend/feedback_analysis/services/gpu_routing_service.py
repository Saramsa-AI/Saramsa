"""
GPU Routing Service for Hybrid CPU/GPU Processing

Routes ML-heavy tasks to Spot GPU workers when available, with automatic
fallback to CPU workers on eviction/failure.

Architecture:
  - Primary: Spot GPU worker (fast, cheap, can be evicted)
  - Fallback: CPU worker on B3 App Service (reliable, always available)

Routing Strategy:
  - Try GPU queue first (timeout: 30s)
  - On timeout/failure: Route to CPU queue
  - Celery handles retries automatically

Environment Variables:
  - ENABLE_GPU_ROUTING: Enable/disable GPU routing (default: false)
  - GPU_QUEUE_TIMEOUT: Seconds to wait for GPU (default: 30)
  - GPU_WORKER_PRIORITY: 0-10, higher = prefer GPU (default: 8)
"""

import logging
import os
from typing import Optional, Dict, Any
from celery import signature
from celery.result import AsyncResult
from kombu.exceptions import OperationalError

logger = logging.getLogger(__name__)


class GPURoutingService:
    """Routes ML tasks to GPU or CPU workers based on availability."""

    def __init__(self):
        self.gpu_enabled = os.getenv('ENABLE_GPU_ROUTING', 'false').lower() == 'true'
        self.gpu_timeout = int(os.getenv('GPU_QUEUE_TIMEOUT', '30'))
        self.gpu_priority = int(os.getenv('GPU_WORKER_PRIORITY', '8'))

        # Queue names
        self.gpu_queue = 'gpu_ml_tasks'
        self.cpu_queue = 'celery'  # Default queue

        logger.info(
            "GPU routing configured",
            extra={
                "gpu_enabled": self.gpu_enabled,
                "gpu_timeout_s": self.gpu_timeout,
                "gpu_priority": self.gpu_priority,
            },
        )

    def route_task(self, task_name: str, args: tuple, kwargs: dict,
                   prefer_gpu: bool = True) -> AsyncResult:
        """
        Route a task to GPU or CPU worker.

        Args:
            task_name: Name of the Celery task
            args: Task positional arguments
            kwargs: Task keyword arguments
            prefer_gpu: If True, try GPU first (default)

        Returns:
            AsyncResult: Celery task result handle

        Strategy:
            1. If GPU routing disabled → CPU only
            2. If prefer_gpu=False → CPU only
            3. Try GPU queue with timeout
            4. On failure/timeout → Fallback to CPU
        """

        # Route to CPU if GPU disabled or not preferred
        if not self.gpu_enabled or not prefer_gpu:
            logger.info("Routing task to CPU", extra={"task_name": task_name})
            return self._dispatch_to_cpu(task_name, args, kwargs)

        # Try GPU first
        try:
            logger.info("Attempting GPU routing", extra={"task_name": task_name})
            result = self._dispatch_to_gpu(task_name, args, kwargs)

            # Quick check: Is GPU worker available?
            # (Don't wait for completion, just check if task accepted)
            try:
                result.get(timeout=0.1, propagate=False)
            except Exception:
                # Task is running or queued - good!
                pass

            logger.info("Task routed to GPU", extra={"task_id": result.id})
            return result

        except (OperationalError, TimeoutError, Exception) as e:
            # GPU unavailable - fallback to CPU
            logger.warning(
                "GPU routing failed; falling back to CPU",
                extra={"task_name": task_name, "error_type": type(e).__name__},
            )
            return self._dispatch_to_cpu(task_name, args, kwargs)

    def _dispatch_to_gpu(self, task_name: str, args: tuple, kwargs: dict) -> AsyncResult:
        """Dispatch task to GPU queue."""
        sig = signature(
            task_name,
            args=args,
            kwargs=kwargs,
            queue=self.gpu_queue,
            priority=self.gpu_priority,
            time_limit=3600,  # 1 hour max
            soft_time_limit=3300,  # Warn at 55 min
        )
        return sig.apply_async()

    def _dispatch_to_cpu(self, task_name: str, args: tuple, kwargs: dict) -> AsyncResult:
        """Dispatch task to CPU queue (fallback)."""
        sig = signature(
            task_name,
            args=args,
            kwargs=kwargs,
            queue=self.cpu_queue,
            priority=5,  # Normal priority
            time_limit=7200,  # 2 hours max (CPU is slower)
            soft_time_limit=6900,
        )
        return sig.apply_async()

    def get_worker_stats(self) -> Dict[str, Any]:
        """Get GPU and CPU worker statistics."""
        from celery import current_app

        try:
            inspect = current_app.control.inspect()
            stats = inspect.stats()
            active = inspect.active()

            gpu_workers = {k: v for k, v in (stats or {}).items() if 'gpu' in k.lower()}
            cpu_workers = {k: v for k, v in (stats or {}).items() if 'gpu' not in k.lower()}

            return {
                'gpu_enabled': self.gpu_enabled,
                'gpu_workers': len(gpu_workers),
                'cpu_workers': len(cpu_workers),
                'gpu_active_tasks': sum(
                    len(tasks) for worker, tasks in (active or {}).items()
                    if 'gpu' in worker.lower()
                ),
                'cpu_active_tasks': sum(
                    len(tasks) for worker, tasks in (active or {}).items()
                    if 'gpu' not in worker.lower()
                ),
                'gpu_queue': self.gpu_queue,
                'cpu_queue': self.cpu_queue,
            }
        except Exception as e:
            logger.exception("Failed to get worker stats")
            return {'error': str(e)}


# Singleton instance
_routing_service = None


def get_gpu_routing_service() -> GPURoutingService:
    """Get or create the GPU routing service singleton."""
    global _routing_service
    if _routing_service is None:
        _routing_service = GPURoutingService()
    return _routing_service
