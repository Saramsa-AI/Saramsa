import os
import ssl
import sys
import logging
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, beat_init, setup_logging, task_prerun, task_postrun

from .otel import setup_otel


@task_prerun.connect
def _bind_task_log_context(task_id=None, **_kwargs):
    """Bind the Celery task id to the logging context so worker logs carry a
    correlation id (the analysis task additionally sets analysis_id/user/tenant)."""
    try:
        from apis.core.request_context import task_id_var
        task_id_var.set(task_id)
    except Exception:
        pass


@task_postrun.connect
def _clear_task_log_context(**_kwargs):
    try:
        from apis.core.request_context import task_id_var, analysis_id_var, user_id_var, organization_id_var
        for var in (task_id_var, analysis_id_var, user_id_var, organization_id_var):
            var.set(None)
    except Exception:
        pass


@setup_logging.connect
def _configure_celery_logging(**_kwargs):
    """Apply Django's LOGGING dict to the celery worker.

    Celery normally hijacks Python's root logger when a worker starts —
    that silently overrides Django's dictConfig and all our per-logger
    handlers (e.g. `apis.pipeline` → celery.log via
    ConcurrentRotatingFileHandler). The result is task processing that
    runs invisibly while logs are routed nowhere we look.

    By connecting to the `setup_logging` signal we take over logging
    configuration ourselves: Celery sees a handler is registered and
    skips its own hijack, leaving Django's LOGGING dict authoritative
    for both the backend and the worker. Phase/heartbeat lines from the
    worker now land in the same celery.log file the backend writes to.
    """
    from logging.config import dictConfig
    from django.conf import settings
    dictConfig(settings.LOGGING)

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apis.settings')

app = Celery('saramsa')

# Windows compatibility: Use 'solo' pool on Windows (single process, no forking)
# On Unix/Linux, use default 'prefork' pool for better performance
logger = logging.getLogger(__name__)
if sys.platform == 'win32':
    app.conf.worker_pool = 'solo'
    logger.info("Running on Windows - using 'solo' worker pool")

# Get URLs from environment BEFORE Django settings (Azure Redis)
broker_url = os.getenv('CELERY_BROKER_URL')
result_backend = os.getenv('CELERY_RESULT_BACKEND')

# Add SSL certificate requirements to Redis URLs for Azure Redis
if broker_url and broker_url.startswith('rediss://'):
    # Append ssl_cert_reqs parameter to URL if not already present
    if 'ssl_cert_reqs' not in broker_url:
        separator = '&' if '?' in broker_url else '?'
        # Use CERT_NONE as the value (required by Celery Redis backend)
        broker_url = f"{broker_url}{separator}ssl_cert_reqs=CERT_NONE"
        # Update environment variable for settings.py
        os.environ['CELERY_BROKER_URL'] = broker_url
    app.conf.broker_url = broker_url
    app.conf.broker_transport_options = {'ssl_cert_reqs': ssl.CERT_NONE}
    
if result_backend and result_backend.startswith('rediss://'):
    # Append ssl_cert_reqs parameter to URL if not already present
    if 'ssl_cert_reqs' not in result_backend:
        separator = '&' if '?' in result_backend else '?'
        # Use CERT_NONE as the value (required by Celery Redis backend)
        result_backend = f"{result_backend}{separator}ssl_cert_reqs=CERT_NONE"
        # Update environment variable for settings.py
        os.environ['CELERY_RESULT_BACKEND'] = result_backend
    app.conf.result_backend = result_backend
    app.conf.result_backend_transport_options = {'ssl_cert_reqs': ssl.CERT_NONE}

app.config_from_object('django.conf:settings', namespace='CELERY')

# Suppress deprecation warning about broker_connection_retry
app.conf.broker_connection_retry_on_startup = True

# Belt-and-suspenders: prevent celery's root-logger hijack even if the
# setup_logging signal handler above somehow doesn't fire (e.g., import
# order edge cases). Together they guarantee Django's LOGGING dict is the
# single source of truth for both backend and worker.
app.conf.worker_hijack_root_logger = False

