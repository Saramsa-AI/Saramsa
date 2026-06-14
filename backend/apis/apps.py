import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ApisConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apis'
    verbose_name = 'Core API Services'

    def ready(self):
        """App startup — heavy probes are deferred to first request."""
        pass  # Redis connectivity is verified on first cache access

    # ------------------------------------------------------------------
    @staticmethod
    def _log_redis_status():
        """Probe Redis / cache and log the result at startup."""
        try:
            from apis.infrastructure.cache_service import get_cache_service
            cache = get_cache_service()
            health = cache.health_check()
            status = health.get('status', 'unknown')
            backend = health.get('backend', 'unknown')

            if backend == 'redis':
                host = health.get('host', '?')
                port = health.get('port', '?')
                latency = health.get('latency_ms', '?')
                version = health.get('redis_version', '?')
                memory = health.get('used_memory', '?')
                rw = health.get('read_write_test', '?')
                logger.info(
                    "Redis [%s] %s:%s  v%s  latency=%sms  memory=%s  r/w=%s",
                    status.upper(), host, port, version, latency, memory, rw,
                )
            else:
                logger.info("Cache backend: in-memory (status=%s)", status)
        except Exception:
            logger.exception("Redis startup probe failed")