"""
CLI for Celery Ops.

Usage: celery-ops serve -A apis [--host 0.0.0.0] [--port 9800] [--no-ui] [--store memory|sqlite|redis]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import create_app
from .config import OpsConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("celery_ops")


def _load_celery_app(app_spec: str):
    """Load Celery app from -A spec (e.g. apis or apis.infrastructure.celery:app)."""
    import importlib

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    if ":" in app_spec:
        mod_path, attr = app_spec.rsplit(":", 1)
        mod = importlib.import_module(mod_path)
        app = getattr(mod, attr)
    else:
        mod = importlib.import_module(app_spec)
        app = getattr(mod, "celery_app", None) or getattr(mod, "app", None)
        if app is None:
            raise ValueError(
                f"Module {app_spec!r} has no 'celery_app' or 'app'. "
                f"Use -A {app_spec}.infrastructure.celery:app or similar."
            )
    return app


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Celery Ops — ops-only observer and lightweight controller for Celery")
    p.add_argument("cmd", nargs="?", default="serve", help="Command (default: serve)")
    p.add_argument("-A", "--app", default="", help="Celery app instance (e.g. apis or apis.infrastructure.celery:app)")
    p.add_argument("--host", default="0.0.0.0", help="Bind host")
    p.add_argument("--port", type=int, default=9800, help="Bind port")
    p.add_argument("--no-ui", action="store_true", help="Disable web UI")
    p.add_argument("--no-events", action="store_true", help="Disable event consumer (use inspect only)")
    p.add_argument("--store", choices=("memory", "sqlite", "redis"), default="memory", help="Ops store (default: memory)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.cmd != "serve":
        logger.error("Unknown command; use: celery-ops serve -A apis")
        sys.exit(1)
    if not args.app:
        logger.error("Celery app required; use: celery-ops serve -A apis")
        sys.exit(1)

    celery_app = _load_celery_app(args.app)

    config = OpsConfig.from_env(celery_app=args.app)
    config.host = args.host
    config.port = args.port
    config.ui_enabled = not args.no_ui
    config.store = args.store
    config.events_enabled = not args.no_events

    api = create_app(celery_app, config)

    import uvicorn
    logger.info("Celery Ops listening on %s:%d (ui=%s)", config.host, config.port, config.ui_enabled)
    uvicorn.run(api, host=config.host, port=config.port, log_level="info")
