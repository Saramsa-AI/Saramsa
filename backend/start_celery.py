"""
Startup script for Celery worker + Celery Ops on Azure App Service.
Runs the Celery worker in a subprocess and Celery Ops (FastAPI) on port 8000
to serve both the monitoring UI/API and Azure's health probes.
"""
import subprocess
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("start_celery")

workdir = os.path.dirname(os.path.abspath(__file__))

# Add celery_ops to sys.path (avoids pip install delay and preserves UI build files)
celery_ops_dir = os.path.join(workdir, "celery_ops")
if os.path.isdir(celery_ops_dir) and celery_ops_dir not in sys.path:
    sys.path.insert(0, celery_ops_dir)
    logger.info("Added celery_ops to sys.path: %s", celery_ops_dir)

# Initialize OpenTelemetry for the celery_ops uvicorn process. The worker and
# beat subprocesses initialize their own pipelines via Celery signals.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apis.settings")
sys.path.insert(0, workdir)
try:
    from apis.infrastructure.otel import setup_otel
    setup_otel()
except Exception as e:
    logger.warning("OpenTelemetry setup failed in celery_ops process: %s", e)

# Start Celery worker as a subprocess.
#
# Concurrency = 1: each worker fork loads its own ~2.2 GB DeBERTa NLI model
# (prefork pool, no shared memory). On a 16 GB App Service plan, two forks
# OOM-SIGKILL under load (verified in prod logs 2026-05-30). Concurrency=1
# fits one full ML task in 4-6 GB peak. Scale OUT (multiple instances)
# rather than UP when we need genuine parallelism.
#
# max-tasks-per-child=10: recycle the worker process every 10 tasks to
# bound the model/Python memory leak we measured (RAM baseline grew from
# 4 GB → 10 GB across a day of tasks before this guard).
#
# prefetch-multiplier=1: only pull 1 task per worker from Redis at a time.
# Default (4) caused tasks to sit in worker-local queues even while other
# workers were idle, blocking the "scale out by adding instances" plan.
celery_args = [
    sys.executable, "-m", "celery", "-A", "apis", "worker",
    "--loglevel=info",
    "--concurrency=1",
    "--max-tasks-per-child=10",
    "--prefetch-multiplier=1",
    "-E",
]
logger.info("Starting Celery worker: %s", " ".join(celery_args[1:]))
celery_proc = subprocess.Popen(celery_args, cwd=workdir)

# Start Celery beat scheduler
logger.info("Starting Celery beat...")
celery_beat_proc = subprocess.Popen(
    [sys.executable, "-m", "celery", "-A", "apis", "beat", "--loglevel=info"],
    cwd=workdir
)

# Start Celery Ops on port 8000 (Azure's expected port)
try:
    from celery_ops.cli import _load_celery_app
    from celery_ops.app import create_app
    from celery_ops.config import OpsConfig
    import uvicorn

    os.chdir(workdir)
    celery_app = _load_celery_app("apis")

    config = OpsConfig.from_env(celery_app="apis")
    config.host = "0.0.0.0"
    config.port = 8000
    config.ui_enabled = True
    config.store = "memory"
    config.events_enabled = True

    api = create_app(celery_app, config)

    logger.info("Starting Celery Ops on port 8000...")
    uvicorn.run(api, host="0.0.0.0", port=8000, log_level="info")

except Exception as e:
    logger.error("Failed to start Celery Ops: %s. Falling back to simple health server.", e)

    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = "running" if celery_proc.poll() is None else "stopped"
            code = 200 if status == "running" else 503
            self.send_response(code)
            self.end_headers()
            self.wfile.write(f"Celery worker {status}".encode())

        def log_message(self, format, *args):
            pass

    logger.info("Starting fallback health server on port 8000...")
    HTTPServer(("0.0.0.0", 8000), HealthHandler).serve_forever()
