"""
OpenTelemetry + Azure Application Insights setup.

Configures three signal pipelines when a valid App Insights connection string
is present:

  1. **Traces** — Django, Psycopg2, Requests auto-instrumentation + custom
     GenAI spans created by ``usage_logging.log_token_usage``.
  2. **Metrics** — GenAI counters (token usage, LLM call count) and histograms
     (LLM latency) with ``user_id / project_id / task_type`` dimensions.
  3. **Logs** — Python ``logging`` records forwarded to App Insights so
     structured token-usage entries appear as ``traces`` / ``customEvents``.

All three are optional — if the connection string is missing or placeholder
the app runs without telemetry.
"""

import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Module-level handles populated by setup_otel() ──
# Other modules (e.g. usage_logging) import these and check for None before
# recording, so the app never fails when OTel is disabled.
_tracer = None
_meter = None

# Metrics instruments (created once in setup_otel, used by usage_logging)
llm_token_counter = None       # Counter: cumulative tokens
llm_call_counter = None        # Counter: number of LLM calls
llm_cost_counter = None        # Counter: estimated cost in USD
llm_latency_histogram = None   # Histogram: LLM call duration in ms


def get_tracer():
    """Return the app tracer, or None if OTel is not configured."""
    return _tracer


def get_meter():
    """Return the app meter, or None if OTel is not configured."""
    return _meter


def setup_otel():
    """Setup OpenTelemetry with Azure Application Insights.

    Safe to call at startup — silently skips when the connection string is
    absent or any OTel package is missing.
    """
    global _tracer, _meter
    global llm_token_counter, llm_call_counter, llm_cost_counter, llm_latency_histogram

    connection_string = os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')

    if not connection_string or connection_string.endswith('00000000-0000-0000-0000-000000000000'):
        logger.info("OpenTelemetry setup skipped - no valid Application Insights connection string")
        logger.info("To enable, set APPLICATIONINSIGHTS_CONNECTION_STRING in environment")
        return

    try:
        # ── Core imports ──
        try:
            from opentelemetry import trace, metrics
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from azure.monitor.opentelemetry.exporter import (
                AzureMonitorTraceExporter,
                AzureMonitorMetricExporter,
                AzureMonitorLogExporter,
            )
        except ImportError as e:
            logger.warning(f"OpenTelemetry packages not available: {e}")
            logger.info("Continuing without OpenTelemetry instrumentation")
            return

        # ── Resource (shared across all signals) ──
        resource = Resource.create({
            "service.name": os.getenv('WEBSITE_SITE_NAME', 'saramsa-backend'),
            "service.instance.id": os.getenv('HOSTNAME', 'localhost'),
            "service.version": os.getenv('APP_VERSION', '1.0.0'),
            "deployment.environment": os.getenv('ENVIRONMENT', 'development'),
        })

        # ── 1. Traces ──
        tracer_provider = TracerProvider(resource=resource)
        trace_exporter = AzureMonitorTraceExporter.from_connection_string(connection_string)
        tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("saramsa.genai", "1.0.0")
        logger.info("Traces pipeline configured")

        # ── 2. Metrics ──
        try:
            metric_exporter = AzureMonitorMetricExporter.from_connection_string(connection_string)
            metric_reader = PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=60_000,  # flush every 60s
            )
            meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
            metrics.set_meter_provider(meter_provider)
            _meter = metrics.get_meter("saramsa.genai", "1.0.0")

            # Create instruments
            llm_token_counter = _meter.create_counter(
                name="genai.token.usage",
                description="Cumulative token usage across all GenAI calls",
                unit="token",
            )
            llm_call_counter = _meter.create_counter(
                name="genai.calls",
                description="Number of GenAI LLM calls",
                unit="call",
            )
            llm_cost_counter = _meter.create_counter(
                name="genai.cost",
                description="Estimated cost of GenAI calls",
                unit="USD",
            )
            llm_latency_histogram = _meter.create_histogram(
                name="genai.latency",
                description="LLM call duration",
                unit="ms",
            )
            logger.info("Metrics pipeline configured (4 instruments)")
        except Exception as e:
            logger.warning(f"Metrics pipeline setup failed: {e}")

        # ── 3. Logs ──
        try:
            log_exporter = AzureMonitorLogExporter.from_connection_string(connection_string)
            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

            # Attach OTel handler so logger.info() etc. flow to App Insights as
            # traces-table entries, stamped with the correlation/context fields.
            otel_handler = LoggingHandler(
                level=logging.INFO,
                logger_provider=logger_provider,
            )
            try:
                from apis.core.request_context import CorrelationIdFilter
                otel_handler.addFilter(CorrelationIdFilter())
            except Exception:
                pass
            logging.getLogger().addHandler(otel_handler)
            # App loggers are configured with propagate=False (they have their own
            # console/file handlers), so they never reach the root handler above.
            # Attach the exporter to them directly so their records — the bulk of
            # our logs — also reach App Insights. propagate=False means no record
            # double-exports (it won't also bubble to root).
            for _lg in list(logging.root.manager.loggerDict.values()):
                if isinstance(_lg, logging.Logger) and not _lg.propagate and _lg.handlers:
                    _lg.addHandler(otel_handler)

            # Break the OTel feedback loop: the Azure SDK and exporter log every
            # outbound HTTP call at INFO level. Those logs would flow through the
            # handler above, ship to App Insights, generate more SDK logs, and
            # peg CPU. Silence them at the source. Without this, celery workers
            # spend 100% CPU shipping noise about their own shipping and die.
            for noisy in (
                "azure.core.pipeline.policies.http_logging_policy",
                "azure.monitor.opentelemetry.exporter.export._base",
                "urllib3.connectionpool",
            ):
                logging.getLogger(noisy).setLevel(logging.WARNING)

            logger.info("Logs pipeline configured (root logger -> App Insights)")
        except Exception as e:
            logger.warning(f"Logs pipeline setup failed: {e}")

        # ── Auto-instrumentation ──
        _instrument_django()
        _instrument_psycopg2()
        _instrument_requests()
        _instrument_celery()

        logger.info("OpenTelemetry setup completed — traces, metrics, and logs enabled")

    except Exception as e:
        logger.error(f"Failed to setup OpenTelemetry: {e}")
        logger.info("Continuing without OpenTelemetry — basic logging is still active")


# ── Auto-instrumentors (isolated so one failure doesn't block others) ──

def _instrument_django():
    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        DjangoInstrumentor().instrument()
        logger.info("Django instrumentation enabled")
    except Exception as e:
        logger.warning(f"Django instrumentation failed: {e}")


def _instrument_psycopg2():
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        Psycopg2Instrumentor().instrument(enable_commenter=False)
        logger.info("Psycopg2 instrumentation enabled")
    except Exception as e:
        logger.warning(f"Psycopg2 instrumentation failed: {e}")


def _instrument_requests():
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
        logger.info("Requests instrumentation enabled")
    except Exception as e:
        logger.warning(f"Requests instrumentation failed: {e}")


def _instrument_celery():
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
        logger.info("Celery instrumentation enabled")
    except Exception as e:
        logger.warning(f"Celery instrumentation failed: {e}")