# Re-apply Windows pool setting after config loading (in case settings.py overrides it)
if sys.platform == 'win32':
    app.conf.worker_pool = 'solo'

# Force re-set URLs and SSL options after config loading to ensure they persist
if broker_url and broker_url.startswith('rediss://'):
    # Re-read from environment in case settings.py modified it
    updated_broker_url = os.getenv('CELERY_BROKER_URL', broker_url)
    if 'ssl_cert_reqs' not in updated_broker_url:
        separator = '&' if '?' in updated_broker_url else '?'
        updated_broker_url = f"{updated_broker_url}{separator}ssl_cert_reqs=CERT_NONE"
    app.conf.broker_url = updated_broker_url
    app.conf.broker_transport_options = {'ssl_cert_reqs': ssl.CERT_NONE}
    
if result_backend and result_backend.startswith('rediss://'):
    # Re-read from environment in case settings.py modified it
    updated_result_backend = os.getenv('CELERY_RESULT_BACKEND', result_backend)
    if 'ssl_cert_reqs' not in updated_result_backend:
        separator = '&' if '?' in updated_result_backend else '?'
        updated_result_backend = f"{updated_result_backend}{separator}ssl_cert_reqs=CERT_NONE"
    app.conf.result_backend = updated_result_backend
    app.conf.result_backend_transport_options = {'ssl_cert_reqs': ssl.CERT_NONE}

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Ensure events are sent so Celery Ops / Flower can observe runs
app.conf.worker_send_task_events = True
app.conf.task_send_sent_event = True

# Task timeout configuration to prevent hung tasks from blocking the queue forever
# With concurrency=1, one stuck task blocks all other tasks indefinitely.
# Soft limit (1800s / 30min): Send SIGTERM, task can cleanup gracefully
# Hard limit (2100s / 35min): Send SIGKILL, forcefully terminate the task
# Large analysis tasks (150+ comments) take 23-24 minutes; these limits catch genuinely hung tasks
# while allowing normal processing to complete.
app.conf.task_soft_time_limit = 1800  # 30 minutes soft timeout
app.conf.task_time_limit = 2100  # 35 minutes hard timeout

# Task result expiration - clean up old task results from Redis after 24 hours
# Prevents Redis from filling up with stale task metadata
app.conf.result_expires = 86400  # 24 hours


# Initialize OpenTelemetry inside each forked worker process — prefork workers
# inherit the parent's memory but BatchSpanProcessor's background threads don't
# survive fork, so each child must build its own pipeline.
@worker_process_init.connect
def _init_otel_worker(**_kwargs):
    setup_otel()


@beat_init.connect
def _init_otel_beat(**_kwargs):
    setup_otel()

# Scheduled ingestion task disabled for now.
# To re-enable later, restore the beat entry below.
app.conf.beat_schedule = {
    "unsnooze-candidates": {
        "task": "unsnooze_expired_candidates",
        "schedule": crontab(minute=0, hour=9),
    },
    "weekly-digest-email": {
        "task": "send_weekly_digest",
        "schedule": crontab(minute=0, hour=9, day_of_week=1),  # Every Monday 9 AM UTC
    },
    "cleanup-expired-invites": {
        # Mark pending OrganizationInvite rows past their expires_at as
        # 'expired' so the table doesn't grow unbounded. The service layer
        # already rejects expired invites at lookup-time, so this is hygiene
        # rather than a correctness fix. Runs at 02:00 UTC daily — picked a
        # low-traffic window so the UPDATE doesn't compete with user reqs.
        "task": "cleanup_expired_invites",
        "schedule": crontab(minute=0, hour=2),
    },
}
# app.conf.beat_schedule = {
#     "run-scheduled-ingestions": {
#         "task": "feedback_analysis.run_scheduled_ingestions",
#         "schedule": 900.0,
#     }
# }

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    logger.debug(f'Request: {self.request!r}')
