# Logging standard

How we log across the Saramsa backend (Django/Celery) and frontend (Next.js).
The goal: every log line is **professional, consistent, correlatable, and safe**.

## 1. Levels — strict semantics

| Level | Use for | Notes |
|---|---|---|
| `DEBUG` | Developer detail | Off in production. Never business-critical info. |
| `INFO` | Meaningful lifecycle events | Sparse. "Analysis started", "User registered". |
| `WARNING` | Handled / recoverable anomaly | A fallback was used, a retry happened, a soft limit hit. |
| `ERROR` | A real failure needing attention | **Always** `logger.exception(...)` (captures the traceback). |
| `CRITICAL` | System-level failure | Can't reach Postgres/Redis, etc. Rare. |

- Never emit `DEBUG`-grade detail at `INFO`.
- An `except` block that logs a failure uses `logger.exception("Failed to ...")`, not `logger.error(f"...: {e}")` (the latter drops the stack trace).
- Never `except: pass` in pipeline / auth / billing paths — at minimum log it.

## 2. Wording — neutral and factual

- **No emojis or icons. No exclamation marks. No hype words** ("successfully", "done!", "great").
- **Sentence case, no trailing period.** `Analysis completed`, not `analysis completed.`.
- **Event first; identifiers as structured fields** (`extra={}`), not interpolated prose.
- Consistent verbs per phase:

| Phase | Wording | Example |
|---|---|---|
| Start | `Starting <operation>` | `Starting feedback analysis` |
| Success | `<Subject> <past-tense verb>` | `Analysis completed`, `Email sent` |
| Skip / no-op | `Skipping <operation>: <reason>` | `Skipping re-delivered task: already complete` |
| Retry | `Retrying <operation> (attempt N)` | `Retrying LLM classification (attempt 2)` |
| Warning / fallback | `<condition>; <action taken>` | `Taxonomy regeneration failed; using cached taxonomy` |
| Failure | `Failed to <action>` (+ `logger.exception`) | `Failed to save analysis` |

### Examples

```python
# Good
logger.info("Analysis completed", extra={"analysis_id": aid, "duration_ms": ms})
logger.warning("No active organization; using user-keyed quota fallback", extra={"user_id": uid})
try:
    save(...)
except Exception:
    logger.exception("Failed to save analysis", extra={"analysis_id": aid})

# Bad
logger.info(f"✅ Local ML pipeline completed in {t:.2f}s")     # emoji + prose + interpolation
logger.error(f"Error saving: {e}")                             # no traceback
logger.info(f"🔍 DEBUG: insight id {id}")                       # DEBUG-grade at INFO
```

## 3. Correlation + context (automatic)

Every backend log record is automatically stamped by `CorrelationIdFilter`
(`apis/core/request_context.py`) with:

| Field | Meaning | Source |
|---|---|---|
| `correlation_id` | request id (web) or Celery task id | middleware / `task_prerun` |
| `analysis_id` | the analysis being processed | analysis task |
| `user_id` | acting user | JWT auth / analysis task |
| `organization_id` | tenant | JWT auth |
| `environment` | production / staging / development | `ENVIRONMENT` env var |
| `release` | deployed git SHA | `RELEASE` env var (set by deploy) |
| `trace_id` / `span_id` | distributed-trace link | active OpenTelemetry span |

You do **not** pass these manually — add only the *event-specific* fields via
`extra={}` (e.g. `duration_ms`, `comment_count`). The text formatter shows
`[correlation_id]`; all fields are available on the record.

**Deploy must set** `ENVIRONMENT` and `RELEASE` (git SHA) as app settings on
each App Service so `environment` and `release` aren't `unknown`.

## 4. Never log

- Secrets / tokens / API keys / passwords.
- PII: emails, full user objects, request bodies.
- Full payloads / model output / comment **content** (log counts, not content).

## 5. Destinations

- Logs go to stdout + rotating files **and** are exported via OpenTelemetry to
  **Azure Application Insights** (the durable, queryable store; files are
  ephemeral on App Service). Both API and Celery export to the same resource;
  they're distinguished by `cloud_RoleName` (service name).
- Per environment: use a **separate App Insights resource** (set the env's
  `APPLICATIONINSIGHTS_CONNECTION_STRING`), not one shared resource.

## 6. Frontend

- Use a small logger wrapper, not raw `console.*`; production builds strip debug
  logs. Never dump Redux state / payloads / PII to the browser console.
- Surface user-facing failures (toast/banner); don't swallow API errors silently.
- Client error tracking (e.g. Sentry) is the destination for uncaught errors —
  separate from this backend standard.
