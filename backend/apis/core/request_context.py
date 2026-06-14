import logging
import os
from contextvars import ContextVar
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
token_usage_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar('token_usage', default=None)
task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
analysis_id_var: ContextVar[Optional[str]] = ContextVar('analysis_id', default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar('user_id', default=None)
organization_id_var: ContextVar[Optional[str]] = ContextVar('organization_id', default=None)

# environment/release come from deploy-set app settings; "unknown" locally.
_ENVIRONMENT = os.getenv('ENVIRONMENT') or ('development' if os.getenv('DEBUG', '').lower() == 'true' else 'production')
_RELEASE = os.getenv('RELEASE') or os.getenv('RELEASE_SHA') or os.getenv('GIT_COMMIT') or 'unknown'


def set_request_identity(user_id: Optional[str], organization_id: Optional[str] = None) -> None:
    """Bind the current user + tenant to the logging context (called from auth)."""
    if user_id is not None:
        user_id_var.set(str(user_id))
    if organization_id is not None:
        organization_id_var.set(str(organization_id))


def _otel_trace_ids():
    """Return (trace_id, span_id) hex strings from the active OTel span, or ('-','-')."""
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if getattr(ctx, "trace_id", 0):
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return "-", "-"


class CorrelationIdFilter(logging.Filter):
    """Stamp correlation/context identifiers onto every LogRecord (default ``-``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = request_id_var.get() or task_id_var.get() or "-"
        record.analysis_id = analysis_id_var.get() or "-"
        record.user_id = user_id_var.get() or "-"
        record.organization_id = organization_id_var.get() or "-"
        record.environment = _ENVIRONMENT
        record.release = _RELEASE
        record.trace_id, record.span_id = _otel_trace_ids()
        return True